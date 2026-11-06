param(
    [ValidateSet('Preflight', 'Initialize', 'Start', 'Stop', 'Remove')][string]$Action,
    [string]$ProgramRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ConfigPath = '',
    [string]$ErrorFile = ''
)
$ErrorActionPreference = 'Stop'
trap {
    if ($ErrorFile) {
        [IO.File]::WriteAllText($ErrorFile, $_.Exception.Message, [Text.UTF8Encoding]::new($false))
        exit 1
    }
    throw $_
}
if ($ErrorFile -and (Test-Path -LiteralPath $ErrorFile)) { Remove-Item -LiteralPath $ErrorFile }
$dataRoot = Join-Path $env:ProgramData 'SmartData\SD SMA'
$serviceExe = Join-Path $ProgramRoot '_Service\SD_SMA_DB.exe'
$launcher = Join-Path $ProgramRoot '_Launcher\SD_SMA_Launcher.exe'
$installedConfig = Join-Path $dataRoot 'config\launcher\launcher_config.json'
if (-not $ConfigPath) { $ConfigPath = $installedConfig }
$securityPath = Join-Path $dataRoot 'secrets\launcher_security.json'
$externalMode = $false
if (Test-Path -LiteralPath $securityPath) {
    try { $security = Get-Content -LiteralPath $securityPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw 'Cannot read database selection. Original credentials were retained; repair launcher_security.json before retrying.' }
    if ($security.PSObject.Properties.Name -contains 'database_binding') {
        if ($security.database_binding.mode -notin @('managed', 'external')) { throw 'Invalid database selection. Repair it in SD SMA management before retrying.' }
    }
    $externalMode = $security.database_binding.mode -eq 'external'
}

if ($Action -eq 'Preflight') {
    if (Test-Path -LiteralPath $installedConfig) {
        $existing = Get-Content -LiteralPath $installedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $existing.managed_database.enabled) { throw 'Existing standard installation found. Use a clean machine; migration is not supported.' }
    }
    $launcherService = Get-CimInstance Win32_Service -Filter "Name='SD_SMA'"
    if ($launcherService) {
        if ($launcherService.PathName.Trim('"') -ne (Join-Path $ProgramRoot '_Service\SD_SMA_Service.exe')) {
            throw 'SD_SMA belongs to another installation. Side-by-side installation is not supported.'
        }
        Stop-Service SD_SMA
        (Get-Service SD_SMA).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
}
if ($externalMode -and $Action -in @('Initialize', 'Start')) { exit 0 }
$service = Get-CimInstance Win32_Service -Filter "Name='SD_SMA_DB'"
if ($service -and $service.PathName.Trim('"') -ne $serviceExe) {
    throw 'SD_SMA_DB belongs to another installation. Side-by-side installation is not supported.'
}
if ($Action -eq 'Stop' -or $Action -eq 'Remove' -or $Action -eq 'Preflight') {
    if ($service) {
        Stop-Service -Name SD_SMA_DB -ErrorAction Stop
        (Get-Service SD_SMA_DB).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    if ($Action -eq 'Remove' -and $service) {
        & $serviceExe uninstall
        if ($LASTEXITCODE -ne 0) { throw 'Failed to remove SD_SMA_DB service; database files were retained.' }
    }
    if ($Action -ne 'Preflight') { exit 0 }
}

if ($Action -eq 'Preflight') {
    if ($externalMode) { exit 0 }
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $port = [int]$config.managed_database.port
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $port)
    $listener.ExclusiveAddressUse = $true
    try { $listener.Start() }
    catch { throw "Managed database port $port is occupied." }
    finally { $listener.Stop() }
    exit 0
}

$env:SD_SMA_DATA_ROOT = $dataRoot
if ($Action -eq 'Initialize') {
    & $launcher --config $ConfigPath --initialize-database
    if ($LASTEXITCODE -ne 0) { throw 'Managed database initialization failed. Rerun the installer; existing data and credentials were retained.' }
    if ($service) { & $serviceExe refresh } else { & $serviceExe install }
    if ($LASTEXITCODE -ne 0) { throw 'Failed to register SD_SMA_DB service.' }
}
$status = Get-Service SD_SMA_DB -ErrorAction Stop
if ($status.Status -ne 'Running') { Start-Service SD_SMA_DB }
(Get-Service SD_SMA_DB).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
& $launcher --config $ConfigPath --check-database
if ($LASTEXITCODE -ne 0) { throw 'SD_SMA_DB is running but database login failed. Check database initialization and credentials.' }
