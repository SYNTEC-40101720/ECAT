param([string]$ReleaseRoot = "D:\Release\SYNTEC-ECAT-Test")
$ErrorActionPreference = "Stop"

$backend = Join-Path $ReleaseRoot "win-unpacked\resources\backend\SYNTEC-ECAT-Test-Backend.exe"
$internal = Join-Path $ReleaseRoot "win-unpacked\resources\backend\_internal"
$electronExe = Join-Path $ReleaseRoot "win-unpacked\SYNTEC-ECAT-Test.exe"
$installer = Get-ChildItem $ReleaseRoot -Filter "SYNTEC-ECAT-Test-Setup-*.exe" -File -ErrorAction SilentlyContinue
if (-not (Test-Path $backend)) { throw "Packaged backend missing: $backend" }
if (-not (Test-Path $internal)) { throw "Backend _internal missing: $internal" }
if (-not (Test-Path $electronExe)) { throw "Packaged Electron executable missing: $electronExe" }
if (-not $installer) { throw "SYNTEC installer named SYNTEC-ECAT-Test-Setup-*.exe was not found in $ReleaseRoot" }

$backendInfo = (Get-Item $backend).VersionInfo
if ($backendInfo.FileVersion -notmatch '^1\.0\.0\.0$' -or $backendInfo.ProductVersion -notmatch '^1\.0\.0\.0') { throw "Backend version metadata is incorrect." }
if ($backendInfo.CompanyName -notmatch 'SYNTEC' -or $backendInfo.LegalCopyright -notmatch 'Copyright . SYNTEC 2026') { throw "Backend SYNTEC metadata is incorrect." }
if (-not (Test-Path (Join-Path $internal "python*.dll"))) { throw "Python runtime DLL missing from _internal." }
if (-not (Test-Path (Join-Path $internal "ESI"))) { throw "Backend ESI resources missing from _internal." }
if (-not (Test-Path (Join-Path $ReleaseRoot "win-unpacked\resources\esi"))) { throw "ESI resources missing." }
$electronInfo = (Get-Item $electronExe).VersionInfo
$metadataFailures = @()
if ($electronInfo.ProductName -notmatch '^SYNTEC') { $metadataFailures += "ProductName='$($electronInfo.ProductName)' does not start with SYNTEC" }
if ($electronInfo.FileVersion -notmatch '^\d+\.\d+\.\d+\.\d+$') { $metadataFailures += "FileVersion='$($electronInfo.FileVersion)' is not four numeric components" }
if ($electronInfo.CompanyName -notmatch 'SYNTEC') { $metadataFailures += "CompanyName='$($electronInfo.CompanyName)' does not contain SYNTEC" }
if ($electronInfo.LegalCopyright -notmatch 'SYNTEC') { $metadataFailures += "LegalCopyright='$($electronInfo.LegalCopyright)' does not contain SYNTEC" }
if ($metadataFailures.Count -gt 0) { throw "Electron EXE metadata validation failed: $($metadataFailures -join '; '). electron-builder did not provide stable SYNTEC metadata." }
Write-Output ("Backend: {0} ({1:N0} bytes)" -f $backend, (Get-Item $backend).Length)
Write-Output ("Electron: {0} ({1:N0} bytes)" -f $electronExe, (Get-Item $electronExe).Length)
Write-Output ("Installer: {0} ({1:N0} bytes)" -f $installer.FullName, $installer.Length)
Write-Output ("Backend metadata: {0}, {1}, {2}" -f $backendInfo.FileVersion, $backendInfo.CompanyName, $backendInfo.LegalCopyright)
Write-Output ("Electron metadata: {0}, {1}, {2}, {3}" -f $electronInfo.ProductName, $electronInfo.FileVersion, $electronInfo.CompanyName, $electronInfo.LegalCopyright)
