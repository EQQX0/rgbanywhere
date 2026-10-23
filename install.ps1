# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# RGB Anywhere installer. Run in PowerShell from this folder:  powershell -ExecutionPolicy Bypass -File install.ps1
# Installs Python packages, fetches the bundled OpenRGB if needed, then sets up the login tasks, the startup entry and the Start menu shortcut.
# Asks for admin once (the lighting daemon needs it to reach RAM lights and some boards).
#   -NoStartup  do not start the app at login
#   -Desktop    put the shortcut on the desktop instead of the Start menu
#   -Quiet      for the Setup exe: no prompts, log to install.log, exit code 1 on failure, start the lighting daemon at the end
param([switch]$NoStartup, [switch]$Desktop, [switch]$Quiet)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# Re-launch elevated
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$($MyInvocation.MyCommand.Path)`"")
    if ($NoStartup) { $a += '-NoStartup' }
    if ($Desktop) { $a += '-Desktop' }
    if ($Quiet) { $a += '-Quiet' }
    Start-Process powershell -Verb RunAs -ArgumentList $a -Wait
    exit
}
if ($Quiet) {
    Start-Transcript -Path (Join-Path $here 'install.log') -Force | Out-Null
    trap { Write-Host "FAILED: $_"; try { Stop-Transcript | Out-Null } catch {}; exit 1 }
}
$user = "$env:USERDOMAIN\$env:USERNAME"   # the elevated shell is still this user

# 1. Python. A release zip carries its own (python\ next to this script) with every package installed; a source checkout uses yours.
$bundled = Join-Path $here 'python\pythonw.exe'
if (Test-Path $bundled) {
    $py = Join-Path $here 'python\python.exe'; $pyw = $bundled
    Write-Host "Using the bundled Python: $py"
} else {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (& py -3 -c "import sys;print(sys.executable)" 2>$null) }
    if (-not $py) { throw "Python 3.10+ not found. Install it from python.org (tick 'Add to PATH'), then run this again." }
    # The Microsoft Store alias (WindowsApps\python.exe) is not a Python, and older versions lack what the app uses
    $ok = (& $py -c "import sys; print(sys.version_info >= (3, 10))" 2>$null)
    if ($ok -ne 'True') { throw "$py is not Python 3.10 or newer. Install Python from python.org (tick 'Add to PATH'), then run this again." }
    $pyw = Join-Path (Split-Path $py) 'pythonw.exe'
    Write-Host "Python: $py"
    & $py -m pip install --upgrade -r (Join-Path $here 'requirements.txt')
    if ($LASTEXITCODE) { throw "pip install failed" }
}

# 2. OpenRGB, the program that talks to the hardware. A release zip has it in openrgb\; a source checkout downloads it.
if (-not (Test-Path (Join-Path $here 'openrgb\OpenRGB.exe'))) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'fetch-openrgb.ps1')
    if ($LASTEXITCODE) { throw "fetch-openrgb.ps1 failed. Check your internet connection and run this again." }
}
# RAM and some boards are reached through the PawnIO driver (a separate, signed driver from https://pawnio.eu).
if (-not (Get-Service PawnIO -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:ProgramFiles\PawnIO")) {
    $ok = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "PawnIO driver not found; installing it with winget (namazso.PawnIO)..."
        try {
            winget install --id namazso.PawnIO -e --silent --accept-package-agreements --accept-source-agreements
            $ok = ($LASTEXITCODE -eq 0) -and ((Get-Service PawnIO -ErrorAction SilentlyContinue) -or (Test-Path "$env:ProgramFiles\PawnIO"))
        } catch { $ok = $false }
    }
    if (-not $ok) {
        Write-Warning "PawnIO driver is not installed. RAM and some motherboard lights need it: install it from https://pawnio.eu (or: winget install namazso.PawnIO). USB devices work without it."
    }
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
# "RGB Anywhere Lighting" is the elevated lighting daemon (rgbd.py); "RGB Anywhere Rescan" asks it to re-detect devices.
New-RgbTask 'RGB Anywhere Lighting' ('"{0}"' -f (Join-Path $here 'rgbd.py')) $true $true
New-RgbTask 'RGB Anywhere Rescan'   ('"{0}" --rescan' -f (Join-Path $here 'rgbd.py')) $false $false

# 5. Start the app at login (hidden in the tray) and make the Start menu shortcut
if (-not $NoStartup) {
    $run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    # HKCU of the elevated shell is the same user's hive
    Set-ItemProperty $run -Name 'RGB Anywhere' -Value ('"{0}" "{1}" --tray' -f $pyw, (Join-Path $here 'rgb_app.pyw'))
    Write-Host "Startup entry set"
}
$sc = @((Join-Path $here 'make_shortcut.py'))
if ($Desktop) { $sc += '--desktop' }
& $py @sc
if ($LASTEXITCODE) { throw "could not create the shortcut" }

# 6. Entry in Settings > Apps (per user). Uninstall.exe is a copy of the Setup exe that the Setup window puts here.
$unexe = Join-Path $here 'Uninstall.exe'
if (Test-Path $unexe) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RGB Anywhere'
    New-Item $key -Force | Out-Null
    $ver = if (Test-Path (Join-Path $here 'version.txt')) { (Get-Content (Join-Path $here 'version.txt') -TotalCount 1).Trim() } else { '' }
    $kb = [int]((Get-ChildItem $here -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1KB)
    $vals = @{
        DisplayName = 'RGB Anywhere'; DisplayVersion = $ver; Publisher = 'EQQX2'; InstallLocation = $here
        DisplayIcon = (Join-Path $here 'rgb.ico'); UninstallString = ('"{0}" /uninstall' -f $unexe)
        URLInfoAbout = 'https://github.com/EQQX0/rgbanywhere'
    }
    foreach ($k in $vals.Keys) { Set-ItemProperty $key -Name $k -Value $vals[$k] }
    foreach ($k in 'NoModify', 'NoRepair') { Set-ItemProperty $key -Name $k -Value 1 -Type DWord }
    Set-ItemProperty $key -Name EstimatedSize -Value $kb -Type DWord
    Write-Host "Added to Settings > Apps"
}

if ($Quiet) {
    Start-ScheduledTask -TaskName 'RGB Anywhere Lighting'   # start the daemon now instead of at the next login
    Write-Host "Done."
    Stop-Transcript | Out-Null
    exit 0
}
$where = if ($Desktop) { 'the desktop' } else { 'the Start menu' }
Write-Host "`nDone. Start 'RGB Anywhere' from $where, or run:  schtasks /run /tn `"RGB Anywhere Lighting`""
Write-Host "Optional per-PC settings (monitor, vendor workarounds, device overrides) go in config.json - see README.md."
