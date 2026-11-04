$ErrorActionPreference = "Stop"

$programRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $env:ProgramData "SmartData\SD SMA"
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
    $identity = New-Object Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $dataRoot -AclObject $acl
$directories = @(
    "config\launcher", "config\collector", "config\query_web", "config\db_admin",
    "config\report_copy", "logs\launcher", "logs\service", "logs\collector",
    "logs\query_web", "logs\db_admin", "logs\report_copy", "backups", "runtime",
    "state", "secrets", "ImportBox", "logs\database"
)
foreach ($relative in $directories) {
    New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot $relative) | Out-Null
}

function Copy-DefaultsIfEmpty {
    param([string]$Source, [string]$Destination)
    if (-not (Test-Path -LiteralPath $Source)) { return }
    $existing = @(Get-ChildItem -LiteralPath $Destination -Force -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 0) {
        Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
        }
    }
}

$launcherConfig = Join-Path $dataRoot "config\launcher\launcher_config.json"
if (-not (Test-Path -LiteralPath $launcherConfig)) {
    Copy-Item -LiteralPath (Join-Path $programRoot "_Launcher\launcher_config.json") -Destination $launcherConfig
}
foreach ($name in @("collector", "query_web", "db_admin", "report_copy")) {
    Copy-DefaultsIfEmpty `
        -Source (Join-Path $programRoot "config\$name") `
        -Destination (Join-Path $dataRoot "config\$name")
}

Set-Acl -LiteralPath (Join-Path $dataRoot 'secrets') -AclObject $acl
$importBox = Join-Path $dataRoot "ImportBox"
& icacls.exe $importBox /grant:r "*S-1-5-32-545:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to set ImportBox permissions." }

$firewallMode = "Global"
$networkState = Join-Path $dataRoot "state\network.json"
if (Test-Path -LiteralPath $networkState) {
    try {
        $savedNetwork = Get-Content -LiteralPath $networkState -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($savedNetwork.network_mode -eq "local") { $firewallMode = "Local" }
    }
    catch {
        Write-Warning "Invalid network state; using global access defaults."
    }
}
& (Join-Path $PSScriptRoot "Configure-SD_SMA-Firewall.ps1") -Mode $firewallMode
