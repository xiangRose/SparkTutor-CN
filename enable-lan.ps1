#Requires -RunAsAdministrator
param(
    [ValidateRange(1, 65535)][int]$Port = 8888,
    [string]$InterfaceAlias = 'WLAN'
)

$ErrorActionPreference = 'Stop'
$webProgram = Join-Path $PSScriptRoot '.web-runtime/python.exe'
if (-not (Test-Path -LiteralPath $webProgram)) {
    $webProgram = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $webProgram)) { throw 'Cannot find the project Python interpreter.' }
$ruleName = "SparkTutor-Web-LAN-$Port"
$existingRule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Program $webProgram | Out-Null
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName "SparkTutor Web LAN (TCP $Port)" -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Program $webProgram | Out-Null
}
Write-Output "SparkTutor TCP $Port is allowed from the local subnet on $InterfaceAlias."
