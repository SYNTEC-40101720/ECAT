param([string]$ReleaseRoot = "D:\Release\SYNTEC-ECAT-Test")
$ErrorActionPreference = "Stop"

$desktopExe = Join-Path $ReleaseRoot "SYNTEC-ECAT-Test.exe"
$internal = Join-Path $ReleaseRoot "_internal"
if (-not (Test-Path $desktopExe)) { throw "Desktop executable missing: $desktopExe" }
if (-not (Test-Path $internal)) { throw "_internal directory missing: $internal" }
if (-not (Test-Path (Join-Path $internal "webui\dist\index.html"))) { throw "webui/dist assets missing from _internal." }
if (-not (Test-Path (Join-Path $internal "ESI"))) { throw "ESI resources missing from _internal." }
if (-not (Test-Path (Join-Path $internal "python*.dll"))) { throw "Python runtime DLL missing from _internal." }

$exeInfo = (Get-Item $desktopExe).VersionInfo
if ($exeInfo.FileVersion -notmatch '^1\.0\.0\.0$') { throw "FileVersion metadata is incorrect: '$($exeInfo.FileVersion)'" }
if ($exeInfo.CompanyName -notmatch 'SYNTEC') { throw "CompanyName metadata is incorrect: '$($exeInfo.CompanyName)'" }
if ($exeInfo.LegalCopyright -notmatch 'SYNTEC') { throw "LegalCopyright metadata is incorrect: '$($exeInfo.LegalCopyright)'" }
if ($exeInfo.ProductName -notmatch '^SYNTEC') { throw "ProductName metadata is incorrect: '$($exeInfo.ProductName)'" }

Write-Output ("Desktop: {0} ({1:N0} bytes)" -f $desktopExe, (Get-Item $desktopExe).Length)
Write-Output ("Metadata: FileVersion={0} CompanyName={1} ProductName={2}" -f $exeInfo.FileVersion, $exeInfo.CompanyName, $exeInfo.ProductName)
