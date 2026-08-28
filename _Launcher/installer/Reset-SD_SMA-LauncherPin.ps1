$ErrorActionPreference = "Stop"

$securityFile = Join-Path $env:ProgramData "SmartData\SD SMA\secrets\launcher_security.json"
if (-not (Test-Path -LiteralPath $securityFile)) {
    Write-Host "Launcher PIN is not configured."
    exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}

$data = Get-Content -LiteralPath $securityFile -Raw -Encoding UTF8 | ConvertFrom-Json
$data.PSObject.Properties.Remove("pin_hash")
$data.PSObject.Properties.Remove("pin_salt")
$json = $data | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($securityFile, $json + "`r`n", [System.Text.UTF8Encoding]::new($false))
Write-Host "Launcher PIN has been reset. Credentials and assignments were preserved."
