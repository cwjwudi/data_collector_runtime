param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LauncherArguments)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $elevatedArguments = @(
        '-NoLogo -NoProfile -ExecutionPolicy Bypass -File', ('"{0}"' -f $PSCommandPath)
    )
    foreach ($argument in $LauncherArguments) {
        $elevatedArguments += '"{0}"' -f $argument.Replace('"', '\"')
    }
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $elevatedArguments
    exit
}

$programRoot = Split-Path -Parent $PSScriptRoot
$env:SD_SMA_DATA_ROOT = Join-Path $env:ProgramData "SmartData\SD SMA"
$launcher = Join-Path $programRoot "_Launcher\SD_SMA_Launcher.exe"
$config = Join-Path $env:SD_SMA_DATA_ROOT "config\launcher\launcher_config.json"
Set-Location -LiteralPath (Split-Path -Parent $launcher)
& $launcher --config $config @LauncherArguments
exit $LASTEXITCODE
