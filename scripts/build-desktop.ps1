# Build the Sniff Windows installer from a clean checkout:
#   powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
# Needs uv, Node/npm and Rust (the GNU or MSVC toolchain). Output: the NSIS installer under the cargo target folder.
# The model weights are not bundled; the app downloads them on first run (docs/INSTALL.md).
param(
    [string]$Cache = (Join-Path (Split-Path -Parent $PSScriptRoot) ".cache"),  # cargo, npm and PyInstaller caches (gitignored)
    [string]$Llama = ""  # a folder with llama-server.exe and its DLLs; default: models\llama, downloaded if missing
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root "desktop\build"
(Get-Process -Id $PID).PriorityClass = "BelowNormal"  # child processes inherit it
$env:CARGO_HOME = Join-Path $Cache "cargo"
$env:CARGO_TARGET_DIR = Join-Path $Cache "cargo-target"
$env:CARGO_BUILD_JOBS = "2"
$env:npm_config_cache = Join-Path $Cache "npm"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Cache "pyinstaller"

function Invoke-Step([string]$dir, [scriptblock]$cmd) {
    Push-Location $dir
    try { & $cmd; if ($LASTEXITCODE) { throw "failed in ${dir}: exit $LASTEXITCODE" } } finally { Pop-Location }
}

# 1. llama-server (CPU build b12628), the same build scripts\dev.ps1 uses
if (-not $Llama) {
    $Llama = Join-Path $root "models\llama"
    if (-not (Test-Path (Join-Path $Llama "llama-server.exe"))) {
        $zip = Join-Path $root "models\llama-b12628-bin-win-cpu-x64.zip"
        New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
        if (-not (Test-Path $zip)) {
            Invoke-WebRequest "https://github.com/ggml-org/llama.cpp/releases/download/b12628/llama-b12628-bin-win-cpu-x64.zip" -OutFile $zip
        }
        if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne "E2F60BE1E39AE47743E89F6210886E8D985B67108E1BE19B7BCC166D863BC580") {
            throw "$zip does not match its sha256; delete it and run again"
        }
        Expand-Archive $zip $Llama -Force
    }
}
Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$build\llama" | Out-Null
# only llama-server and the DLLs it loads, plus the Visual C++ runtime it needs (a clean Windows may not have it)
Get-ChildItem $Llama -File | Where-Object {
    $_.Name -in "llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "mtmd.dll", "libomp.dll", "LICENSE-LLVM-OpenMP" -or $_.Name -like "ggml*.dll"
} | Copy-Item -Destination "$build\llama"
foreach ($dll in "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll") {
    Copy-Item (Join-Path $env:SystemRoot "System32\$dll") "$build\llama"
}

# 2. the back end, frozen with PyInstaller (one folder)
Invoke-Step (Join-Path $root "backend") {
    uv sync --frozen
    uv run --with pyinstaller pyinstaller sniff\desktop.py --noconfirm --clean --onedir --name sniff-backend --paths . `
        --distpath $build --workpath (Join-Path $Cache "pyinstaller\work") --specpath (Join-Path $Cache "pyinstaller") `
        --collect-data rapidocr_onnxruntime --collect-all sqlite_vec --collect-data docx --collect-data pptx
}
# Google's client library ships discovery files for every Google API (tens of MB); Sniff only uses Calendar
Get-ChildItem "$build\sniff-backend\_internal\googleapiclient\discovery_cache\documents" -Exclude "calendar.v3.json" -ErrorAction SilentlyContinue |
    Remove-Item

# 3. the front end and the desktop app; tauri builds the front end first (beforeBuildCommand)
Invoke-Step (Join-Path $root "frontend") { npm ci --no-audit --no-fund }
Invoke-Step (Join-Path $root "desktop") { npm ci --no-audit --no-fund; npx tauri build }

$setup = Get-ChildItem (Join-Path $env:CARGO_TARGET_DIR "release\bundle\nsis\*.exe") | Sort-Object LastWriteTime | Select-Object -Last 1
"Installer: $($setup.FullName) ({0:N1} MB)" -f ($setup.Length / 1MB)
