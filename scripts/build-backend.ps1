$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$buildRequirements = Join-Path $projectRoot "packaging\requirements-build.txt"
try {
    $pyinstallerVersion = (& py -m PyInstaller --version 2>&1 | Select-Object -Last 1).ToString().Trim()
} catch {
    $pyinstallerVersion = ""
}
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pyinstallerVersion)) {
    throw "PyInstaller is required for the backend build. Install the pinned build dependency with: py -m pip install -r `"$buildRequirements`""
}
Write-Output "PyInstaller: $pyinstallerVersion"

$distRoot = Join-Path $projectRoot "build\backend-dist"
if (Test-Path $distRoot) { Remove-Item $distRoot -Recurse -Force }
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

py -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $projectRoot "build\backend-work") (Join-Path $projectRoot "packaging\backend.spec")
$backend = Join-Path $distRoot "SYNTEC-ECAT-Test-Backend"
if (-not (Test-Path (Join-Path $backend "SYNTEC-ECAT-Test-Backend.exe"))) {
    throw "PyInstaller did not produce the SYNTEC backend executable."
}
Write-Output "Backend build: $backend"
