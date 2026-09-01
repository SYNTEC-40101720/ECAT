$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseRoot = "D:\Release\SYNTEC-ECAT-Test"
Set-Location $projectRoot

& (Join-Path $PSScriptRoot "build-backend.ps1")
if ($LASTEXITCODE -ne 0) { throw "Backend build failed." }

if (-not (Test-Path (Join-Path $projectRoot "node_modules\electron-builder"))) {
    throw "electron-builder is missing. Run npm install first."
}
if (Test-Path $releaseRoot) { Remove-Item $releaseRoot -Recurse -Force }
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

npm exec electron-builder -- --win --x64 --config electron-builder.yml --publish never --config.directories.output=$releaseRoot
if ($LASTEXITCODE -ne 0) { throw "Electron builder failed." }
Write-Output "Release output: $releaseRoot"
