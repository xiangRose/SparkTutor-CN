param(
    [ValidateRange(1, 65535)][int]$Port = 8888,
    [string]$ListenAddress = '0.0.0.0',
    [string]$PythonPath = '',
    [string]$DataDir = '',
    [switch]$DryRun,
    [switch]$TrustedLocal,
    [switch]$IsolatedExecution,
    [ValidateSet('Auto', 'Windows', 'WSL')][string]$Runtime = 'Auto'
)

$ErrorActionPreference = 'Stop'
if ($IsolatedExecution -and ($TrustedLocal -or $DryRun)) { throw '-IsolatedExecution cannot be combined with -TrustedLocal or -DryRun.' }
if ($TrustedLocal) {
    if ($DryRun) { throw '-TrustedLocal cannot be combined with -DryRun.' }
    if (-not $PSBoundParameters.ContainsKey('ListenAddress')) { $ListenAddress = '127.0.0.1' }
    $trustedAddress = $null
    $trustedLoopback = $ListenAddress -eq 'localhost' -or (
        [Net.IPAddress]::TryParse($ListenAddress, [ref]$trustedAddress) -and
        $trustedAddress.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
        [Net.IPAddress]::IsLoopback($trustedAddress)
    )
    if (-not $trustedLoopback) { throw '-TrustedLocal only accepts localhost or an IPv4 loopback address.' }
}
Set-Location -LiteralPath $PSScriptRoot
$runtimeConfigurationPath = Join-Path $PSScriptRoot '.sparktutor-web/runtime.json'
$runtimeConfiguration = $null
if (-not $PythonPath -and (Test-Path -LiteralPath $runtimeConfigurationPath -PathType Leaf)) {
    try {
        $runtimeConfiguration = Get-Content -LiteralPath $runtimeConfigurationPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw 'The saved runtime configuration in .sparktutor-web/runtime.json is invalid. Correct it or use an explicit -Runtime and -PythonPath.'
    }
}
$selectedRuntime = $Runtime
if ($Runtime -eq 'Auto') {
    $selectedRuntime = if (-not $PythonPath -and $runtimeConfiguration.runtime -eq 'wsl') { 'WSL' } else { 'Windows' }
}
if ($selectedRuntime -eq 'WSL') {
    $wslOptions = @{Port=$Port; ListenAddress=$ListenAddress; DryRun=$DryRun; TrustedLocal=$TrustedLocal}
    $wslFields = @{
        wslDistribution='Distribution'; wslPythonPath='PythonPath'; wslProjectPath='ProjectPath';
        wslDockerHost='DockerHost'; wslDockerExecutable='DockerExecutable'; wslDockerService='DockerService';
        wslExecutable='WslExecutable'; executorImage='ExecutorImage'
    }
    foreach ($field in $wslFields.Keys) {
        if ($runtimeConfiguration -and $runtimeConfiguration.PSObject.Properties.Name -contains $field) {
            if ($runtimeConfiguration.$field -isnot [string]) { throw "The runtime setting $field must be a string." }
            $wslOptions[$wslFields[$field]] = $runtimeConfiguration.$field
        }
    }
    if ($PythonPath) { $wslOptions.PythonPath = $PythonPath }
    if ($DataDir) { $wslOptions.DataDir = $DataDir }
    & (Join-Path $PSScriptRoot 'start-web-wsl.ps1') @wslOptions
    exit $LASTEXITCODE
}
if (-not $PythonPath -and $runtimeConfiguration -and $runtimeConfiguration.PSObject.Properties.Name -contains 'pythonPath') {
    if ($runtimeConfiguration.pythonPath -isnot [string] -or [string]::IsNullOrWhiteSpace($runtimeConfiguration.pythonPath)) {
        throw 'The saved pythonPath must be a non-empty string.'
    }
    $PythonPath = $runtimeConfiguration.pythonPath
}
$candidates = @(
    (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'),
    (Join-Path $PSScriptRoot '.web-runtime/python.exe')
)
$systemPython = Get-Command python -ErrorAction SilentlyContinue
if ($systemPython) { $candidates += $systemPython.Source }
if ($PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'The selected Python executable does not exist.' }
    $candidates = @((Resolve-Path -LiteralPath $PythonPath).Path)
}
$previousSparkTutorPythonPath = $env:PYTHONPATH
$projectPythonSource = Join-Path $PSScriptRoot 'src'
try {
    $env:PYTHONPATH = if ($previousSparkTutorPythonPath) { $projectPythonSource + [IO.Path]::PathSeparator + $previousSparkTutorPythonPath } else { $projectPythonSource }
    $webPython = $null
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            & $candidate -c 'import sys; assert sys.version_info >= (3, 10); import sparktutor.web.server' 2>$null
            if ($LASTEXITCODE -eq 0) { $webPython = $candidate; break }
        }
    }
    if (-not $webPython) {
        throw 'Python 3.10+ and project dependencies are required. Create .venv and run: pip install -e ".[dev]"'
    }
    Write-Output ("Using Python: " + $webPython)
    if ($DryRun) {
        Write-Output 'Syntax-check mode requested: learner code will not be executed.'
    } else {
        & $webPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pyspark') else 1)"
        if ($LASTEXITCODE -ne 0) {
            Write-Output 'This Python does not include PySpark. Auto mode may only check syntax.'
            Write-Output 'Use -PythonPath to select a Spark environment. Model/API settings do not enable Spark execution.'
        }
    }
    $webArgs = @('-X', 'utf8', '-m', 'sparktutor', 'web', '--port', "$Port", '--host', $ListenAddress)
    if ($DryRun) { $webArgs = @('-X', 'utf8', '-m', 'sparktutor', '--dry-run', 'web', '--port', "$Port", '--host', $ListenAddress) }
    if ($DataDir) { $webArgs += @('--data-dir', $DataDir) }
    if ($TrustedLocal) { $webArgs += '--trusted-local' }
    if ($IsolatedExecution) { $webArgs += '--isolated-execution' }
    & $webPython @webArgs
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousSparkTutorPythonPath
}
