param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode")
)

$ErrorActionPreference = "Stop"
$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$python = Join-Path $InstallDir ".venv\Scripts\python.exe"
$configPath = Join-Path $InstallDir "config.yaml"
$tokenPath = Join-Path $InstallDir "agent.token"
$pidPath = Join-Path $InstallDir "agent.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output "WindowsGpuNode is stopped."
    exit 1
}
$agentPid = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$agentPid)) {
    throw "The Agent PID file is invalid: $pidPath"
}
$process = Get-Process -Id $agentPid -ErrorAction SilentlyContinue
$agentCommand = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $agentPid"
if (
    $null -eq $process `
    -or $process.Path -ne $python `
    -or $null -eq $agentCommand `
    -or $agentCommand.CommandLine -notmatch "(?i)(^|\s)-m\s+windows_gpu_node(\s|$)"
) {
    Write-Output "WindowsGpuNode process is not running."
    exit 1
}

$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
$tokenEnvVar = & $python -c "from windows_gpu_node.config import load_config; import sys; print(load_config(sys.argv[1]).token_env_var)" $configPath
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the Agent configuration."
}
$port = & $python -c "from windows_gpu_node.config import load_config; import sys; print(load_config(sys.argv[1]).port)" $configPath
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the Agent port."
}
$previousToken = [Environment]::GetEnvironmentVariable($tokenEnvVar, "Process")
try {
    [Environment]::SetEnvironmentVariable($tokenEnvVar, $token, "Process")
    $headers = @{ Authorization = "Bearer $token" }
    $health = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:$port/api/v1/health" `
        -Headers $headers `
        -TimeoutSec 3
}
catch {
    Write-Output "WindowsGpuNode process exists but health check failed; inspect its local logs."
    exit 1
}
finally {
    [Environment]::SetEnvironmentVariable($tokenEnvVar, $previousToken, "Process")
    $token = $null
    $headers = $null
}

Write-Output "WindowsGpuNode is healthy (PID $agentPid, commit $($health.agent_commit_sha), state $($health.state), mock_only=$($health.mock_only))."
