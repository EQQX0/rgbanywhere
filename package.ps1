# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Build a ready-to-run release zip: dist\RGBAnywhere-<version>-win64.zip
#   powershell -ExecutionPolicy Bypass -File package.ps1 [-Version 0.1.0] [-SkipBuild]
# The zip holds the app, rgbcore.dll (built by build.ps1), the runtime DLLs, and its own Python with every package
# installed, so the target PC needs nothing but Windows 10/11 (and the PawnIO driver for RAM/board lights).
# Extract it to a permanent folder and run Install.cmd.
param([string]$Version = '0.1.0', [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SkipBuild) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'build.ps1')
    if ($LASTEXITCODE) { throw 'build.ps1 failed' }
}
$pyver = (& python -c "import sys;print('%d.%d.%d' % sys.version_info[:3])")
$minor = ($pyver -split '\.')[0..1] -join ''
$name  = "RGBAnywhere-$Version-win64"
$dist  = Join-Path $here 'dist'
$stage = Join-Path $dist $name
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage, "$stage\python" | Out-Null

# 1. Embeddable Python of the same version as the local one, so the pip-installed packages match it
$embed = Join-Path $dist "python-$pyver-embed-amd64.zip"
if (-not (Test-Path $embed)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip" -OutFile $embed -UseBasicParsing
}
Expand-Archive $embed "$stage\python" -Force
$site = "$stage\python\Lib\site-packages"
& python -m pip install --quiet --no-warn-script-location --target $site -r (Join-Path $here 'requirements.txt')
if ($LASTEXITCODE) { throw 'pip install failed' }
# The embeddable Python ignores .pth files, so name every search path here. ".." is the app folder.
[IO.File]::WriteAllText("$stage\python\python$minor._pth", ("python$minor.zip`r`n.`r`n..`r`nLib\site-packages`r`nLib\site-packages\win32`r`nLib\site-packages\win32\lib`r`nimport site`r`n"))
Copy-Item "$site\pywin32_system32\*.dll" "$stage\python" -Force   # pywintypes/pythoncom DLLs: found next to python.exe

# 2. App, core and runtime files
$files = '*.py','*.pyw','rgbcore.dll','PawnIOLib.dll','hidapi-hotplug.dll','libusb-1.0.dll','libwinpthread-1.dll','*.bin',
         'rgb.ico','config.example.json','requirements.txt','install.ps1','LICENSE','NOTICE','THIRD_PARTY_LICENSES.md','README.md'
foreach ($f in $files) { Copy-Item (Join-Path $here $f) $stage -Force }
Copy-Item (Join-Path $here 'ui') $stage -Recurse -Force
[IO.File]::WriteAllText("$stage\Install.cmd", "@echo off`r`ncd /d `"%~dp0`"`r`npowershell -NoProfile -ExecutionPolicy Bypass -File install.ps1`r`npause`r`n")
[IO.File]::WriteAllText("$stage\SOURCE.txt", @"
RGB Anywhere $Version. Corresponding source (GPL): https://github.com/EQQX0/rgbanywhere  (tag v$Version)
It includes OpenRGB (GPL-2.0-or-later) in third_party/openrgb; rgbcore.dll is built from that source with build.ps1.
Python $pyver (python\) is the unmodified embeddable build from python.org. Package licenses: python\Lib\site-packages\*.dist-info.
"@)

# 3. Zip and checksum
$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
(Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() + "  $name.zip" | Set-Content (Join-Path $dist "$name.zip.sha256") -Encoding ascii
# Fixed-name copy: RGB-Anywhere-Setup.exe downloads releases/latest/download/RGBAnywhere-win64.zip
$latest = Join-Path $dist 'RGBAnywhere-win64.zip'
Copy-Item $zip $latest -Force
(Get-FileHash $latest -Algorithm SHA256).Hash.ToLower() + "  RGBAnywhere-win64.zip" | Set-Content "$latest.sha256" -Encoding ascii
Write-Host "Built $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)"
