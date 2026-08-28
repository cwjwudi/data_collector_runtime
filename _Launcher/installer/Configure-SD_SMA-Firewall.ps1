param(
    [ValidateSet("Global", "Local", "Remove")]
    [string]$Mode = "Global"
)

$ErrorActionPreference = "Stop"
$ruleName = "SD SMA Runtime 8090-8094"

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

if ($Mode -eq "Global") {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description "Allow SD SMA Launcher and service web ports." `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8090-8094 `
        -Profile Any | Out-Null
}
