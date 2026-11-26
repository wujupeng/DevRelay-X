param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode")
)

$ErrorActionPreference = "Stop"
$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$pidPath = Join-Path $InstallDir "agent.pid"
$python = Join-Path $InstallDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output "WindowsGpuNode is not running (no PID file)."
    exit 0
}

$agentPid = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$agentPid)) {
    throw "The Agent PID file is invalid. Inspect it before removing it: $pidPath"
}
$process = Get-Process -Id $agentPid -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidPath
    Write-Output "Removed stale WindowsGpuNode PID file."
    exit 0
}
$agentCommand = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $agentPid"
if (
    $process.Path -ne $python `
    -or $null -eq $agentCommand `
    -or $agentCommand.CommandLine -notmatch "(?i)(^|\s)-m\s+windows_gpu_node(\s|$)"
) {
    throw "PID $agentPid belongs to another executable; refusing to stop it."
}

Stop-Process -Id $agentPid
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 250
    if ($null -eq (Get-Process -Id $agentPid -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $pidPath
        Write-Output "WindowsGpuNode stopped."
        exit 0
    }
}
throw "WindowsGpuNode did not stop within 5 seconds; inspect PID $agentPid and its logs."
