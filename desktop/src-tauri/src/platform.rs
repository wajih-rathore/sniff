// What differs between Windows, macOS and Linux: where Sniff keeps its files, how its child processes end with it,
// opening and revealing files, the backdrop, start at sign-in and the limits of the global shortcut.
// main.rs only calls these functions. Only the Windows branches are built and run on our laptop; the macOS and Linux
// branches are checked by CI (.github/workflows/release.yml) and by teammates (docs/INSTALL.md).

use std::path::{Path, PathBuf};
use std::process::{Child, Command};

use tauri::window::{Effect, EffectsBuilder};
use tauri::{AppHandle, WebviewWindow, Wry};

/// Data, models and logs: SNIFF_HOME if set, else %LOCALAPPDATA%\Sniff, ~/Library/Application Support/Sniff, or
/// $XDG_DATA_HOME/sniff (~/.local/share/sniff). The same rule as app_home() in backend/sniff/config.py.
pub fn home() -> PathBuf {
    if let Some(h) = std::env::var_os("SNIFF_HOME") {
        return h.into();
    }
    let var = |k: &str| std::env::var_os(k).map(PathBuf::from).filter(|p| p.is_absolute());
    if cfg!(windows) {
        var("LOCALAPPDATA").expect("LOCALAPPDATA is not set").join("Sniff")
    } else if cfg!(target_os = "macos") {
        var("HOME").expect("HOME is not set").join("Library/Application Support/Sniff")
    } else {
        var("XDG_DATA_HOME").unwrap_or_else(|| var("HOME").expect("HOME is not set").join(".local/share")).join("sniff")
    }
}

/// Before a child process starts. Windows: no console window. Unix: the child leads a new process group, so
/// stop() and the signal handler also reach what it starts (the back end starts the chat llama-server).
pub fn prepare(cmd: &mut Command) {
    #[cfg(windows)]
    std::os::windows::process::CommandExt::creation_flags(cmd, 0x0800_0000); // CREATE_NO_WINDOW
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(cmd, 0);
}

/// After a child process started: make sure it ends with Sniff. Windows: it joins a job object that kills its
/// processes when Sniff exits in any way, a crash included. Unix: its group is noted, so SIGTERM, SIGINT or SIGHUP
/// to Sniff stops the group first. Returns whether that worked.
pub fn adopt(child: &Child) -> bool {
    #[cfg(windows)]
    return win::adopt(child);
    #[cfg(unix)]
    return unix::adopt(child.id() as i32);
}

/// Quit: stop a child and everything it started. On Unix the group gets SIGTERM and 3 s to exit, then SIGKILL.
pub fn stop(child: &mut Child) {
    #[cfg(unix)]
    unix::stop(child);
    let _ = child.kill(); // Windows; on Unix the group is gone by now and this does nothing
    let _ = child.wait();
}

/// Open a file in its default app. Windows: a PDF with a page above 1 opens in Microsoft Edge at that page, because
/// the default PDF reader may ignore page numbers (Edge comes with Windows 11 and reads #page=N). macOS and Linux
/// have no common way to pass a page to the default app, so the file opens on page 1.
pub fn open(file: &Path, page: Option<u32>) -> Result<(), String> {
    if cfg!(windows) {
        let edge = PathBuf::from(std::env::var_os("ProgramFiles(x86)").unwrap_or_default()).join(r"Microsoft\Edge\Application\msedge.exe");
        let pdf = file.extension().is_some_and(|e| e.eq_ignore_ascii_case("pdf"));
        return match page {
            Some(n) if n > 1 && pdf && edge.exists() => {
                let mut url = tauri::Url::from_file_path(file).map_err(|_| format!("Not a full path: {}", file.display()))?;
                url.set_fragment(Some(&format!("page={n}")));
                run(Command::new(edge).arg(url.as_str()))
            }
            _ => run(Command::new("explorer").arg(file)),
        };
    }
    run(Command::new(if cfg!(target_os = "macos") { "open" } else { "xdg-open" }).arg(file))
}

/// Show the file selected in the file manager: Explorer, Finder, or on Linux the file manager over D-Bus
/// (org.freedesktop.FileManager1, which Nautilus, Dolphin, Nemo and Thunar answer). Without one, the folder opens.
pub fn reveal(file: &Path) -> Result<(), String> {
    if cfg!(windows) {
        return run(Command::new("explorer").arg("/select,").arg(file));
    }
    if cfg!(target_os = "macos") {
        return run(Command::new("open").arg("-R").arg(file));
    }
    let uri = file_uri(file)?;
    let folder = file.parent().unwrap_or(file).to_path_buf();
    // in a thread: dbus-send waits for the reply, and a command must not block the window
    std::thread::spawn(move || {
        let shown = Command::new("dbus-send")
            .args(["--session", "--print-reply", "--reply-timeout=5000", "--dest=org.freedesktop.FileManager1", "--type=method_call",
                   "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems", &format!("array:string:{uri}"), "string:"])
            .output()
            .is_ok_and(|o| o.status.success());
        if !shown {
            let _ = Command::new("xdg-open").arg(folder).status();
        }
    });
    Ok(())
}

/// file:// URI of a full path. dbus-send splits array items at commas, so a comma is percent-encoded too.
fn file_uri(path: &Path) -> Result<String, String> {
    tauri::Url::from_file_path(path).map(|u| u.as_str().replace(',', "%2C")).map_err(|_| format!("Not a full path: {}", path.display()))
}

/// Start a helper program without waiting for it. A thread reaps it, so no zombie is left on Unix.
fn run(cmd: &mut Command) -> Result<(), String> {
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    std::thread::spawn(move || child.wait());
    Ok(())
}

/// The see-through backdrop behind the launcher. Windows: acrylic, set in tauri.conf.json (windowEffects; the UI
/// branch owns it). macOS: vibrancy, set here. Linux has no such effect. It only shows on a transparent window, and on
/// macOS a transparent window needs macOSPrivateApi (tauri.conf.json). Returns false when the page should paint a
/// solid background itself.
pub fn backdrop(w: &WebviewWindow) -> bool {
    if cfg!(target_os = "macos") {
        return w.set_effects(EffectsBuilder::new().effect(Effect::HudWindow).build()).is_ok();
    }
    cfg!(windows)
}

/// Plugins only some systems use: start at sign-in on macOS (a LaunchAgent) and Linux (~/.config/autostart).
/// Windows keeps its own Run-key value (win::set_autostart), whose path is quoted and which the uninstaller removes.
pub fn plugins(b: tauri::Builder<Wry>) -> tauri::Builder<Wry> {
    #[cfg(not(windows))]
    let b = b.plugin(tauri_plugin_autostart::Builder::new().arg("--hidden").build());
    b
}

/// Start Sniff at sign-in, in the tray (--hidden).
pub fn set_autostart(app: &AppHandle, enabled: bool) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = app;
        win::set_autostart(enabled)
    }
    #[cfg(not(windows))]
    {
        use tauri_plugin_autostart::ManagerExt;
        let a = app.autolaunch();
        let done = if enabled { a.enable() } else { a.disable() };
        done.map_err(|e| e.to_string())
    }
}

pub fn autostart_enabled(app: &AppHandle) -> bool {
    #[cfg(windows)]
    {
        let _ = app;
        win::autostart_enabled()
    }
    #[cfg(not(windows))]
    {
        use tauri_plugin_autostart::ManagerExt;
        app.autolaunch().is_enabled().unwrap_or(false)
    }
}

/// Why the global shortcut may not work, for Settings, or None. The shortcut plugin grabs keys through X11, and a
/// Wayland session does not hand global keys to apps. There the user binds a system shortcut to `sniff --toggle`.
pub fn hotkey_note() -> Option<String> {
    let session = std::env::var("XDG_SESSION_TYPE").unwrap_or_default();
    wayland_note(cfg!(target_os = "linux"), std::env::var_os("WAYLAND_DISPLAY").is_some(), &session)
}

fn wayland_note(linux: bool, wayland_display: bool, session_type: &str) -> Option<String> {
    (linux && (wayland_display || session_type == "wayland")).then(|| {
        "Wayland does not let apps catch global shortcuts, so this one may not work. In your desktop's keyboard \
         settings, add a custom shortcut that runs: sniff --toggle"
            .to_string()
    })
}

#[cfg(windows)]
mod win {
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use std::process::{Child, Command};
    use std::sync::OnceLock;

    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    const NO_WINDOW: u32 = 0x0800_0000;
    const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"; // the uninstaller deletes our "Sniff" value

    /// A job object that kills its processes when its last handle closes. Sniff holds the only handle, so when Sniff
    /// exits in any way, a crash included, the back end and the model servers it started end with it.
    fn job() -> usize {
        static JOB: OnceLock<usize> = OnceLock::new();
        *JOB.get_or_init(|| unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let size = std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32;
            if job.is_null() || SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info as *const _ as _, size) == 0 {
                crate::log("could not set up the job object: child processes will outlive a crash");
            }
            job as usize
        })
    }

    pub fn adopt(child: &Child) -> bool {
        unsafe { AssignProcessToJobObject(job() as _, child.as_raw_handle() as _) != 0 }
    }

    /// "Start with Windows": a value named Sniff in the HKCU Run key.
    pub fn set_autostart(enabled: bool) -> Result<(), String> {
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

    pub fn autostart_enabled() -> bool {
        Command::new("reg").args(["query", RUN_KEY, "/v", "Sniff"]).creation_flags(NO_WINDOW).output().is_ok_and(|o| o.status.success())
    }
}

#[cfg(unix)]
mod unix {
    use std::process::Child;
    use std::sync::atomic::{AtomicI32, Ordering::SeqCst};
    use std::sync::Once;
    use std::time::{Duration, Instant};

    // Process groups of the children Sniff started. A signal handler may not take a lock, hence atomics.
    static GROUPS: [AtomicI32; 8] = [const { AtomicI32::new(0) }; 8];

    pub fn adopt(pid: i32) -> bool {
        static HANDLERS: Once = Once::new();
        HANDLERS.call_once(|| {
            for sig in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
                unsafe { libc::signal(sig, on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t) };
            }
        });
        GROUPS.iter().any(|g| g.compare_exchange(0, pid, SeqCst, SeqCst).is_ok())
    }

    /// Logging out, Ctrl+C in a terminal, or `kill`: stop the children's groups, then exit. Only async-signal-safe
    /// calls here (kill, _exit).
    extern "C" fn on_signal(sig: libc::c_int) {
        for g in &GROUPS {
            let pid = g.load(SeqCst);
            if pid > 0 {
                unsafe { libc::kill(-pid, libc::SIGTERM) };
            }
        }
        unsafe { libc::_exit(128 + sig) };
    }

    pub fn stop(child: &mut Child) {
        let pid = child.id() as i32;
        unsafe { libc::kill(-pid, libc::SIGTERM) };
        let t = Instant::now();
        while t.elapsed() < Duration::from_secs(3) && matches!(child.try_wait(), Ok(None)) {
            std::thread::sleep(Duration::from_millis(100));
        }
        unsafe { libc::kill(-pid, libc::SIGKILL) }; // whatever is left in the group, such as a chat server
        for g in &GROUPS {
            let _ = g.compare_exchange(pid, 0, SeqCst, SeqCst);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_uri_encodes_spaces_and_commas() {
        let path = if cfg!(windows) { r"C:\Docs\a b,c.pdf" } else { "/Docs/a b,c.pdf" };
        let want = if cfg!(windows) { "file:///C:/Docs/a%20b%2Cc.pdf" } else { "file:///Docs/a%20b%2Cc.pdf" };
        assert_eq!(file_uri(Path::new(path)).unwrap(), want);
        assert!(file_uri(Path::new("relative.pdf")).is_err());
    }

    #[test]
    fn wayland_note_only_on_linux_wayland() {
        assert!(wayland_note(true, true, "").is_some());
        assert!(wayland_note(true, false, "wayland").is_some());
        assert!(wayland_note(true, false, "x11").is_none());
        assert!(wayland_note(false, true, "wayland").is_none());
    }
}
