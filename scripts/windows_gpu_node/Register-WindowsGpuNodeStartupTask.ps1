param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "DevRelayX\WindowsGpuNode"),
    [string]$TaskName = "DevRelayX-WindowsGpuNode"
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script as the standard, non-elevated Agent user."
}

$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$startScript = Join-Path $InstallDir "Start-WindowsGpuNode.ps1"
if (-not (Test-Path -LiteralPath $startScript -PathType Leaf)) {
    throw "Start script not found. Run Install-WindowsGpuNode.ps1 first."
}

$powerShell = Join-Path $PSHOME "powershell.exe"
$action = New-ScheduledTaskAction `
    -Execute $powerShell `
    -Argument ('-NoProfile -File "' + $startScript + '" -InstallDir "' + $InstallDir + '"')
$trigger = New-ScheduledTaskTrigger -AtStartup
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId $identity.Name `
    -LogonType S4U `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $taskPrincipal `
    -Settings $settings `
    -Force | Out-Null
Write-Output "Startup task registered under $($identity.Name) with limited privileges and S4U logon."
Write-Output "No password is stored. If Windows requires elevation or denies S4U, stop and request administrator review."
