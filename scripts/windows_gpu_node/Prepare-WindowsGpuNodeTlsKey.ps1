param(
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode\config.yaml")
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script as the standard, non-elevated Agent user."
}

$python = Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Agent Python not found. Run the standard-user installer first."
}
$keyPath = & $python -c "from windows_gpu_node.config import load_config; import sys; print(load_config(sys.argv[1]).tls_key_file or '')" $ConfigPath
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($keyPath)) {
    throw "Could not read the configured TLS private-key path."
}
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw "TLS private key does not exist: $keyPath"
}

$icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
$userSid = "*" + $identity.User.Value + ":(R)"
& $icacls $keyPath /inheritance:r /grant:r $userSid | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict the TLS private-key file to the current user."
}
Write-Output "TLS private-key file access restricted to the current standard user."
