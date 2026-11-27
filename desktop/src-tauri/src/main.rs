// Sniff desktop shell: a launcher window shown and hidden by a global shortcut, a tray icon, reminders as Windows
// notifications, and the child processes it starts and stops (the FastAPI back end and the embedding llama-server).
// The chat model server is not started here: the back end starts it when a model call needs it and stops it when idle.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::Value;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, PhysicalPosition, RunEvent, WebviewWindow, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

// Alt+Space opens the window menu in Windows and is PowerToys Run's default, so Sniff uses Alt+Shift+Space.
// set_hotkey changes it and saves the new one in data/hotkey.txt.
const SHORTCUT: &str = "Alt+Shift+Space";
// Same port as backend/sniff/desktop.py and frontend/src/api.ts. Not 8000, so a dev back end can run alongside.
const API_PORT: u16 = 8765;
const NO_WINDOW: u32 = 0x0800_0000; // CREATE_NO_WINDOW: no console windows for the child processes
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"; // the uninstaller deletes our "Sniff" value
const NOTIFY_DAYS: i64 = 7; // dates this close get a notification, at most once a day per document

#[derive(Default)]
struct Children(Mutex<Vec<Child>>);

struct Hotkey(Mutex<String>); // the shortcut registered now

// When Windows refuses to hand the focus to the window (it does that to a program that did not get the last key or
// click, for example when a second launch asks the first one to show), the window reports a focus loss right after
// it appears. Focus losses this soon after showing are not clicks elsewhere, so they do not hide it.
static SHOWN_AT: Mutex<Option<Instant>> = Mutex::new(None);

/// Data, models and logs: %LOCALAPPDATA%\Sniff, or SNIFF_HOME if set.
fn home() -> PathBuf {
    match std::env::var_os("SNIFF_HOME") {
        Some(h) => h.into(),
        None => PathBuf::from(std::env::var_os("LOCALAPPDATA").expect("LOCALAPPDATA is not set")).join("Sniff"),
    }
}

fn log(msg: &str) {
    let dir = home().join("logs");
    let _ = fs::create_dir_all(&dir);
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(dir.join("desktop.log")) {
        let _ = writeln!(f, "{msg}");
    }
}

fn port_open(port: u16) -> bool {
    TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], port)), Duration::from_millis(300)).is_ok()
}

/// GET from the back end with a plain HTTP/1.0 request; one call an hour does not need an HTTP client crate.
fn get(path: &str) -> Option<String> {
    let mut s = TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], API_PORT)), Duration::from_secs(2)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(60))).ok()?;
    write!(s, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").ok()?;
    let mut reply = String::new();
    s.read_to_string(&mut reply).ok()?;
    let (head, body) = reply.split_once("\r\n\r\n")?;
    (head.split(' ').nth(1) == Some("200")).then(|| body.to_string())
}

/// A Windows job object that kills its processes when its last handle closes. Sniff holds the only handle, so when
/// Sniff exits in any way, a crash included, the back end and the model servers it started end with it.
fn job() -> usize {
    static JOB: OnceLock<usize> = OnceLock::new();
    *JOB.get_or_init(|| unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let size = std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32;
        if job.is_null() || SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info as *const _ as _, size) == 0 {
            log("could not set up the job object: child processes will outlive a crash");
        }
        job as usize
    })
}

/// Start `exe` with its output in logs/<name>.log, and keep the handle so Quit can stop it.
fn spawn(app: &AppHandle, name: &str, exe: &Path, args: &[&str]) {
    let mut cmd = Command::new(exe);
    cmd.args(args).env("SNIFF_HOME", home()).stdin(Stdio::null()).creation_flags(NO_WINDOW);
    if let Ok(out) = File::create(home().join("logs").join(format!("{name}.log"))) {
        if let Ok(err) = out.try_clone() {
            cmd.stderr(err);
        }
        cmd.stdout(out);
    }
    match cmd.spawn() {
        Ok(child) => {
            let in_job = unsafe { AssignProcessToJobObject(job() as _, child.as_raw_handle() as _) } != 0;
            log(&format!("started {name} (pid {}, in job: {in_job})", child.id()));
            app.state::<Children>().0.lock().unwrap().push(child);
        }
        Err(e) => log(&format!("could not start {name}: {e}")),
    }
}

/// Reuse a model server that already answers on its port. Otherwise start the bundled llama-server, with the
/// flags scripts/dev.ps1 uses, as soon as its model file is on disk (on first run the download screen puts it there).
/// Only the small embedding server runs all the time; the back end owns the chat server (backend/sniff/ai/server.py).
fn model_servers(app: AppHandle, llama: PathBuf) {
    let models = home().join("models");
    let servers = [("embed-server", 8081u16, "all-minilm-l6-v2.gguf", "--embedding -c 2048 -b 2048 -ub 2048")];
    let mut waiting = [true];
    while waiting.contains(&true) {
        for (i, (name, port, model, flags)) in servers.iter().enumerate() {
            if !waiting[i] {
                continue;
            }
            let file = models.join(model);
            if port_open(*port) {
                log(&format!("{name}: reusing the server on port {port}"));
            } else if file.exists() {
                let port = port.to_string();
                let mut args = vec!["-m", file.to_str().unwrap(), "--port", &port];
                args.extend(flags.split(' '));
                spawn(&app, name, &llama, &args);
            } else {
                continue;
            }
            waiting[i] = false;
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// Every hour, ask the back end for dates in the next NOTIFY_DAYS days and show a Windows notification for each,
/// at most once a day per document (remembered in data/notified.json).
fn reminders(app: AppHandle) {
    let file = home().join("data").join("notified.json");
    loop {
        let Some(items) = get(&format!("/upcoming?days={NOTIFY_DAYS}")).and_then(|b| serde_json::from_str::<Vec<Value>>(&b).ok())
        else {
            std::thread::sleep(Duration::from_secs(30)); // the back end is still starting
            continue;
        };
        // shortcut: days are counted in UTC, so "once a day" turns over at 5 am in Pakistan; fine for reminders
        let today = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() / 86_400;
        let mut sent: HashMap<String, u64> = fs::read_to_string(&file).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default();
        for it in &items {
            let (Some(path), Some(days)) = (it["path"].as_str(), it["days_left"].as_i64()) else { continue };
            if days < 0 || sent.get(path) == Some(&today) {
                continue;
            }
            let what = match it["fact"]["field"].as_str().unwrap_or("date") {
                "expiry_date" => "Expires".to_string(),
                "due_date" => "Payment due".to_string(),
                "warranty_until" => "Warranty ends".to_string(),
                other => other.replace('_', " "),
            };
            let when = match days {
                0 => "today".to_string(),
                1 => "tomorrow".to_string(),
                n => format!("in {n} days"),
            };
            let shown = app.notification().builder().title(it["document"].as_str().unwrap_or("Sniff"))
                .body(format!("{what} {when} ({})", it["fact"]["value"].as_str().unwrap_or(""))).show();
            log(&format!("notification for {path}: {what} {when} ({shown:?})"));
            sent.insert(path.to_string(), today);
        }
        let _ = fs::write(&file, serde_json::to_string(&sent).unwrap_or_default());
        std::thread::sleep(Duration::from_secs(3600));
    }
}

/// Centred across the screen, with its middle at a third of the screen height.
fn place(w: &WebviewWindow) {
    let monitor = w.current_monitor().ok().flatten().or_else(|| w.primary_monitor().ok().flatten());
    if let (Some(m), Ok(size)) = (monitor, w.outer_size()) {
        let x = m.position().x + (m.size().width as i32 - size.width as i32) / 2;
        let y = m.position().y + (m.size().height as i32 / 3 - size.height as i32 / 2).max(0);
        let _ = w.set_position(PhysicalPosition::new(x, y));
    }
}

fn show(w: &WebviewWindow) {
    *SHOWN_AT.lock().unwrap() = Some(Instant::now());
    place(w);
    let _ = w.show();
    let _ = w.unminimize();
    let _ = w.set_focus();
}

fn register(app: &AppHandle, hotkey: &str) -> Result<(), String> {
    app.global_shortcut()
        .on_shortcut(hotkey, |app, _, e| {
            if e.state == ShortcutState::Pressed {
                toggle(app);
            }
        })
        .map_err(|e| e.to_string())?;
    *app.state::<Hotkey>().0.lock().unwrap() = hotkey.to_string();
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(format!("Sniff ({hotkey})")));
    }
    Ok(())
}

fn toggle(app: &AppHandle) {
    let Some(w) = app.get_webview_window("main") else { return };
    let (visible, focused) = (w.is_visible().unwrap_or(false), w.is_focused().unwrap_or(false));
    log(&format!("hotkey: visible={visible} focused={focused}"));
    if visible && focused {
        let _ = w.hide();
    } else {
        show(&w);
    }
}

/// Open a file in its default app. A PDF with a page above 1 opens in Microsoft Edge at that page, because the
/// default PDF reader may ignore page numbers (Edge comes with Windows 11 and reads #page=N).
#[tauri::command]
fn open_file(path: String, page: Option<u32>) -> Result<(), String> {
    let file = Path::new(&path);
    if !file.is_file() {
        return Err(format!("File not found: {path}"));
    }
    let edge = PathBuf::from(std::env::var_os("ProgramFiles(x86)").unwrap_or_default()).join(r"Microsoft\Edge\Application\msedge.exe");
    let pdf = file.extension().is_some_and(|e| e.eq_ignore_ascii_case("pdf"));
    let mut cmd = match page {
        Some(n) if n > 1 && pdf && edge.exists() => {
            let mut url = tauri::Url::from_file_path(file).map_err(|_| format!("Not a full path: {path}"))?;
            url.set_fragment(Some(&format!("page={n}")));
            let mut c = Command::new(edge);
            c.arg(url.as_str());
            c
        }
        _ => {
            let mut c = Command::new("explorer");
            c.arg(file);
            c
        }
    };
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// Show the file selected in an Explorer window.
#[tauri::command]
fn reveal(path: String) -> Result<(), String> {
    Command::new("explorer").args(["/select,", &path]).spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// Use another global shortcut, such as "Ctrl+Alt+K". If it cannot be registered, the old one stays.
#[tauri::command]
fn set_hotkey(app: AppHandle, hotkey: String) -> Result<(), String> {
    let old = app.state::<Hotkey>().0.lock().unwrap().clone();
    app.global_shortcut().unregister_all().map_err(|e| e.to_string())?;
    if let Err(e) = register(&app, &hotkey) {
        let _ = register(&app, &old);
        return Err(format!("Could not use {hotkey}: {e}"));
    }
    let data = home().join("data");
    let _ = fs::create_dir_all(&data);
    fs::write(data.join("hotkey.txt"), &hotkey).map_err(|e| e.to_string())
}

/// Save an .ics reminder from the back end (a path such as /reminders/ics?fact_id=3, or the full URL) to a temp
/// file and open it in the default calendar app.
#[tauri::command]
fn open_ics(url: String) -> Result<(), String> {
    let base = tauri::Url::parse(&format!("http://127.0.0.1:{API_PORT}/")).unwrap();
    let url = base.join(&url).map_err(|e| e.to_string())?;
    if url.origin() != base.origin() {
        return Err("Only .ics files from the Sniff back end can be opened".into());
    }
    let path = format!("{}{}", url.path(), url.query().map(|q| format!("?{q}")).unwrap_or_default());
    let ics = get(&path).ok_or_else(|| format!("Could not get {path} from the back end"))?;
    let millis = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis();
    let file = std::env::temp_dir().join(format!("sniff-reminder-{millis}.ics"));
    fs::write(&file, ics).map_err(|e| e.to_string())?;
    Command::new("explorer").arg(&file).spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// The shortcut in use, so the settings show the saved one after a restart.
#[tauri::command]
fn get_hotkey(app: AppHandle) -> String {
    app.state::<Hotkey>().0.lock().unwrap().clone()
}

#[tauri::command]
fn hide_window(window: WebviewWindow) {
    let _ = window.hide();
}

/// "Start with Windows": a value named Sniff in the HKCU Run key. The app then starts in the tray (--hidden).
#[tauri::command]
fn set_autostart(enabled: bool) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let value = format!("\"{}\" --hidden", exe.display());
    let mut cmd = Command::new("reg");
    if enabled {
        cmd.args(["add", RUN_KEY, "/v", "Sniff", "/t", "REG_SZ", "/d", &value, "/f"]);
    } else {
        cmd.args(["delete", RUN_KEY, "/v", "Sniff", "/f"]);
    }
    let out = cmd.creation_flags(NO_WINDOW).output().map_err(|e| e.to_string())?;
    // deleting a value that is not there fails, and that is fine
    if out.status.success() || !enabled { Ok(()) } else { Err(String::from_utf8_lossy(&out.stderr).trim().to_string()) }
}

#[tauri::command]
fn autostart_enabled() -> bool {
    Command::new("reg").args(["query", RUN_KEY, "/v", "Sniff"]).creation_flags(NO_WINDOW).output().is_ok_and(|o| o.status.success())
}

fn main() {
    tauri::Builder::default()
        // Opening Sniff again (say from the Start menu while it sits in the tray) shows the running one instead.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(w) = app.get_webview_window("main") {
                show(&w);
            }
        }))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .manage(Children::default())
        .manage(Hotkey(Mutex::new(SHORTCUT.to_string())))
        .invoke_handler(tauri::generate_handler![open_file, reveal, hide_window, set_autostart, autostart_enabled, set_hotkey, get_hotkey, open_ics])
        .setup(|app| {
            let _ = fs::create_dir_all(home().join("logs"));
            let open = MenuItem::with_id(app, "open", "Open", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip(format!("Sniff ({SHORTCUT})"))
                .menu(&Menu::with_items(app, &[&open, &settings, &quit])?)
                .on_menu_event(|app, e| {
                    let Some(w) = app.get_webview_window("main") else { return };
                    match e.id.as_ref() {
                        "open" => show(&w),
                        "settings" => {
                            show(&w);
                            let _ = w.eval("window.dispatchEvent(new CustomEvent('sniff:go', { detail: 'settings' }))");
                        }
                        "quit" => app.exit(0),
                        _ => {}
                    }
                })
                .build(app)?;

            // another program may hold the shortcut; then the default is tried, and the tray icon still opens the window
            let saved = fs::read_to_string(home().join("data").join("hotkey.txt")).map(|s| s.trim().to_string());
            let hotkey = saved.unwrap_or_else(|_| SHORTCUT.to_string());
            match register(app.handle(), &hotkey).or_else(|e| {
                log(&format!("could not register {hotkey}: {e}"));
                register(app.handle(), SHORTCUT)
            }) {
                Ok(()) => log(&format!("shortcut {} registered", app.state::<Hotkey>().0.lock().unwrap())),
                Err(e) => log(&format!("could not register {SHORTCUT}: {e}")),
            }

            // In `tauri dev` the resource folder has no back end or llama-server: run them by hand (scripts/dev.ps1).
            let res = app.path().resource_dir()?;
            let backend = res.join("backend").join("sniff-backend.exe");
            if port_open(API_PORT) {
                log(&format!("backend: port {API_PORT} is already in use, reusing it"));
            } else if backend.exists() {
                spawn(app.handle(), "backend", &backend, &[]);
            }
            let llama = res.join("llama").join("llama-server.exe");
            if llama.exists() {
                let handle = app.handle().clone();
                std::thread::spawn(move || model_servers(handle, llama));
            }
            let handle = app.handle().clone();
            std::thread::spawn(move || reminders(handle));

            // started with Windows (--hidden): stay in the tray until the shortcut is pressed
            if !std::env::args().any(|a| a == "--hidden") {
                if let Some(w) = app.get_webview_window("main") {
                    show(&w);
                }
            }
            Ok(())
        })
        .on_window_event(|w, e| match e {
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close(); // closing hides to the tray; Quit in the tray menu ends the app
                let _ = w.hide();
            }
            WindowEvent::Focused(false) => {
                let just_shown = SHOWN_AT.lock().unwrap().is_some_and(|t| t.elapsed() < Duration::from_millis(800));
                if !just_shown {
                    let _ = w.hide(); // a launcher goes away when you click elsewhere
                }
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("could not start Sniff")
        .run(|app, e| {
            if let RunEvent::Exit = e {
                for mut child in app.state::<Children>().0.lock().unwrap().drain(..) {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                log("quit: child processes stopped");
            }
        });
}
