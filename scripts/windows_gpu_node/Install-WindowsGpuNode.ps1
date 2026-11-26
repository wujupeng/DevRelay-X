param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode"),
    [string]$PythonExe = "",
    [switch]$RegisterStartupTask
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer as a standard, non-elevated Windows user."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python 3.12+ was not found. Install Python separately; this script does not download it."
    }
    $PythonExe = $pythonCommand.Source
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable not found: $PythonExe"
}

$pythonVersionText = & $PythonExe --version
if ($LASTEXITCODE -ne 0 -or $pythonVersionText -notmatch "^Python (\d+)\.(\d+)") {
    throw "Unable to determine the selected Python version."
}
if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 12)) {
    throw "Python 3.12 or newer is required; found $pythonVersionText."
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $PythonExe -m venv (Join-Path $InstallDir ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the private Python environment."
    }
}

$uvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
if ($null -eq $uvCommand) {
    throw "uv is required to install locked dependencies. Install uv separately, then rerun this script."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "uv.lock") -PathType Leaf)) {
    throw "uv.lock is missing; refusing an unlocked dependency installation."
}
$previousUvEnvironment = [Environment]::GetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", "Process")
try {
    [Environment]::SetEnvironmentVariable(
        "UV_PROJECT_ENVIRONMENT",
        (Join-Path $InstallDir ".venv"),
        "Process"
    )
    & $uvCommand.Source sync --project $projectRoot --frozen --no-dev --python $PythonExe
}
finally {
    [Environment]::SetEnvironmentVariable(
        "UV_PROJECT_ENVIRONMENT",
        $previousUvEnvironment,
        "Process"
    )
}
if ($LASTEXITCODE -ne 0) {
    throw "Could not install WindowsGpuNode from the committed uv.lock."
}
$gitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    throw "Git is required to record the installed Agent commit."
}
$commitSha = & $gitCommand.Source -C $projectRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $commitSha -notmatch "^[0-9a-f]{40}$") {
    throw "Could not determine the checked-out Agent commit SHA."
}
$commitSha | Set-Content -LiteralPath (Join-Path $InstallDir "agent.commit") -NoNewline

$configPath = Join-Path $InstallDir "config.yaml"
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "windows_gpu_node\config.example.yaml") -Destination $configPath
}
Copy-Item -Path (Join-Path $PSScriptRoot "*.ps1") -Destination $InstallDir -Force

$tokenPath = Join-Path $InstallDir "agent.token"
if (-not (Test-Path -LiteralPath $tokenPath)) {
    $randomBytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($randomBytes)
    }
    finally {
        $generator.Dispose()
    }
    $token = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    [IO.File]::WriteAllText($tokenPath, $token, [Text.UTF8Encoding]::new($false))
}

$icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
$tokenSid = "*" + $identity.User.Value + ":(R)"
& $icacls $tokenPath /inheritance:r /grant:r $tokenSid | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict the bearer-token file to the current user."
}

New-Item -ItemType Directory -Path (Join-Path $InstallDir "logs") -Force | Out-Null
Write-Output "WindowsGpuNode installed for the current standard user."
Write-Output "Install directory: $InstallDir"
Write-Output "Configuration: $configPath"
Write-Output "Agent commit: $commitSha"
Write-Output "Bearer token stored with user-only read permissions: $tokenPath"
Write-Output "The service remains loopback-only and uses Mock Runtime."

if ($RegisterStartupTask) {
    & (Join-Path $InstallDir "Register-WindowsGpuNodeStartupTask.ps1") -InstallDir $InstallDir
}
