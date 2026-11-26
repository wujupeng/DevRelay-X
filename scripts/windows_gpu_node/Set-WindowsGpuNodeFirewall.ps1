param(
    [string]$PythonExe = "",
    [string]$LocalAddress = "192.168.2.85",
    [string]$RemoteAddress = "192.168.2.3",
    [int]$Port = 8765,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this firewall-only script in an elevated PowerShell session."
}

foreach ($entry in @(@{Name="LocalAddress"; Value=$LocalAddress}, @{Name="RemoteAddress"; Value=$RemoteAddress})) {
    $parsedAddress = $null
    if (-not [System.Net.IPAddress]::TryParse($entry.Value, [ref]$parsedAddress) -or $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw "$($entry.Name) must be a single IPv4 address."
    }
}
if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "Port must be between 1024 and 65535."
}
if ($Port -eq 11434) {
    throw "TCP/11434 is reserved for loopback-only Ollama and must never be opened."
}

$ruleName = "DevRelayX-WindowsGpuNode-HTTPS"
if ($Remove) {
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    Write-Output "Removed firewall rule $ruleName if it existed."
    exit 0
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Agent Python executable not found: $PythonExe"
}
Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Description "HTTPS access to WindowsGpuNode only from the authorized Debian node." `
    -Direction Inbound `
    -Action Allow `
    -Enabled True `
    -Profile Domain,Private `
    -Protocol TCP `
    -LocalAddress $LocalAddress `
    -LocalPort $Port `
    -RemoteAddress $RemoteAddress `
    -Program $PythonExe `
    -EdgeTraversalPolicy Block | Out-Null

$rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction Stop
$addressFilter = $rule | Get-NetFirewallAddressFilter
$portFilter = $rule | Get-NetFirewallPortFilter
if (
    $rule.Action -ne "Allow" `
    -or $portFilter.LocalPort -ne [string]$Port `
    -or $addressFilter.RemoteAddress -notcontains $RemoteAddress `
    -or $addressFilter.LocalAddress -notcontains $LocalAddress
) {
    Remove-NetFirewallRule -DisplayName $ruleName
    throw "Firewall rule verification failed; the rule was removed."
}

Write-Output "Verified firewall rule: $RemoteAddress -> $LocalAddress TCP/$Port, program $PythonExe."
Write-Output "No rule was created for TCP/11434."
Write-Output "Rollback: .\Set-WindowsGpuNodeFirewall.ps1 -Remove"
