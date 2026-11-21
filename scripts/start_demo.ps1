# Transtudy - stage start: today's review, server, DEMO window, Chrome, side-by-side layout.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 [-Port 8000]
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
Set-Location $Root

# 1) today's review for today's date (no Gemini)
& $Py scripts\seed_demo.py --today

# 2) server window (skipped if the port is already in use)
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $busy) {
  Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Transtudy SERVER - do not close'; Set-Location '$Root'; & '$Py' -m uvicorn app.main:app --port $Port"
}

# 3) DEMO window with short commands: inject, attack, reset
$demoCmd = @"
`$Host.UI.RawUI.WindowTitle='Transtudy DEMO'; Set-Location '$Root'; .\.venv\Scripts\Activate.ps1;
function inject { python scripts\injection_demo.py $Port };
function attack { python scripts\red_team_demo.py };
function reset { python scripts\seed_demo.py --reset };
Write-Host 'Commands: inject, attack, reset'
"@
Start-Process powershell -ArgumentList "-NoExit", "-Command", $demoCmd

# 4) wait for the server, then open Chrome in a new window
$url = "http://localhost:$Port"
for ($i = 0; $i -lt 60; $i++) {
  try { Invoke-WebRequest -UseBasicParsing "$url/api/settings" -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
}
Start-Process "chrome.exe" -ArgumentList "--new-window", $url
Start-Sleep -Seconds 3

# 5) layout: Chrome left 68%, server log right 32%, DEMO window behind
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class Win {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int ht, bool r);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public static IntPtr Find(string part) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, p) => { if (!IsWindowVisible(h)) return true; var sb = new StringBuilder(512); GetWindowText(h, sb, 512);
      if (sb.ToString().Contains(part)) { found = h; return false; } return true; }, IntPtr.Zero);
    return found;
  }
}
"@
[Win]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$leftW = [int]($wa.Width * 0.68)
$chrome = [Win]::Find("Transtudy - ")
if ($chrome -eq [IntPtr]::Zero) { $chrome = [Win]::Find("Transtudy") }
$server = [Win]::Find("Transtudy SERVER")
$demo = [Win]::Find("Transtudy DEMO")
if ($chrome -ne [IntPtr]::Zero) { [Win]::MoveWindow($chrome, $wa.X, $wa.Y, $leftW, $wa.Height, $true) | Out-Null }
if ($server -ne [IntPtr]::Zero) { [Win]::MoveWindow($server, $wa.X + $leftW, $wa.Y, $wa.Width - $leftW, $wa.Height, $true) | Out-Null }
if ($demo -ne [IntPtr]::Zero) { [Win]::SetWindowPos($demo, [IntPtr]1, $wa.X, $wa.Y, $leftW, $wa.Height, 0x0010) | Out-Null }
Write-Host "Transtudy is running at $url"
