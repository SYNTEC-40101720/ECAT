$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseRoot = "D:\Release\SYNTEC-ECAT-Test"
Set-Location $projectRoot

# 前端产物：desktop.spec 要求 webui/dist/index.html 就位
Set-Location (Join-Path $projectRoot "webui")
npm run build
if ($LASTEXITCODE -ne 0) { throw "webui build failed." }
Set-Location $projectRoot

$buildRequirements = Join-Path $projectRoot "packaging\requirements-build.txt"
$pyinstallerVersion = (& py -m PyInstaller --version 2>&1 | Select-Object -Last 1).ToString().Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pyinstallerVersion)) {
    throw "PyInstaller is required. Install the pinned build dependency with: py -m pip install -r `"$buildRequirements`""
}
Write-Output "PyInstaller: $pyinstallerVersion"

$distRoot = Join-Path $projectRoot "build\desktop-dist"
if (Test-Path $distRoot) { Remove-Item $distRoot -Recurse -Force }

py -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $projectRoot "build\desktop-work") (Join-Path $projectRoot "packaging\desktop.spec")
$desktop = Join-Path $distRoot "SYNTEC-ECAT-Test"
if (-not (Test-Path (Join-Path $desktop "SYNTEC-ECAT-Test.exe"))) {
    throw "PyInstaller did not produce the SYNTEC desktop executable."
}

if (Test-Path $releaseRoot) { Remove-Item $releaseRoot -Recurse -Force }
New-Item -ItemType Directory -Path (Split-Path -Parent $releaseRoot) -Force | Out-Null
Copy-Item -Path $desktop -Destination $releaseRoot -Recurse
Write-Output "Release output: $releaseRoot"
