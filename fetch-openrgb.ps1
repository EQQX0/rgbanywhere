# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Download the official OpenRGB 1.0 Windows release, check its SHA-256 and unpack it, unmodified, into <Dest> (default: .\openrgb).
# RGB Anywhere starts OpenRGB.exe from there as its own program. Source: https://openrgb.org, commit 81bbe18a84c2e507006f19dd252e397e40a56bfe.
# It writes <Dest>\fetched-from.txt (the URL and SHA-256), so a later change of the pinned build replaces the old one.
#   powershell -ExecutionPolicy Bypass -File fetch-openrgb.ps1 [-Dest <folder>]
param([string]$Dest)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Windows PowerShell 5.1 downloads many times slower while drawing progress
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Dest) { $Dest = Join-Path $here 'openrgb' }
$url  = 'https://codeberg.org/OpenRGB/OpenRGB/releases/download/release_1.0/OpenRGB_1.0_Windows_64_81bbe18.zip'
$hash = '182A52A3C97C4C4AE52C80286B4260C9666C51C3447DBC416EE8945F66192E90'
$stamp = Join-Path $Dest 'fetched-from.txt'
if (Test-Path (Join-Path $Dest 'OpenRGB.exe')) {
    # No stamp: unpacked by an older copy of this script, which fetched this same build.
    if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw) -match $hash) { Write-Host "OpenRGB already in $Dest"; return }
    Write-Host "$Dest holds another OpenRGB build; replacing it."
}
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('openrgb-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'openrgb.zip'
    Write-Host "Downloading OpenRGB 1.0 (about 30 MB) from codeberg.org..."
    Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($got -ne $hash) { throw "OpenRGB download has the wrong checksum ($got). Expected $hash. Nothing was installed." }
    $x = Join-Path $tmp 'x'
    Expand-Archive $zip $x -Force
    # The release zip holds one folder with OpenRGB.exe in it; accept the files at the top as well.
    $exe = Get-ChildItem $x -Recurse -Filter 'OpenRGB.exe' | Sort-Object { $_.FullName.Length } | Select-Object -First 1
    if (-not $exe) { throw 'OpenRGB.exe not found in the download.' }
    New-Item -ItemType Directory -Force $Dest | Out-Null
    Copy-Item (Join-Path $exe.DirectoryName '*') $Dest -Recurse -Force
    Set-Content $stamp "$url`r`nSHA-256 $hash" -Encoding ascii
    Write-Host "OpenRGB unpacked to $Dest"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
