# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Builds installer\RGB-Anywhere-Setup.exe (a small downloader/installer). Needs MinGW-w64 GCC (see ..\README.md).
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$winlibs = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'BrechtSanders.WinLibs*' -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path $_.FullName 'mingw64\bin' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($winlibs) { $env:Path = "$winlibs;$env:Path" }
if (-not (Get-Command g++ -ErrorAction SilentlyContinue)) { throw 'g++ not found. Install it with: winget install BrechtSanders.WinLibs.POSIX.UCRT' }
Push-Location $here
try {
    windres setup.rc -O coff -o setup_res.o
    g++ -std=c++17 -O2 -s -static -o RGB-Anywhere-Setup.exe setup.cpp setup_res.o -lwininet -ladvapi32 -lshell32
    if ($LASTEXITCODE) { throw 'g++ failed' }
    Remove-Item setup_res.o
} finally { Pop-Location }
Write-Host "Built $here\RGB-Anywhere-Setup.exe ($([math]::Round((Get-Item "$here\RGB-Anywhere-Setup.exe").Length/1KB)) KB)"
