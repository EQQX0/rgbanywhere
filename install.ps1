# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# RGB Control installer. Run in PowerShell from this folder:  powershell -ExecutionPolicy Bypass -File install.ps1
# Installs Python packages, builds the embedded OpenRGB core if needed, then sets up the login tasks, the startup entry and the Start menu shortcut.
# Asks for admin once (the lighting daemon needs it to reach RAM lights and some boards).
param([switch]$NoStartup)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# Re-launch elevated
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$($MyInvocation.MyCommand.Path)`"")
    if ($NoStartup) { $a += '-NoStartup' }
    Start-Process powershell -Verb RunAs -ArgumentList $a -Wait
    exit
}
$user = "$env:USERDOMAIN\$env:USERNAME"   # the elevated shell is still this user

# 1. Python
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (& py -3 -c "import sys;print(sys.executable)" 2>$null) }
if (-not $py) { throw "Python 3.10+ not found. Install it from python.org (tick 'Add to PATH'), then run this again." }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
Write-Host "Python: $py"
& $py -m pip install --upgrade -r (Join-Path $here 'requirements.txt')
if ($LASTEXITCODE) { throw "pip install failed" }

# 2. Embedded OpenRGB core (rgbcore.dll). Build it from source with build.ps1 if it is not here yet.
if (-not (Test-Path (Join-Path $here 'rgbcore.dll'))) {
    Write-Host "rgbcore.dll not found; building it (needs MinGW-w64 GCC, CMake and Ninja, see README.md)..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'build.ps1')
    if ($LASTEXITCODE) { throw "build.ps1 failed. Install the build tools listed in README.md and run it again." }
}
# RAM and some boards are reached through the PawnIO driver (a separate, signed driver from https://pawnio.eu).
if (-not (Get-Service PawnIO -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:ProgramFiles\PawnIO")) {
    Write-Warning "PawnIO driver not found. RAM and some motherboard lights need it: install it from https://pawnio.eu (or: winget install namazso.PawnIO). USB devices work without it."
}

# 3. Default config.json (only if absent): generic settings, no vendor workarounds
$cfg = Join-Path $here 'config.json'
if (-not (Test-Path $cfg)) { [IO.File]::WriteAllText($cfg, '{ "argb_leds": 60 }', (New-Object Text.UTF8Encoding $false)) }

# 4. Scheduled tasks (elevated, so the lighting daemon can reach RAM/board lights)
function New-RgbTask($name, $arg, $logon, $unlimited) {
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $arg -WorkingDirectory $here
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    if ($unlimited) { $settings.ExecutionTimeLimit = 'PT0S' }  # the daemon runs until logoff
    $p = @{ TaskName = $name; Action = $action; Principal = $principal; Settings = $settings; Force = $true }
    if ($logon) { $t = New-ScheduledTaskTrigger -AtLogOn -User $user; $t.Delay = 'PT15S'; $p.Trigger = $t }
    Register-ScheduledTask @p | Out-Null
    Write-Host "Task '$name' registered"
}
# "RGB Lighting" is the elevated lighting daemon (rgbd.py); "RGB Rescan" asks it to re-detect devices.
New-RgbTask 'RGB Lighting' ('"{0}"' -f (Join-Path $here 'rgbd.py')) $true $true
New-RgbTask 'RGB Rescan'   ('"{0}" --rescan' -f (Join-Path $here 'rgbd.py')) $false $false

# 5. Start the app at login (hidden in the tray) and make the Start menu shortcut
if (-not $NoStartup) {
    $run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    # HKCU of the elevated shell is the same user's hive
    Set-ItemProperty $run -Name 'RGB Control' -Value ('"{0}" "{1}" --tray' -f $pyw, (Join-Path $here 'rgb_app.pyw'))
    Write-Host "Startup entry set"
}
& $py (Join-Path $here 'make_shortcut.py')

Write-Host "`nDone. Start 'RGB Control' from the Start menu, or run:  schtasks /run /tn `"RGB Lighting`""
Write-Host "Optional per-PC settings (monitor, vendor workarounds, device overrides) go in config.json - see README.md."
