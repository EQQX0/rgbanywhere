# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Removes RGB Anywhere: scheduled tasks, startup entry, shortcuts, the Settings > Apps entry and the install folder.
# Run by Uninstall.exe (elevated, hidden). The PawnIO driver is left alone because other programs can use it.
param([Parameter(Mandatory)][string]$Dir)
$ErrorActionPreference = 'Continue'
$Dir = (Resolve-Path -LiteralPath $Dir).Path.TrimEnd('\')
# Safety: only ever delete a folder named "RGB Anywhere" that holds this app.
if ((Split-Path $Dir -Leaf) -ne 'RGB Anywhere' -or -not (Test-Path (Join-Path $Dir 'rgbd.py'))) { Write-Host "Refusing to delete $Dir"; exit 1 }

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
