# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
# Makes a Setup exe that carries the app zip inside it, so installing needs no download:
#   [Setup stub][zip][64 hex chars of the zip's SHA-256][zip size, 8 bytes little-endian]["RGBAPAY1"]
# Needs MinGW-w64 GCC through build.ps1. Usage: bundle.ps1 -Zip <app.zip> -Out <Setup.exe>
param([Parameter(Mandatory)][string]$Zip, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'build.ps1')
if ($LASTEXITCODE) { throw 'build.ps1 failed' }
$stub = Join-Path $here 'RGB-Anywhere-Setup.exe'
$sha = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $Zip).Length
$fs = [IO.File]::Create($Out)
try {
    foreach ($part in $stub, $Zip) {
        $in = [IO.File]::OpenRead($part)
        try { $in.CopyTo($fs, 1MB) } finally { $in.Dispose() }
    }
    $tail = [Text.Encoding]::ASCII.GetBytes($sha) + [BitConverter]::GetBytes([int64]$size) + [Text.Encoding]::ASCII.GetBytes('RGBAPAY1')
    $fs.Write($tail, 0, $tail.Length)
} finally { $fs.Dispose() }
Write-Host "Built $Out ($([math]::Round((Get-Item $Out).Length/1MB,1)) MB, zip inside)"
