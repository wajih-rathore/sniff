# Installing Sniff

Sniff runs on Windows, macOS and Linux. Each system gets its own installer, and none of them needs Python, a
terminal or a manual model download.

| System | File | Status |
|---|---|---|
| Windows 10/11, x64 | `Sniff_0.1.0_x64-setup.exe` | Built and tested on our laptop |
| macOS, Apple Silicon | `Sniff_0.1.0_aarch64.dmg` | Built by CI, not run yet |
| macOS, Intel | `Sniff_0.1.0_x64.dmg` | Built by CI, not run yet |
| Linux x64, any distribution | `Sniff_0.1.0_amd64.AppImage` | Built by CI, not run yet |
| Debian, Ubuntu 22.04 or newer | `Sniff_0.1.0_amd64.deb` | Built by CI, not run yet |

The macOS and Linux files come from `.github/workflows/release.yml`, which builds every installer on GitHub's
machines and starts each one's back end once as a smoke test (the file names above are the ones it produced).
Nobody has installed the macOS or Linux ones yet; until then, Linux and macOS teammates can run Sniff from source
(below).

## Windows

The installer is 166 MB. It installs for the current user only, so it needs no administrator rights.

### What the installer does

- Copies the app to `%LOCALAPPDATA%\Sniff` (usually `C:\Users\<you>\AppData\Local\Sniff`).
- Adds Sniff to the Start menu and to Settings > Apps, with an uninstaller. The last page offers a desktop shortcut.
- Installs nothing system-wide and writes nothing under `Program Files`. WebView2 is part of Windows 11; on an
  older Windows the installer downloads Microsoft's WebView2 bootstrapper if it is missing.

The installer is not signed yet, so Windows SmartScreen may warn the first time. Click "More info", then "Run anyway".

### Where the files go

Everything is under `%LOCALAPPDATA%\Sniff`. The program itself takes 497 MB (1,032 files); most of it is the back
end's libraries (OpenCV, PyMuPDF and ONNX Runtime for reading and OCR).

| Folder | What is in it |
|---|---|
| `sniff.exe` | The desktop app: the launcher window, the tray icon and the global shortcut |
| `backend\` | The Sniff back end (FastAPI, frozen with PyInstaller): readers, OCR models, search and the database code |
| `llama\` | `llama-server.exe` (llama.cpp build b12628, CPU) and its DLLs, including the Visual C++ runtime it needs |
| `models\` | The embedding model, downloaded on first run, and the chat model if you download it for Settings > AI > This laptop |
| `data\` | `sniff.db` (the index, facts and reminders), the MCP access log. The Google sign-in token is in Windows Credential Manager (`Sniff`); it falls back to a file here only when that fails |
| `logs\` | One log per process: `desktop.log`, `backend.log`, `chat-server.log`, `embed-server.log` |
| `secrets\` | Put `google_client.json` here to turn on Google Calendar reminders |

The browser engine keeps its cache in `%LOCALAPPDATA%\pk.edu.nu.sniff`.

### First run

When the embedding model is missing, Sniff shows a download screen. It fetches the one model that search,
sorting and facts need from the Ollama registry, the same file and checksum as `eval/fetch_models.py`. The OCR models
come with the back end.

| Model | File | Size |
|---|---|---|
| all-MiniLM-L6-v2 (embeddings) | `all-minilm-l6-v2.gguf` | 45,949,216 bytes (46 MB) |

A broken download resumes where it stopped, also after closing the app. Each file gets its final name only after its
sha256 matches; a file that does not match is deleted.

The chat model for Settings > AI > This laptop is not part of the first run, and Sniff works without it:

| Model | File | Size |
|---|---|---|
| Qwen3-1.7B, Q4_K_M (chat) | `qwen3-1.7b-q4_k_m.gguf` | 1,359,279,776 bytes (1.36 GB) |

`POST /ai/local/download` fetches it the same way (resumable, sha256 checked) and `GET /ai/local` reports its progress
(`size`, `have`, `running`, `error`, `ready`). This laptop is offered only once the file is there. A button for it in
Settings > AI is planned.

### Running

- **Alt+Shift+Space** shows or hides the launcher from any app. Alt+Space was the first choice, but Windows uses
  it for the window menu and PowerToys Run uses it too. Settings > Hotkey changes it; the new one is saved in
  `data\hotkey.txt`.
- The launcher is one frameless 760x480 window in the upper third of the screen, with no taskbar button. It hides
  when you click elsewhere, and on Esc.
- The tray icon has Open, Settings and Quit. Closing the window only hides it; Quit stops Sniff. Opening Sniff
  again from the Start menu shows the running window instead of starting a second copy.
- Settings > Start with Windows adds a `Sniff` value to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
  Sniff then starts in the tray, without the window. The uninstaller removes the value.
- Once an hour Sniff checks the expiry and due dates it found. For a date in the next 7 days it shows a Windows
  notification, at most once a day per document.
- On start, Sniff starts its back end on `127.0.0.1:8765` and the embedding server on 8081 (reused if one already
  answers there). The chat model is not loaded at start: when Settings > AI is set to This laptop, the back end
  starts `llama-server` on 8080 the first time a document or a question needs it, and stops it after 120 s without a
  call (`SNIFF_LLM_IDLE`). Most documents never need it. AI is off by default (see AI providers below). Quit stops every process Sniff started. They are in a Windows job object (the chat server joins it
  through the back end), so they also end if Sniff crashes. Model servers it only reuses are left alone.
- Enter on a result opens the file. A PDF hit on page 2 or later opens in Microsoft Edge at that page, because the
  default PDF reader may ignore page numbers. Ctrl+Enter shows the file in Explorer. A calendar reminder is saved
  as an `.ics` file in `%TEMP%` and opened in the default calendar app.
- Everything listens on 127.0.0.1 only.

### Uninstalling

Settings > Apps > Installed apps > Sniff > Uninstall, or run `%LOCALAPPDATA%\Sniff\uninstall.exe`.

The uninstaller removes the program files, the Start menu entry and the registry entries. Your index and the
downloaded models stay, so a reinstall does not download again. Tick "Delete the application data" to remove
them too (`data`, `models`, `logs`, `secrets` and the WebView2 cache). From a terminal:

```powershell
& "$env:LOCALAPPDATA\Sniff\uninstall.exe" /S /DELETEAPPDATA
```

## macOS

Not run on a Mac yet: everything in this section is what the build is set up to do. CI builds both installers and
starts their back end once. It needs macOS 15 or later: the OpenCV wheel the text reader uses is built for 15, and
running from source needs it too.

1. Download the `.dmg` for your Mac: `aarch64` for Apple Silicon (M1 and later), `x64` for Intel. Apple menu >
   About This Mac shows which one you have.
2. Open it and drag Sniff into Applications.
3. Sniff has only an ad-hoc signature. It is not notarised by Apple (planned; it needs a paid Apple Developer
   account), so macOS blocks the first start:
   - macOS 15 Sequoia and later: open Sniff, click Done on the warning, then go to System Settings > Privacy &
     Security, scroll to the bottom and click "Open Anyway". Confirm with your password.
   - macOS 14 and earlier: Control-click Sniff in Applications, choose Open, then Open again.
   - If macOS says Sniff "is damaged", remove the download flag and try again:
     `xattr -dr com.apple.quarantine /Applications/Sniff.app`
4. The first run downloads the models, as on Windows.

Where things go: the app is `/Applications/Sniff.app`, with the back end and `llama-server` inside
`Contents/Resources`. Data, models and logs go to `~/Library/Application Support/Sniff`, in the same folders as on
Windows. The Google sign-in token goes to the Keychain.

How it differs from Windows:
- The shortcut is Option+Shift+Space. Settings > Hotkey can use Cmd too.
- Enter opens the file in its default app, on page 1 (Preview takes no page number). Ctrl+Enter shows it in Finder.
- Start at sign-in adds `~/Library/LaunchAgents/Sniff.plist`. macOS may show a "background item added" notice.
- Reminders use macOS notifications. macOS may ask once whether Sniff may send them.
- The launcher gets a blurred (vibrancy) backdrop. It only shows once the window is transparent, which the UI
  branch sets up for the Windows acrylic.

Uninstall: quit Sniff from its menu bar icon, drag `/Applications/Sniff.app` to the Bin, and delete
`~/Library/Application Support/Sniff` to remove the index and the models. If start at sign-in was on, also delete
`~/Library/LaunchAgents/Sniff.plist`.

## Linux

Not run on Linux yet: everything in this section is what the build is set up to do. Only x86-64 is built.

- **AppImage** (any distribution with glibc 2.35 or newer, such as Ubuntu 22.04, Debian 12 or Fedora 36 and
  later): `chmod +x Sniff_0.1.0_amd64.AppImage`, then run it.
- **.deb** (Debian and Ubuntu): `sudo apt install ./Sniff_0.1.0_amd64.deb`. apt also installs WebKitGTK 4.1.
  The command is then `sniff`, and Sniff appears in the app menu.

Data, models and logs go to `$XDG_DATA_HOME/sniff`, which is `~/.local/share/sniff` on most systems. The Google
sign-in token goes to the Secret Service (GNOME Keyring or KWallet); without one, to a file in `data/`.

How it differs from Windows:
- **Shortcut on Wayland.** Alt+Shift+Space works on X11. On Wayland (the default on Ubuntu, Fedora and new KDE)
  apps cannot catch global shortcuts, and Settings > Hotkey says so. Bind a system shortcut to `sniff --toggle`
  instead; it shows or hides the running Sniff. With the AppImage, use its full path followed by `--toggle`.
  - GNOME: Settings > Keyboard > Keyboard Shortcuts > View and Customise Shortcuts > Custom Shortcuts > +.
  - KDE Plasma: System Settings > Keyboard > Shortcuts > Add New > Command or Script.
  - On Wayland the window also cannot place itself; the desktop decides where it opens.
- **Tray icon.** It needs AppIndicator support. Ubuntu has it; on plain GNOME install the "AppIndicator and
  KStatusNotifierItem Support" extension. Without it there is no tray icon; the shortcut still opens Sniff.
- Enter opens the file with `xdg-open`, on page 1. Ctrl+Enter asks the file manager to show the file (Nautilus,
  Dolphin, Nemo and Thunar support this); otherwise its folder opens.
- Start at sign-in adds `~/.config/autostart/Sniff.desktop`. For the AppImage it stores the file's path, so moving
  the AppImage breaks it.
- Reminders use the desktop's notification service. The backdrop is solid: Linux has no blur behind windows.

Uninstall: `sudo apt remove sniff` (the package name Tauri normally gives; `apt list --installed | grep -i sniff`
shows the real one), or delete the AppImage. `rm -rf ~/.local/share/sniff` removes the index and the models, and
deleting `~/.config/autostart/Sniff.desktop` turns off start at sign-in.

## AI providers (optional)

Sniff works without AI: rules read most fields, a small classifier sorts most documents, and code does every sum,
count and date comparison. AI is **off** until you pick a provider in Settings > AI. When it is on, a model only fills
gaps: a required field the rules missed, a question the rules cannot read, and written answers from passages. No model
does arithmetic.

| Provider | What it needs |
|---|---|
| Anthropic | Your API key. The model is `claude-haiku-5-5` unless you name another |
| OpenAI-compatible | Base URL, model and key: OpenAI, OpenRouter, Groq, Gemini's OpenAI-compatible endpoint, or a local Ollama, LM Studio or llama.cpp server (a local server needs no key) |
| This laptop | The bundled `llama-server` and Qwen3-1.7B, offered when the model file is downloaded. Nothing leaves the computer |

What a provider other than this laptop gets:
- Only the candidate lines the extractor already builds (the rows with a missing field's label and the row under it),
  a question, or the top passages for a written answer. Never a whole document: a document whose labels are not
  printed is not sent, and such a provider never sorts documents into types.
- Never a document of a type hidden from agents by default (ID cards, passports, bank statements, offer letters,
  transcripts and unclassified files), unless you allow that type in Settings > AI.
- ID numbers are masked as for MCP: `35202-1234567-8` goes out as `•••••-•••••67-8`.
- `GET /ai/preview?file_id=N` or `?question=...` returns exactly what would be sent. While AI is off it shows what a
  cloud provider would get. For a file not sorted into a type yet, it sorts it as the background worker will, and
  `note` says what that means.

The key is kept in the system's password store (Windows Credential Manager, the macOS Keychain or the Secret Service,
as `Sniff` / `ai-<provider>`), never in the database, logs or error messages. Without a password store the key is not
saved. A key belongs to its server: when the base URL moves to another server, the OpenAI-compatible key is
removed, so it is never sent to the new one. The base URL must use `https://`, except for a server on this laptop
(`localhost`, `127.0.0.1` or `::1`). Requests time out after 30 seconds. If a call fails, Sniff keeps the facts the rules found and says why a
written answer is missing. `POST /ai/test` makes a one-token call and reports success or a plain reason.

The API is `GET` and `PUT /ai/settings` (`provider`: `off`, `anthropic`, `openai` or `local`, plus `base_url`, `model`,
`allow` and a write-only `key`), `POST /ai/test` and `GET /ai/preview`. `SNIFF_AI=local` overrides the saved provider;
the evaluation scripts set it.

## Known limitations

- Only the Windows build has been run. CI builds the macOS and Linux installers and smoke-tests their back end,
  but nobody has installed them yet.
- macOS: not notarised, so the first start needs the steps above.
- Linux on Wayland: no global shortcut; use `sniff --toggle`.
- Opening a PDF at the matching page works on Windows only (through Edge). Elsewhere the file opens on page 1.
- If Sniff crashes on macOS or Linux, the back end and the chat server notice and stop, but the embedding server
  keeps running. The next start finds it on port 8081 and reuses it. Windows ends all of them through the job
  object. Quitting normally stops everything on every system.
- No ARM builds for Linux or Windows. GitHub keeps Intel Mac machines only until August 2027.

## Run from source (Linux and macOS)

This works before CI does. It needs git, Python's `uv`, Node 22 with npm, and Rust (from rustup.rs). On macOS also
the Xcode command line tools (`xcode-select --install`). On Debian or Ubuntu also Tauri's libraries:

```bash
sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev build-essential curl file
```

From the repo root:

```bash
# 1. the back end's packages, on a Python that uv installs. Some Pythons cannot load SQLite extensions (the one on
#    GitHub's Macs cannot), and Sniff's vector search is one: the back end then stops with "cannot load SQLite
#    extensions". If an earlier `uv sync` made backend/.venv with such a Python, delete backend/.venv first.
(cd backend && uv sync --python-preference only-managed)

# 2. llama-server, the same build as on Windows (b12628). Pick your file:
#    Apple Silicon: llama-b12628-bin-macos-arm64.tar.gz, Intel Mac: llama-b12628-bin-macos-x64.tar.gz,
#    Linux: llama-b12628-bin-ubuntu-x64.tar.gz
F=llama-b12628-bin-ubuntu-x64.tar.gz
mkdir -p models/llama
curl -L -o "models/$F" "https://github.com/ggml-org/llama.cpp/releases/download/b12628/$F"
shasum -a 256 "models/$F"   # must match the value in scripts/build-desktop.ps1
tar -xzf "models/$F" -C models/llama --strip-components 1
# On a Mac, `brew install llama.cpp` works too: Sniff then uses the llama-server on the PATH (a newer build).

# 3. the embedding model, 46 MB, resumable. Add qwen3-1.7b (1.36 GB) to run AI on this laptop
python3 eval/fetch_models.py all-minilm

# 4. the embedding server; leave it running
models/llama/llama-server -m models/all-minilm-l6-v2.gguf --embedding --port 8081 -c 2048 -b 2048 -ub 2048
```

In a second terminal, the API on port 8000. It starts the chat server (port 8080) by itself the first time a
question needs it, from `models/llama/llama-server`:

```bash
cd backend && uv run uvicorn sniff.api:app --port 8000
```

In a third terminal, the desktop window. The first build takes several minutes and about 3 GB of disk:

```bash
(cd frontend && npm ci) && cd desktop && npm ci && npx tauri dev
```

`npx tauri dev` is the same CLI as `cargo tauri dev`, without installing it, and it also starts the front end's dev
server. Without Rust, `cd frontend && npm ci && npm run dev` and http://localhost:5173 in a browser give the same
screens, without the shortcut, the tray and the notifications. Run from source, the data goes to `.sniff/` in the
repo, not to the app folder.

## Tester checklist

For each item write pass or fail, and what you saw. A screenshot helps for anything that looks wrong.

1. **System:** OS and version, the chip (Apple Silicon or Intel / x86-64), RAM, and on Linux X11 or Wayland
   (`echo $XDG_SESSION_TYPE`).
2. **Install:** which file, any warning and what you clicked, time to install.
3. **First run:** the download screen appears. Note how long 46 MB takes. Close Sniff halfway and start it again:
   it should resume. Afterwards `models/` in the data folder holds `all-minilm-l6-v2.gguf` and no `.part` file.
4. **Index a folder:** Settings > add a folder with about 10 documents (PDFs, a Word file, a photo of a receipt).
   Note the time until they show as indexed.
5. **Search:** type a word from one document. Is the right file at the top? Enter opens it, Ctrl+Enter shows it in
   the file manager.
6. **Ask:** a question your documents can answer, such as "When does my passport expire?". Note the answer, whether
   it is right, and how long the first answer took (with AI set to This laptop, the chat model loads then).
7. **Hotkey:** from another app, Alt+Shift+Space (Option+Shift+Space on a Mac) shows the window, and again hides
   it. On Wayland, set up `sniff --toggle` as above and try that. Change the hotkey in Settings and restart Sniff:
   the new one is kept.
8. **Tray and start at sign-in:** the tray (menu bar) icon opens Sniff. Turn on start at sign-in, sign out and in
   again: Sniff is in the tray, without a window.
9. **Quit:** Quit from the tray. Then check nothing is left: `ps aux | grep -E 'sniff|llama-server'` (Task Manager on
   Windows). Only model servers you started yourself may remain.
10. **Uninstall:** as described for your system. Check that the program is gone, and the data folder too if you
    chose to delete it.
11. **Report back:** send the list, the screenshots and the `logs/` folder from the data folder (`desktop.log`,
    `backend.log`, `chat-server.log`, `embed-server.log`) to Wajih.

## Building the installers

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1     # Windows
pwsh scripts/build-desktop.ps1                                         # macOS or Linux (PowerShell 7)
```

The same script builds the installer for the system it runs on, and CI (`.github/workflows/release.yml`) runs it on
`windows-latest`, `macos-15` (Apple Silicon), `macos-15-intel` and `ubuntu-22.04` (GitHub retired `macos-14` in November 2026 and
`macos-13` in December 2025). The script needs uv, Node and npm, and Rust;
Linux also needs the libraries listed under "Run from source". It downloads llama.cpp build b12628 for the system
into `models/llama` and checks its sha256, freezes the back end with PyInstaller, builds the front end and runs
`tauri build`. On macOS it freezes the back end with a Python that uv installs (see "Run from source", step 1;
delete `backend/.venv` first if it was made with another Python). Builds run at below-normal priority with 2 cargo
jobs, and all caches go to `.cache/` in the repo.
The bundles land in `.cache/cargo-target/release/bundle/` (`nsis/`, `dmg/`, `appimage/`, `deb/`).

Pushing a tag such as `v0.1.0` makes CI attach all five files to a draft GitHub release. Running the workflow by
hand (Actions > Release > Run workflow) keeps them as downloadable artifacts only. Only the Windows installer has
been installed and run, on our laptop; CI only starts each installer's back end with `--check`.

On our laptop Rust uses the GNU toolchain (`x86_64-pc-windows-gnu`) with MSYS2's MinGW on the PATH, because
Visual Studio Build Tools do not fit on drive C:. CI uses the MSVC toolchain that GitHub's Windows machines have.

## Clean-machine test in Windows Sandbox (8 GB, no GPU)

This answers whether a person with an ordinary 8 GB laptop and nothing installed can install Sniff, get the
models and use it. Windows Sandbox starts a fresh copy of Windows each time: no Python, no models, no dev tools.
`scripts/sandbox/sniff-8gb.wsb` gives it 8 GB of RAM, no GPU and network access, maps a read-only folder with
the installer, the test script and 5 dev documents, and a writable results folder.

Status: ready, not run yet. Windows Sandbox must be turned on once (Windows 11 Pro, admin, then a reboot):
`Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM`.

1. Build the installer (`scripts\build-desktop.ps1`).
2. Run `scripts\sandbox\prepare.ps1`. It fills `E:\E\FAST\Semester 7\GenAI\.cache\sandbox\in` (installer,
   `smoke.ps1`, 5 documents from the dev split: a gas bill, a receipt photo, a certificate, a scanned ID card and a
   recipe). On another machine, change the two host paths in `sniff-8gb.wsb` and pass `-Box`.
3. Close other heavy programs first: the sandbox may take up to 8 GB of the host's memory.
4. Double-click `scripts\sandbox\sniff-8gb.wsb`. At logon the sandbox runs `smoke.ps1`, which:
   1. installs Sniff silently for the sandbox user;
   2. starts it and waits for the back end;
   3. starts the first-run download (the same call as the Download button), 46 MB, and waits until the
      embedding server answers;
   4. indexes the 5 documents;
   5. runs one search and one ask, timed;
   6. quits from the tray menu and checks that no Sniff process is left.
5. Read `.cache\sandbox\results\results.json` and the screenshots next to it. Each step has its time (`at_s` is
   seconds since the script started); `5_search_ask.time_to_first_answer_s` is the time from the start of the
   install to the first answer, download included. `peak_working_set_mb` is the peak memory of each process, and
   `failures` lists any step that went wrong.

Steps 4 and 5 call the API the launcher uses, rather than typing in the window; the script also types the question
into the launcher for a screenshot, but does not depend on it. The sandbox is thrown away when it is closed, so
every run downloads the models again.

## Clean-VM install test (planned)

Not done yet. Things the sandbox run does not cover. On a fresh Windows 11 VM with 8 GB of RAM and no GPU:

1. Copy the installer to the VM. Run it as a normal user. Note any SmartScreen or UAC prompt (there should be no UAC prompt).
2. Check that `%LOCALAPPDATA%\Sniff` has `sniff.exe`, `backend\` and `llama\`, and nothing was written to `Program Files`.
3. Start Sniff. The download screen should appear. Note the time to download 46 MB.
4. Stop the network in the middle of the download, start it again, and check that it resumes rather than restarts.
5. Close and reopen Sniff in the middle of the download. Press Resume and check that it continues.
6. After the download, check `models\` holds `all-minilm-l6-v2.gguf` and no `.part` file is left.
7. Check that `logs\embed-server.log` shows the embedding server loaded, and that no chat `llama-server.exe` runs yet.
8. Index a folder of 10 test documents in Settings. Note the time per document.
9. Run a search and ask a question. Note the answer time.
10. Press Alt+Shift+Space from another app: the window shows. Press it again: it hides.
11. Close the window with Alt+F4: it hides to the tray. Open it from the tray. Try Settings in the tray menu.
12. Record peak memory of `sniff.exe`, `sniff-backend.exe` and both `llama-server.exe` in Task Manager. Wait 2 minutes
    without asking anything: the chat `llama-server.exe` should be gone (`logs\chat-server.log` shows it started).
13. Quit from the tray. Check in Task Manager that no `sniff-backend.exe` or `llama-server.exe` is left.
14. Uninstall with "Delete the application data" ticked. Check `%LOCALAPPDATA%\Sniff`, `%LOCALAPPDATA%\pk.edu.nu.sniff`,
    the Start menu entry and `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall` are all gone.
