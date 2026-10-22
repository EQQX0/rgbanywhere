# Build the embedded OpenRGB device core (rgbcore.dll) from source and put it, with its runtime files, next to the app.
# Everything it compiles is in this repository: core\ (our code) and third_party\openrgb\ (OpenRGB release_1.0,
# commit 81bbe18a84c2e507006f19dd252e397e40a56bfe). That is the complete corresponding source of the DLL.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1            build the DLL
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Android   also build the Android app (android\build.ps1)
#
# Needs: a MinGW-w64 GCC (winget install BrechtSanders.WinLibs.POSIX.UCRT), CMake and Ninja. Python's `pip install cmake ninja` works.
# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
param([switch]$Android, [switch]$Clean)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found. $hint" }
}
# WinLibs installed with `winget --scope user` is not on PATH until you open a new shell; look for it.
$winlibs = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'BrechtSanders.WinLibs*' -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path $_.FullName 'mingw64\bin' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($winlibs) { $env:Path = "$winlibs;$env:Path" }
$pyScripts = & python -c "import sysconfig;print(sysconfig.get_path('scripts'))" 2>$null
if ($pyScripts) { $env:Path = "$pyScripts;$env:Path" }

Need g++   'Install it with: winget install BrechtSanders.WinLibs.POSIX.UCRT'
Need cmake 'Install it with: python -m pip install cmake'
Need ninja 'Install it with: python -m pip install ninja'

$src = Join-Path $here 'core'
$out = Join-Path $here 'build'
if ($Clean -and (Test-Path $out)) { Remove-Item $out -Recurse -Force }
cmake -S $src -B $out -G Ninja -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE) { throw 'cmake configure failed' }
cmake --build $out -j
if ($LASTEXITCODE) { throw 'build failed' }

# Runtime files the app loads from its own folder: the DLL, the DLLs it links, and the PawnIO modules.
$or = Join-Path $here 'third_party\openrgb\dependencies'
Copy-Item "$out\rgbcore.dll" $here -Force
Copy-Item "$or\PawnIO\PawnIOLib.dll", "$or\hidapi-hotplug-win\x64\hidapi-hotplug.dll", "$or\libusb-1.0.27\VS2019\MS64\dll\libusb-1.0.dll" $here -Force
Copy-Item "$or\PawnIO\modules\*.bin" $here -Force
$pth = Join-Path (Split-Path (Get-Command g++).Source) 'libwinpthread-1.dll'  # MinGW-w64 threading runtime (MIT/ZPL)
if (Test-Path $pth) { Copy-Item $pth $here -Force }
Write-Host "Built rgbcore.dll and copied its runtime files into $here"

if ($Android) { & (Join-Path $here 'android\build.ps1') }
