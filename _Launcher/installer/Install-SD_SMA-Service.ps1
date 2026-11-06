$ErrorActionPreference = "Stop"

$serviceExe = Join-Path $PSScriptRoot "SD_SMA_Service.exe"
$existing = Get-Service -Name "SD_SMA" -ErrorAction SilentlyContinue
if (-not $existing) {
    & $serviceExe install
    if ($LASTEXITCODE -ne 0) { throw "Failed to register SD_SMA service." }
}

# Existing SCM dependencies must be cleared explicitly after removing them from the XML.
& sc.exe config SD_SMA depend= /
if ($LASTEXITCODE -ne 0) { throw "Failed to clear obsolete SD_SMA service dependencies." }

& $serviceExe start
if ($LASTEXITCODE -ne 0) { throw "Failed to start SD_SMA service." }
