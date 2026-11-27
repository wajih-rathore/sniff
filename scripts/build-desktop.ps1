# Build the Sniff installer for the system it runs on, from a clean checkout:
#   Windows:       powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1   (NSIS .exe)
#   macOS, Linux:  pwsh scripts/build-desktop.ps1                                         (.dmg; AppImage and .deb)
# Needs uv, Node/npm and Rust (on Windows the GNU or MSVC toolchain); Linux also needs Tauri's system libraries
# (docs/INSTALL.md). .github/workflows/release.yml runs this on each system. Output: the bundles under the cargo
# target folder. The model weights are not bundled; the app downloads them on first run (docs/INSTALL.md).
param(
    [string]$Cache = (Join-Path (Split-Path -Parent $PSScriptRoot) ".cache"),  # cargo, npm and PyInstaller caches (gitignored)
    [string]$Llama = "",  # a folder with llama-server and its libraries; default: models/llama, downloaded if missing
    [string]$Bundles = ""  # tauri bundle types; default: nsis on Windows, dmg on macOS, appimage,deb on Linux
)
$ErrorActionPreference = "Stop"
$win = $env:OS -eq "Windows_NT"  # Windows PowerShell 5.1 has no $IsWindows
$mac = -not $win -and (uname) -eq "Darwin"
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root "desktop/build"
try { (Get-Process -Id $PID).PriorityClass = "BelowNormal" } catch {}  # child processes inherit it
$env:CARGO_HOME = Join-Path $Cache "cargo"
$env:CARGO_TARGET_DIR = Join-Path $Cache "cargo-target"
$env:CARGO_BUILD_JOBS = "2"
$env:npm_config_cache = Join-Path $Cache "npm"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Cache "pyinstaller"

function Invoke-Step([string]$dir, [scriptblock]$cmd) {
    Push-Location $dir
    try { & $cmd; if ($LASTEXITCODE) { throw "failed in ${dir}: exit $LASTEXITCODE" } } finally { Pop-Location }
}

# 1. llama-server, CPU build b12628 (the one scripts\dev.ps1 uses), for this system. The sha256 values are the ones
#    GitHub lists for the release assets; the Windows one also matches the zip we downloaded.
$asset, $sha256 = if ($win) { "llama-b12628-bin-win-cpu-x64.zip", "E2F60BE1E39AE47743E89F6210886E8D985B67108E1BE19B7BCC166D863BC580" }
    elseif ($mac -and (uname -m) -eq "arm64") { "llama-b12628-bin-macos-arm64.tar.gz", "CB68827E6DB91F31BB9D0F5C24EE32244F96EFB80E758993CC1823A3FADF47F1" }
    elseif ($mac) { "llama-b12628-bin-macos-x64.tar.gz", "00E6C8BC278ACB89D1653D5771B823CBDD217DDFCAAE718BC51D383018049517" }
    else { "llama-b12628-bin-ubuntu-x64.tar.gz", "D84BA76C8A9066F13F55834F0171CFA9BAE86E228E66F9B2BBFF9769D7CBF389" }
$exe = if ($win) { ".exe" } else { "" }
if (-not $Llama) {
    $Llama = Join-Path $root "models/llama"
    if (-not (Test-Path (Join-Path $Llama "llama-server$exe"))) {
        $archive = Join-Path $root "models/$asset"
        New-Item -ItemType Directory -Force $Llama | Out-Null
        if (-not (Test-Path $archive)) {
            Invoke-WebRequest "https://github.com/ggml-org/llama.cpp/releases/download/b12628/$asset" -OutFile $archive
        }
        if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $sha256) {
            throw "$archive does not match its sha256; delete it and run again"
        }
        if ($win) { Expand-Archive $archive $Llama -Force } else { tar -xzf $archive -C $Llama --strip-components 1 }
    }
}
Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$build/llama" | Out-Null
if ($win) {
    # only llama-server and the DLLs it loads, plus the Visual C++ runtime it needs (a clean Windows may not have it)
    Get-ChildItem $Llama -File | Where-Object {
        $_.Name -in "llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "mtmd.dll", "libomp.dll", "LICENSE-LLVM-OpenMP" -or $_.Name -like "ggml*.dll"
    } | Copy-Item -Destination "$build/llama"
    foreach ($dll in "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll") {
        Copy-Item (Join-Path $env:SystemRoot "System32\$dll") "$build/llama"
    }
} else {
    # llama-server and its libraries (the CPU backends are among them). cp -L copies what a symlink points to, since
    # the bundle may not keep the archive's links (libllama.so.1 -> libllama.so.1.0.0).
    # shortcut: each library lands under all its link names (about 30 MB extra); copy only the loader's names if size matters
    /bin/cp -L (Join-Path $Llama "llama-server") (Join-Path $Llama "LICENSE") (Get-ChildItem $Llama -Filter "lib*").FullName "$build/llama/"
    if ($LASTEXITCODE) { throw "could not copy llama-server" }
}

# 2. the back end, frozen with PyInstaller (one folder). PyInstaller builds for the system it runs on.
Invoke-Step (Join-Path $root "backend") {
    uv sync --frozen
    uv run --with pyinstaller pyinstaller sniff/desktop.py --noconfirm --clean --onedir --name sniff-backend --paths . `
        --distpath $build --workpath (Join-Path $Cache "pyinstaller/work") --specpath (Join-Path $Cache "pyinstaller") `
        --collect-data rapidocr_onnxruntime --collect-all sqlite_vec --collect-data docx --collect-data pptx --collect-all keyring
}
# Google's client library ships discovery files for every Google API (tens of MB); Sniff only uses Calendar
Get-ChildItem "$build/sniff-backend/_internal/googleapiclient/discovery_cache/documents" -Exclude "calendar.v3.json" -ErrorAction SilentlyContinue |
    Remove-Item

# 3. the front end and the desktop app; tauri builds the front end first (beforeBuildCommand)
if (-not $Bundles) { $Bundles = if ($win) { "nsis" } elseif ($mac) { "dmg" } else { "appimage,deb" } }
Invoke-Step (Join-Path $root "frontend") { npm ci --no-audit --no-fund }
Invoke-Step (Join-Path $root "desktop") { npm ci --no-audit --no-fund; npx tauri build --bundles $Bundles }

Get-ChildItem (Join-Path $env:CARGO_TARGET_DIR "release/bundle") -Recurse -File -Include "*.exe", "*.dmg", "*.AppImage", "*.deb" |
    ForEach-Object { "Bundle: $($_.FullName) ({0:N1} MB)" -f ($_.Length / 1MB) }
