# Installing Sniff on Windows

Sniff ships as one installer, `Sniff_0.1.0_x64-setup.exe` (166 MB). It installs for the current user only, so it
needs no administrator rights. Nobody has to install Python, open a terminal or fetch models by hand.

## What the installer does

- Copies the app to `%LOCALAPPDATA%\Sniff` (usually `C:\Users\<you>\AppData\Local\Sniff`).
- Adds Sniff to the Start menu and to Settings > Apps, with an uninstaller. The last page offers a desktop shortcut.
- Installs nothing system-wide and writes nothing under `Program Files`. WebView2 is part of Windows 11; on an
  older Windows the installer downloads Microsoft's WebView2 bootstrapper if it is missing.

The installer is not signed yet, so Windows SmartScreen may warn the first time. Click "More info", then "Run anyway".

## Where the files go

Everything is under `%LOCALAPPDATA%\Sniff`. The program itself takes 496 MB (1,032 files); most of it is the back
end's libraries (OpenCV, PyMuPDF and ONNX Runtime for reading and OCR).

| Folder | What is in it |
|---|---|
| `sniff.exe` | The desktop app: the launcher window, the tray icon and the global shortcut |
| `backend\` | The Sniff back end (FastAPI, frozen with PyInstaller): readers, OCR models, search and the database code |
| `llama\` | `llama-server.exe` (llama.cpp build b12628, CPU) and its DLLs, including the Visual C++ runtime it needs |
| `models\` | The two model files, downloaded on first run |
| `data\` | `sniff.db` (the index, facts and reminders), the Google sign-in token, the MCP access log |
| `logs\` | One log per process: `desktop.log`, `backend.log`, `chat-server.log`, `embed-server.log` |
| `secrets\` | Put `google_client.json` here to turn on Google Calendar reminders |

The browser engine keeps its cache in `%LOCALAPPDATA%\pk.edu.nu.sniff`.

## First run

When the model files are missing, Sniff shows a download screen. It fetches two models from the Ollama registry,
the same files and checksums as `eval/fetch_models.py`:

| Model | File | Size |
|---|---|---|
| Qwen3-1.7B, Q4_K_M (chat) | `qwen3-1.7b-q4_k_m.gguf` | 1,359,279,776 bytes |
| all-MiniLM-L6-v2 (embeddings) | `all-minilm-l6-v2.gguf` | 45,949,216 bytes |

That is 1.41 GB in total. A broken download resumes where it stopped, also after closing the app. Each file
gets its final name only after its sha256 matches; a file that does not match is deleted.

## Running

- **Alt+Shift+Space** shows or hides the launcher from any app. Alt+Space was the first choice, but Windows uses
  it for the window menu and PowerToys Run uses it too.
- The tray icon has Open, Settings and Quit. Closing the window only hides it; Quit stops Sniff. Opening Sniff
  again from the Start menu shows the running window instead of starting a second copy.
- On start, Sniff starts its back end on `127.0.0.1:8765`. It checks ports 8080 (chat) and 8081 (embeddings): if a
  model server already answers there it is reused, otherwise Sniff starts its own `llama-server` with the same
  flags as `scripts/dev.ps1`. Quit stops every process Sniff started.
- Everything listens on 127.0.0.1 only.

## Uninstalling

Settings > Apps > Installed apps > Sniff > Uninstall, or run `%LOCALAPPDATA%\Sniff\uninstall.exe`.

The uninstaller removes the program files, the Start menu entry and the registry entries. Your index and the
downloaded models stay, so a reinstall does not download again. Tick "Delete the application data" to remove
them too (`data`, `models`, `logs`, `secrets` and the WebView2 cache). From a terminal:

```powershell
& "$env:LOCALAPPDATA\Sniff\uninstall.exe" /S /DELETEAPPDATA
```

## Building the installer

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

It needs uv, Node and npm, and Rust. It freezes the back end with PyInstaller, copies `llama-server` (downloaded
from the llama.cpp release if `models\llama` is empty), builds the front end and runs `tauri build`. Builds run at
below-normal priority with 2 cargo jobs, and all caches go to `.cache\` in the repo. The installer lands in
`.cache\cargo-target\release\bundle\nsis\`.

On our laptop Rust uses the GNU toolchain (`x86_64-pc-windows-gnu`) with MSYS2's MinGW on the PATH, because
Visual Studio Build Tools do not fit on drive C:.

## Clean-VM install test (planned)

Not done yet. To run on a fresh Windows 11 VM with 8 GB of RAM and no GPU:

1. Copy the installer to the VM. Run it as a normal user. Note any SmartScreen or UAC prompt (there should be no UAC prompt).
2. Check that `%LOCALAPPDATA%\Sniff` has `sniff.exe`, `backend\` and `llama\`, and nothing was written to `Program Files`.
3. Start Sniff. The download screen should appear. Note the time to download 1.41 GB.
4. Stop the network in the middle of the download, start it again, and check that it resumes rather than restarts.
5. Close and reopen Sniff in the middle of the download. Press Resume and check that it continues.
6. After the download, check `models\` holds the two files and no `.part` file is left.
7. Check that `logs\chat-server.log` and `logs\embed-server.log` show both servers loaded.
8. Index a folder of 10 test documents in Settings. Note the time per document.
9. Run a search and ask a question. Note the answer time.
10. Press Alt+Shift+Space from another app: the window shows. Press it again: it hides.
11. Close the window with Alt+F4: it hides to the tray. Open it from the tray. Try Settings in the tray menu.
12. Record peak memory of `sniff.exe`, `sniff-backend.exe` and both `llama-server.exe` in Task Manager.
13. Quit from the tray. Check in Task Manager that no `sniff-backend.exe` or `llama-server.exe` is left.
14. Uninstall with "Delete the application data" ticked. Check `%LOCALAPPDATA%\Sniff`, `%LOCALAPPDATA%\pk.edu.nu.sniff`,
    the Start menu entry and `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall` are all gone.

## Known limits

- If Sniff crashes instead of quitting, its child processes keep running. End `sniff-backend.exe` and
  `llama-server.exe` in Task Manager before you reinstall or uninstall.
- The Evaluation page is empty in the installed app. Evaluation results live in the repo (`eval/results/`).
