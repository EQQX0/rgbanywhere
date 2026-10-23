# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Removes RGB Anywhere: scheduled tasks, startup entry, shortcuts, the Settings > Apps entry and the install folder.
# Run by Uninstall.exe (elevated, hidden). The PawnIO driver is left alone because other programs can use it.
param([Parameter(Mandatory)][string]$Dir)
$ErrorActionPreference = 'Continue'
$Dir = (Resolve-Path -LiteralPath $Dir).Path.TrimEnd('\')
# Safety: only ever delete a folder named "RGB Anywhere" that holds this app.
if ((Split-Path $Dir -Leaf) -ne 'RGB Anywhere' -or -not (Test-Path (Join-Path $Dir 'rgbd.py'))) { Write-Host "Refusing to delete $Dir"; exit 1 }

# Hand the lights back first, while the daemon still runs: each device gets the lighting it had before RGB Anywhere
# first changed it (original.json), so the vendor's app or the device's own effect takes over again.
$py = Join-Path $Dir 'python\python.exe'
if ((Test-Path $py) -and (Test-Path (Join-Path $Dir 'original.json'))) {
    $p = Start-Process $py -ArgumentList "`"$(Join-Path $Dir 'rgb.py')`" --restore" -WorkingDirectory $Dir -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit(60000)) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Write-Host 'Restoring the original lighting timed out' }
}

foreach ($t in 'RGB Anywhere Lighting', 'RGB Anywhere Rescan') {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ProcessId -ne $PID -and
        $_.ExecutablePath.StartsWith($Dir + '\', [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'RGB Anywhere' -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RGB Anywhere' -Recurse -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'RGB Anywhere.lnk') -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'RGB Anywhere.lnk') -ErrorAction SilentlyContinue

# Uninstall.exe is still running, so delete the folder a moment after it exits.
Start-Process cmd.exe -WindowStyle Hidden -ArgumentList "/c ping -n 4 127.0.0.1 >nul & rmdir /s /q `"$Dir`""
exit 0
