// Sniff desktop shell: a launcher window shown and hidden by a global shortcut, a tray icon, and the
// child processes it starts and stops (the FastAPI back end and two llama-server model servers).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewWindow, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

// Alt+Space opens the window menu in Windows and is PowerToys Run's default, so Sniff uses Alt+Shift+Space.
const SHORTCUT: &str = "Alt+Shift+Space";
// Same port as backend/sniff/desktop.py and frontend/src/api.ts. Not 8000, so a dev back end can run alongside.
const API_PORT: u16 = 8765;
const NO_WINDOW: u32 = 0x0800_0000; // CREATE_NO_WINDOW: no console windows for the child processes

#[derive(Default)]
struct Children(Mutex<Vec<Child>>);

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
            log(&format!("started {name} (pid {})", child.id()));
            app.state::<Children>().0.lock().unwrap().push(child);
        }
        Err(e) => log(&format!("could not start {name}: {e}")),
    }
}

/// Reuse a model server that already answers on its port. Otherwise start the bundled llama-server, with the
/// flags scripts/dev.ps1 uses, as soon as its model file is on disk (on first run the download screen puts it there).
fn model_servers(app: AppHandle, llama: PathBuf) {
    let models = home().join("models");
    let servers = [
        ("chat-server", 8080u16, "qwen3-1.7b-q4_k_m.gguf", "--jinja --reasoning-budget 0 -c 8192"),
        ("embed-server", 8081u16, "all-minilm-l6-v2.gguf", "--embedding -c 2048 -b 2048 -ub 2048"),
    ];
    let mut waiting = [true, true];
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

fn show(w: &WebviewWindow) {
    let _ = w.show();
    let _ = w.unminimize();
    let _ = w.set_focus();
}

fn toggle(app: &AppHandle) {
    let Some(w) = app.get_webview_window("main") else { return };
    if w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false) {
        let _ = w.hide();
    } else {
        show(&w);
    }
}

fn main() {
    tauri::Builder::default()
        // Opening Sniff again (say from the Start menu while it sits in the tray) shows the running one instead.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(w) = app.get_webview_window("main") {
                show(&w);
            }
        }))
        .manage(Children::default())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            let _ = fs::create_dir_all(home().join("logs"));
            let open = MenuItem::with_id(app, "open", "Open", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            TrayIconBuilder::new()
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

            let toggled = app.global_shortcut().on_shortcut(SHORTCUT, |app, _, e| {
                if e.state == ShortcutState::Pressed {
                    toggle(app);
                }
            });
            // another program may hold the shortcut; the tray icon still opens the window
            log(&match toggled {
                Ok(()) => format!("shortcut {SHORTCUT} registered"),
                Err(e) => format!("could not register {SHORTCUT}: {e}"),
            });

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
            Ok(())
        })
        .on_window_event(|w, e| {
            if let WindowEvent::CloseRequested { api, .. } = e {
                api.prevent_close(); // closing hides to the tray; Quit in the tray menu ends the app
                let _ = w.hide();
            }
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
