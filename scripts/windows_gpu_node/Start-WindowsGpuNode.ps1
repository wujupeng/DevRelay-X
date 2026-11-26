param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode")
)

$ErrorActionPreference = "Stop"
$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$python = Join-Path $InstallDir ".venv\Scripts\python.exe"
$configPath = Join-Path $InstallDir "config.yaml"
$tokenPath = Join-Path $InstallDir "agent.token"
$pidPath = Join-Path $InstallDir "agent.pid"

foreach ($requiredPath in @($python, $configPath, $tokenPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required installation file is missing: $requiredPath"
    }
}

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$existingPid)) {
        throw "The Agent PID file is invalid. Inspect it before removing it: $pidPath"
    }
    $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($null -ne $existingProcess) {
        $existingCommand = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $existingPid"
        if (
            $existingProcess.Path -ne $python `
            -or $null -eq $existingCommand `
            -or $existingCommand.CommandLine -notmatch "(?i)(^|\s)-m\s+windows_gpu_node(\s|$)"
        ) {
            throw "PID $existingPid belongs to another executable; refusing to replace the PID file."
        }
        Write-Output "WindowsGpuNode process is already running (PID $existingPid)."
        exit 0
    }
    Remove-Item -LiteralPath $pidPath
}

$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if ($token.Length -lt 32) {
    throw "Bearer-token file is invalid; run the installer to regenerate it."
}
$tokenEnvVar = & $python -c "from windows_gpu_node.config import load_config; import sys; print(load_config(sys.argv[1]).token_env_var)" $configPath
if ($LASTEXITCODE -ne 0 -or $tokenEnvVar -notmatch "^[A-Z_][A-Z0-9_]*$") {
    throw "Could not read the configured token environment-variable name."
}
$port = & $python -c "from windows_gpu_node.config import load_config; import sys; print(load_config(sys.argv[1]).port)" $configPath
if ($LASTEXITCODE -ne 0 -or $port -notmatch "^\d+$") {
    throw "Could not read the configured Agent port."
}

$previousToken = [Environment]::GetEnvironmentVariable($tokenEnvVar, "Process")
$logDir = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logStamp = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N")
$stdoutPath = Join-Path $logDir "process-$logStamp.stdout.log"
$stderrPath = Join-Path $logDir "process-$logStamp.stderr.log"
$arguments = '-m windows_gpu_node --config "' + $configPath + '"'

try {
    [Environment]::SetEnvironmentVariable($tokenEnvVar, $token, "Process")
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $arguments `
        -WorkingDirectory $InstallDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $process.Id -NoNewline
    $health = $null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $health = Invoke-RestMethod `
                -Method Get `
                -Uri "http://127.0.0.1:$port/api/v1/health" `
                -Headers @{ Authorization = "Bearer $token" } `
                -TimeoutSec 2
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($null -eq $health) {
        throw "Agent process started but its local health check did not succeed; inspect logs."
    }
}
finally {
    [Environment]::SetEnvironmentVariable($tokenEnvVar, $previousToken, "Process")
    $token = $null
}

Write-Output "WindowsGpuNode is healthy (PID $($process.Id), state $($health.state), mock_only=$($health.mock_only))."
