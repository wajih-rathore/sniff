# Install and run Sniff on a clean Windows (Windows Sandbox, see sniff-8gb.wsb), timing every step.
# Writes results.json and screenshots to the results folder. Windows PowerShell 5.1 (the sandbox has no pwsh).
#   1 silent per-user install  2 start Sniff  3 first-run model download  4 index 5 documents
#   5 one search and one ask   6 quit, and check that no process is left
# -SkipInstall runs steps 2-6 against an installed Sniff, to check the script outside the sandbox.
param(
    [string]$In = "C:\sniff-in",
    [string]$Results = "C:\sniff-results",
    [string]$Question = "How much is my gas bill?",
    [string]$Query = "gas bill",
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class Win {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public static IntPtr Find(uint pid, string cls, bool visible) { IntPtr r = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); var sb = new StringBuilder(256); GetClassName(h, sb, 256); if (p == pid && sb.ToString() == cls && (!visible || IsWindowVisible(h))) r = h; return true; }, IntPtr.Zero); return r; }
}
"@

$api = "http://127.0.0.1:8765"
$app = Join-Path $env:LOCALAPPDATA "Sniff\sniff.exe"
$clock = [Diagnostics.Stopwatch]::StartNew()
$r = [ordered]@{ started = (Get-Date).ToString("s"); machine = @{}; steps = [ordered]@{}; failures = @(); done = $false }
$os = Get-CimInstance Win32_OperatingSystem
$r.machine = @{ ram_gb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1); os = $os.Caption + " " + $os.BuildNumber
                cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name; gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object Name) }
New-Item -ItemType Directory -Force $Results | Out-Null

function Save { $r | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Results "results.json") -Encoding UTF8 }
function Note([string]$step, $data) { $data.at_s = [math]::Round($clock.Elapsed.TotalSeconds, 1); $r.steps[$step] = $data; Write-Host "$step $($data | ConvertTo-Json -Compress)"; Save }
function Fail([string]$step, $err) { $r.failures += @{ step = $step; error = "$err"; at_s = [math]::Round($clock.Elapsed.TotalSeconds, 1) }; Write-Host "FAILED $step : $err"; Save }
function Shot([string]$name) {
    if ($SkipInstall) { return }  # outside the sandbox the screen is someone's own desktop
    try {
        $b = [Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object Drawing.Bitmap $b.Width, $b.Height
        [Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Location, [Drawing.Point]::Empty, $b.Size)
        $bmp.Save((Join-Path $Results "$name.png"), [Drawing.Imaging.ImageFormat]::Png)
    } catch { Fail "screenshot $name" $_ }
}
function Wait-Until([scriptblock]$ok, [int]$seconds) {
    $t = [Diagnostics.Stopwatch]::StartNew()
    while ($t.Elapsed.TotalSeconds -lt $seconds) { try { $v = & $ok; if ($v) { return $v } } catch {}; Start-Sleep 2 }
    throw "timed out after $seconds s"
}
function Tree([int]$root) {  # a process and everything it started, down the tree (WebView2, llama-server, back end)
    $all = @(Get-CimInstance Win32_Process)
    $ids = @($root); $i = 0
    while ($i -lt $ids.Count) { $ids += @($all | Where-Object { $_.ParentProcessId -eq $ids[$i] } | ForEach-Object { [int]$_.ProcessId }); $i++ }
    $ids
}
function Free-MB { [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB) }

try {
    # 1. silent per-user install
    if (-not $SkipInstall) {
        $exe = Get-ChildItem (Join-Path $In "*-setup.exe") | Select-Object -First 1
        $t = [Diagnostics.Stopwatch]::StartNew()
        Start-Process $exe.FullName -ArgumentList "/S" -Wait
        if (-not (Test-Path $app)) { throw "sniff.exe not found after install" }
        Note "1_install" @{ seconds = [math]::Round($t.Elapsed.TotalSeconds, 1); installer_mb = [math]::Round($exe.Length / 1MB, 1)
                           installed_mb = [math]::Round((Get-ChildItem (Split-Path $app) -Recurse -File | Measure-Object Length -Sum).Sum / 1MB) }
    }

    # 2. start Sniff and wait for its back end
    $t = [Diagnostics.Stopwatch]::StartNew()
    Start-Process $app
    $setup = Wait-Until { Invoke-RestMethod "$api/setup" -TimeoutSec 5 } 300
    Note "2_start" @{ backend_ready_s = [math]::Round($t.Elapsed.TotalSeconds, 1); models_on_disk = $setup.ready; servers_answer = $setup.servers }
    Start-Sleep 3
    Shot "1-first-start"

    # 3. first-run model download (the same call the Download button makes), then wait for the embedding server (the chat model is not downloaded: AI is off)
    $t = [Diagnostics.Stopwatch]::StartNew()
    if (-not $setup.ready -and -not $setup.servers) {  # model servers that already answer are reused, as the app does
        Invoke-RestMethod "$api/setup/download" -Method Post | Out-Null
        Start-Sleep 20
        Shot "2-downloading"
        do {  # up to 2 hours; network errors are retried by the back end and shown here
            Start-Sleep 5
            $s = Invoke-RestMethod "$api/setup" -TimeoutSec 10
            $have = ($s.models | Measure-Object have -Sum).Sum
            Write-Host ("download: {0:N0} MB {1}" -f ($have / 1MB), $s.error)
            if (-not $s.running -and -not $s.ready) { throw "download stopped: $($s.error)" }
        } until ($s.ready -or $t.Elapsed.TotalHours -gt 2)
        if (-not $s.ready) { throw "download not finished after 2 hours" }
    }
    $downloaded = $t.Elapsed.TotalSeconds
    Wait-Until { (Invoke-RestMethod "$api/setup" -TimeoutSec 10).servers } 600 | Out-Null
    Wait-Until { (Invoke-WebRequest "http://127.0.0.1:8081/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } 600 | Out-Null
    Note "3_models" @{ download_s = [math]::Round($downloaded, 1); models_ready_s = [math]::Round($t.Elapsed.TotalSeconds, 1)
                       model_mb = [math]::Round(((Invoke-RestMethod "$api/setup").models | Measure-Object size -Sum).Sum / 1MB); free_mb = Free-MB }
    Shot "3-models-ready"

    # 4. index the mapped folder of dev documents
    $docs = Join-Path $In "docs"
    $t = [Diagnostics.Stopwatch]::StartNew()
    Invoke-RestMethod "$api/index" -Method Post -ContentType "application/json" -Body (@{ folder = $docs } | ConvertTo-Json) | Out-Null
    Start-Sleep 2
    # running ends when the files are findable; queued reaches 0 when the background worker has read their facts
    $st = Wait-Until { $s = Invoke-RestMethod "$api/index/status"; if (-not $s.running -and $s.queued -eq 0) { $s } } 3600
    $found = @(Invoke-RestMethod "$api/documents") | ForEach-Object { $_ } | ForEach-Object { @{ name = $_.name; type = $_.doc_type; facts = @($_.facts).Count } }
    Note "4_index" @{ seconds = [math]::Round($t.Elapsed.TotalSeconds, 1); files = $st.total; errors = $st.errors; documents = @($found); free_mb = Free-MB }

    # 5. one search and one ask, timed
    $t = [Diagnostics.Stopwatch]::StartNew()
    $hits = @(Invoke-RestMethod "$api/search?q=$([uri]::EscapeDataString($Query))&k=5" -TimeoutSec 600) | ForEach-Object { $_ }
    $searchMs = [math]::Round($t.Elapsed.TotalMilliseconds)
    $t = [Diagnostics.Stopwatch]::StartNew()
    $a = Invoke-RestMethod "$api/ask" -Method Post -ContentType "application/json" -Body (@{ question = $Question } | ConvertTo-Json) -TimeoutSec 900
    Note "5_search_ask" @{ query = $Query; search_ms = $searchMs; top_hits = @($hits | Select-Object -First 3 | ForEach-Object { $_.name })
                           question = $Question; ask_ms = [math]::Round($t.Elapsed.TotalMilliseconds); route = $a.route; answer = $a.answer
                           time_to_first_answer_s = [math]::Round($clock.Elapsed.TotalSeconds, 1); free_mb = Free-MB }
    # the same question typed into the launcher, for a screenshot (best effort: the window must take the focus).
    # Not with -SkipInstall: outside the sandbox the model servers may be shared, so ask only once there.
    if (-not $SkipInstall) { try {
        [Windows.Forms.SendKeys]::SendWait("%+ ")  # Alt+Shift+Space shows the launcher
        Start-Sleep 2
        [Windows.Forms.SendKeys]::SendWait($Question)
        Start-Sleep 60
        Shot "4-answer"
    } catch { Fail "launcher screenshot" $_ } }

    # peak memory of Sniff and every process it started, by name, before quitting
    $p = Get-Process sniff | Select-Object -First 1
    $mine = Tree $p.Id
    $peak = @{}
    Get-Process -Id $mine -ErrorAction SilentlyContinue | Group-Object ProcessName | ForEach-Object {
        $peak[$_.Name] = [math]::Round(($_.Group | Measure-Object PeakWorkingSet64 -Sum).Sum / 1MB) }
    $r.peak_working_set_mb = $peak
    $r.peak_working_set_total_mb = ($peak.Values | Measure-Object -Sum).Sum
    Save

    # 6. quit from the tray menu: post the tray window the shell's right-click message, then Down x3 (Open, Settings, Quit) and Enter
    $tray = [Win]::Find([uint32]$p.Id, "tray_icon_app", $false)
    [void][Win]::PostMessage($tray, 6002, [IntPtr]::Zero, [IntPtr]0x0205)
    $menu = Wait-Until { $m = [Win]::Find([uint32]$p.Id, "#32768", $true); if ($m -ne [IntPtr]::Zero) { $m } } 20
    Start-Sleep 1
    1..3 | ForEach-Object { [void][Win]::PostMessage($menu, 0x0100, [IntPtr]0x28, [IntPtr]::Zero); Start-Sleep -Milliseconds 150 }
    [void][Win]::PostMessage($menu, 0x0100, [IntPtr]0x0D, [IntPtr]::Zero)
    $t = [Diagnostics.Stopwatch]::StartNew()
    do { Start-Sleep 1; $left = @(Get-Process -Id $mine -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $($_.Id)" }) } until (-not $left.Count -or $t.Elapsed.TotalSeconds -gt 20)
    Note "6_quit" @{ processes_left = $left }
    if ($left.Count) { Fail "6_quit" "processes still running: $($left -join ', ')" }
    $r.done = $true
} catch {
    Fail "stopped" $_
}
$r.total_s = [math]::Round($clock.Elapsed.TotalSeconds, 1)
Save
Write-Host "Results in $Results"
