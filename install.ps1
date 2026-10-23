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

# 0. Where things live. The lighting daemon runs this folder's code as admin, so only admins may change it: Setup puts
# it in Program Files. Settings, the app's logs and tokens stay in the user's folder ($data), which the daemon only reads.
$data = Join-Path $env:LOCALAPPDATA 'RGB Anywhere'
$protected = [bool](@($env:ProgramFiles, $env:ProgramW6432) | Where-Object { $_ -and
        $here.StartsWith($_.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) })
function Remove-Tree($path) {
    # rmdir removes a junction itself instead of what it points to, unlike Remove-Item -Recurse in Windows PowerShell
    if (Test-Path -LiteralPath $path) { & cmd.exe /c "rmdir /s /q `"$path`"" }
}
if ($protected) {
    # Owned by Administrators, with Program Files' own permissions (admins and SYSTEM change, users read and run)
    & icacls.exe $here /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
    & icacls.exe $here /reset /T /C /Q | Out-Null
    # An install before 0.3 ran from $data. Keep its settings there, bring what the daemon owns here, remove its code.
    if ((Test-Path (Join-Path $data 'rgbd.py')) -and ($data -ne $here)) {
        Write-Host "Moving the earlier install out of $data (settings stay there)"
        foreach ($f in 'config.json', 'original.json') {
            $src = Join-Path $data $f
            if ((Test-Path -LiteralPath $src) -and -not (Test-Path (Join-Path $here $f))) {
                try { Get-Content -LiteralPath $src -Raw | ConvertFrom-Json | Out-Null; Copy-Item -LiteralPath $src (Join-Path $here $f) }
                catch { Write-Warning "$f from the earlier install is not valid JSON; not copied" }
            }
        }
        foreach ($d in 'python', 'openrgb', 'ui', 'android', 'installer', '__pycache__', 'cache\core') { Remove-Tree (Join-Path $data $d) }
        Get-ChildItem -LiteralPath $data -File -Force | Where-Object {
            $_.Extension -in '.py', '.pyw', '.ps1', '.cmd', '.ico', '.exe' -or
            $_.Name -in 'version.txt', 'requirements.txt', 'LICENSE', 'NOTICE', 'THIRD_PARTY_LICENSES.md', 'README.md',
                        'config.example.json', 'config.json', 'original.json', 'rgbd.token', 'install.log'
        } | Remove-Item -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Warning ("This copy is not in Program Files. The lighting service runs its code as admin from $here, which " +
                   "programs without admin rights can change. That is fine for development; for everyday use install with Setup.")
}

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
# Its own installer is downloaded from the author's release page, never shipped here, and run only if the SHA-256 matches.
$pawnUrl = 'https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe'
$pawnSha = '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032'
function Get-PawnState { $o = (& sc.exe query PawnIO 2>$null) -join ' '; if ($o -match 'STATE\s*:\s*\d+\s+(\w+)') { $Matches[1] } else { 'MISSING' } }
$pawn = Get-PawnState
if ($pawn -eq 'MISSING') {
    $setup = Join-Path $env:TEMP 'PawnIO_setup.exe'
    try {
        Write-Host "PawnIO driver not found; downloading its installer from github.com/namazso/PawnIO.Setup..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $pawnUrl -OutFile $setup -UseBasicParsing
        $got = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $pawnSha) { throw "the download has SHA-256 $got, not the expected $pawnSha, so it was not run" }
        Start-Process $setup -ArgumentList '-install', '-silent' -Wait
    } catch {
        Write-Warning "PawnIO could not be installed ($_). RAM and some motherboard lights need it: install it from https://pawnio.eu. USB devices work without it."
    } finally { Remove-Item $setup -Force -ErrorAction SilentlyContinue }
    $pawn = Get-PawnState
}
if ($pawn -ne 'MISSING' -and $pawn -ne 'RUNNING') {
    & sc.exe start PawnIO | Out-Null
    Start-Sleep -Seconds 1
    $pawn = Get-PawnState
}
switch ($pawn) {
    'RUNNING' { Write-Host "PawnIO driver is running." }
    'MISSING' { Write-Warning "The PawnIO driver is not installed, so RAM and some motherboard lights stay off. USB devices work." }
    default   { Write-Warning "The PawnIO driver is installed but is $pawn. Restart the PC, then press Rescan. If it still does not start, Windows may be blocking it: turn off Memory integrity (Windows Security > Device security > Core isolation), restart, and press Rescan. USB devices work meanwhile." }
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
# a desktop shortcut from an earlier install would still point at the old folder
if ($Desktop -or (Test-Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'RGB Anywhere.lnk'))) { $sc += '--desktop' }
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
Write-Host "Optional per-PC settings (monitor, vendor workarounds, device overrides) go in $here\config.json - see README.md."
