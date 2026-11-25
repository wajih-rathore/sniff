# Fill the folders sniff-8gb.wsb maps into Windows Sandbox: the installer, smoke.ps1 and 5 dev documents.
#   powershell -ExecutionPolicy Bypass -File scripts\sandbox\prepare.ps1
param(
    [string]$Box = "E:\E\FAST\Semester 7\GenAI\.cache\sandbox",  # must match the HostFolder paths in sniff-8gb.wsb
    [string]$Installer = "",  # default: the newest installer from scripts\build-desktop.ps1
    [string]$Dev = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "data\synth\out\dev")
)
$ErrorActionPreference = "Stop"
if (-not $Installer) {
    $nsis = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) ".cache\cargo-target\release\bundle\nsis"
    $Installer = (Get-ChildItem "$nsis\*-setup.exe" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
}
Remove-Item -Recurse -Force "$Box\in" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$Box\in\docs", "$Box\results" | Out-Null
Copy-Item $Installer "$Box\in\"
Copy-Item (Join-Path $PSScriptRoot "smoke.ps1") "$Box\in\"
# dev split only (test documents stay out): a gas bill, a receipt photo, a certificate, a scanned ID card, a recipe
foreach ($f in "Documents\old\ece95bbc.pdf", "Desktop\IMG_3063.jpg", "Desktop\Print_2020-06-19.pdf", "Downloads\scan_6405.pdf", "Desktop\file_1814.pdf") {
    Copy-Item (Join-Path $Dev $f) "$Box\in\docs\"
}
Get-ChildItem "$Box\in" -Recurse -File | ForEach-Object { "{0,12:N0}  {1}" -f $_.Length, $_.FullName }
