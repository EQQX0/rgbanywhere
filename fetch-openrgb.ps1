# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Download the official OpenRGB 1.0 Windows release, check its SHA-256 and unpack it, unmodified, into <Dest> (default: .\openrgb).
# RGB Anywhere starts OpenRGB.exe from there as its own program. Source: https://openrgb.org, commit 81bbe18a84c2e507006f19dd252e397e40a56bfe.
#   powershell -ExecutionPolicy Bypass -File fetch-openrgb.ps1 [-Dest <folder>]
param([string]$Dest)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Dest) { $Dest = Join-Path $here 'openrgb' }
$url  = 'https://codeberg.org/OpenRGB/OpenRGB/releases/download/release_1.0/OpenRGB_1.0_Windows_64_81bbe18.zip'
$hash = '182A52A3C97C4C4AE52C80286B4260C9666C51C3447DBC416EE8945F66192E90'
if (Test-Path (Join-Path $Dest 'OpenRGB.exe')) { Write-Host "OpenRGB already in $Dest"; return }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('openrgb-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'openrgb.zip'
    Write-Host "Downloading OpenRGB 1.0 (about 30 MB) from codeberg.org..."
    Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($got -ne $hash) { throw "OpenRGB download has the wrong checksum ($got). Expected $hash. Nothing was installed." }
    Expand-Archive $zip (Join-Path $tmp 'x') -Force
    $src = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
    if (-not $src -or -not (Test-Path (Join-Path $src.FullName 'OpenRGB.exe'))) { throw 'OpenRGB.exe not found in the download.' }
    New-Item -ItemType Directory -Force $Dest | Out-Null
    Copy-Item (Join-Path $src.FullName '*') $Dest -Recurse -Force
    Write-Host "OpenRGB unpacked to $Dest"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
