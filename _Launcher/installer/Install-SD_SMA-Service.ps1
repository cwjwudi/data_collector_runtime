$ErrorActionPreference = "Stop"

$serviceExe = Join-Path $PSScriptRoot "SD_SMA_Service.exe"
$existing = Get-Service -Name "SD_SMA" -ErrorAction SilentlyContinue
if ($existing) {
    & $serviceExe refresh
}
else {
    & $serviceExe install
}
if ($LASTEXITCODE -ne 0) { throw "Failed to register SD_SMA service." }

& $serviceExe start
if ($LASTEXITCODE -ne 0) { throw "Failed to start SD_SMA service." }
