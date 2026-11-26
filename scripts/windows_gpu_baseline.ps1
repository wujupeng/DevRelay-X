[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

function Find-Executable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string[]]$FallbackPaths = @()
    )

    $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }

    foreach ($candidate in $FallbackPaths) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return $null
}

function Invoke-ReadOnlyCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    try {
        $output = @(& $Path @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            status   = if ($exitCode -eq 0) { "available" } else { "error" }
            exitCode = $exitCode
            output   = $output
        }
    }
    catch {
        return [pscustomobject]@{
            status   = "error"
            exitCode = $null
            output   = @($_.Exception.Message)
        }
    }
}

$os = $null
$cpu = @()
$memoryBytes = $null
$displayDevices = @()
$cimErrors = @()

try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop |
        Select-Object -First 1 Caption, Version, BuildNumber, OSArchitecture
}
catch {
    $cimErrors += "OperatingSystem: $($_.Exception.Message)"
}

try {
    $cpu = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop |
        ForEach-Object {
            [pscustomobject]@{
                name                       = $_.Name
                numberOfCores              = $_.NumberOfCores
                numberOfLogicalProcessors  = $_.NumberOfLogicalProcessors
            }
        })
}
catch {
    $cimErrors += "Processor: $($_.Exception.Message)"
}

try {
    $memory = @(Get-CimInstance -ClassName Win32_PhysicalMemory -ErrorAction Stop)
    if ($memory.Count -gt 0) {
        $memoryBytes = ($memory | Measure-Object -Property Capacity -Sum).Sum
    }
}
catch {
    $cimErrors += "PhysicalMemory: $($_.Exception.Message)"
}

try {
    $displayDevices = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop |
        ForEach-Object {
            [pscustomobject]@{
                name               = $_.Name
                driverVersion      = $_.DriverVersion
                status             = $_.Status
                adapterRamBytes    = $_.AdapterRAM
                videoProcessor     = $_.VideoProcessor
            }
        })
}
catch {
    $cimErrors += "VideoController: $($_.Exception.Message)"
}

$nvidiaSmiPath = Find-Executable -Name "nvidia-smi.exe" -FallbackPaths @(
    "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
)
$nvidiaSmi = if ($nvidiaSmiPath) {
    [pscustomobject]@{
        version = Invoke-ReadOnlyCommand -Path $nvidiaSmiPath -Arguments @("--version")
        gpus    = Invoke-ReadOnlyCommand -Path $nvidiaSmiPath -Arguments @(
            "--query-gpu=index,name,memory.total,memory.free,driver_version,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits"
        )
    }
}
else {
    [pscustomobject]@{
        version = [pscustomobject]@{ status = "not_installed"; exitCode = $null; output = @() }
        gpus    = [pscustomobject]@{ status = "not_installed"; exitCode = $null; output = @() }
    }
}

$nvccPath = Find-Executable -Name "nvcc.exe"
$cudaToolkit = if ($nvccPath) {
    Invoke-ReadOnlyCommand -Path $nvccPath -Arguments @("--version")
}
else {
    [pscustomobject]@{ status = "not_installed"; exitCode = $null; output = @() }
}

$pythonPath = Find-Executable -Name "python.exe"
$python = if ($pythonPath) {
    Invoke-ReadOnlyCommand -Path $pythonPath -Arguments @("--version")
}
else {
    [pscustomobject]@{ status = "not_installed"; exitCode = $null; output = @() }
}

$torchCuda = if ($pythonPath) {
    $torchProbe = "import importlib.util,json; s=importlib.util.find_spec('torch'); m=__import__('torch') if s else None; print(json.dumps({'status':'not_installed'} if m is None else {'status':'probed','torchVersion':m.__version__,'cudaAvailable':m.cuda.is_available(),'cudaVersion':m.version.cuda,'deviceName':m.cuda.get_device_name(0) if m.cuda.is_available() else None}))"
    Invoke-ReadOnlyCommand -Path $pythonPath -Arguments @("-c", $torchProbe)
}
else {
    [pscustomobject]@{ status = "not_assessed"; exitCode = $null; output = @() }
}

$ollamaPath = Find-Executable -Name "ollama.exe" -FallbackPaths @(
    "C:\Program Files\Ollama\ollama.exe",
    "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
)
$ollama = if ($ollamaPath) {
    [pscustomobject]@{
        version = Invoke-ReadOnlyCommand -Path $ollamaPath -Arguments @("--version")
        models  = Invoke-ReadOnlyCommand -Path $ollamaPath -Arguments @("list")
    }
}
else {
    [pscustomobject]@{
        version = [pscustomobject]@{ status = "not_installed"; exitCode = $null; output = @() }
        models  = [pscustomobject]@{ status = "not_assessed"; exitCode = $null; output = @() }
    }
}

$llamaServerPath = Find-Executable -Name "llama-server.exe"
$llamaCpp = if ($llamaServerPath) {
    Invoke-ReadOnlyCommand -Path $llamaServerPath -Arguments @("--version")
}
else {
    [pscustomobject]@{ status = "not_installed"; exitCode = $null; output = @() }
}

$report = [ordered]@{
    schemaVersion = 1
    collectedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    scope = "local-host-read-only"
    machine = [pscustomobject]@{
        computerName = $env:COMPUTERNAME
        os = $os
        cpu = $cpu
        physicalMemoryBytes = $memoryBytes
        displayDevices = $displayDevices
        cimErrors = $cimErrors
    }
    nvidiaSmi = $nvidiaSmi
    cudaToolkit = $cudaToolkit
    python = $python
    pytorchCudaProbe = $torchCuda
    modelRuntimes = [pscustomobject]@{
        ollama = $ollama
        llamaCppServer = $llamaCpp
    }
    safety = [pscustomobject]@{
        modelDownloaded = $false
        inferenceStarted = $false
        driversChanged = $false
        configurationChanged = $false
    }
}

$json = $report | ConvertTo-Json -Depth 8
if ($OutputPath) {
    $fullPath = [System.IO.Path]::GetFullPath($OutputPath)
    if (Test-Path -LiteralPath $fullPath) {
        throw "Refusing to overwrite existing report: $fullPath"
    }

    $parent = Split-Path -Parent $fullPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Output directory does not exist: $parent"
    }

    Set-Content -LiteralPath $fullPath -Value $json -Encoding UTF8
    Write-Output "Baseline report saved to $fullPath"
}
else {
    Write-Output $json
}
