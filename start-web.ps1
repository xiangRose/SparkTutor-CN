param(
    [ValidateRange(1, 65535)][int]$Port = 8888,
    [string]$ListenAddress = '0.0.0.0',
    [string]$PythonPath = '',
    [string]$DataDir = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeConfigurationPath = Join-Path $PSScriptRoot '.sparktutor-web/runtime.json'
if (-not $PythonPath -and (Test-Path -LiteralPath $runtimeConfigurationPath -PathType Leaf)) {
    try {
        $runtimeConfiguration = Get-Content -LiteralPath $runtimeConfigurationPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($runtimeConfiguration.pythonPath -isnot [string] -or [string]::IsNullOrWhiteSpace($runtimeConfiguration.pythonPath)) {
            throw 'pythonPath must be a non-empty string.'
        }
        $PythonPath = $runtimeConfiguration.pythonPath
    } catch {
        throw 'The saved Python configuration in .sparktutor-web/runtime.json is invalid. Correct it or use -PythonPath.'
    }
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
    & $webPython @webArgs
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousSparkTutorPythonPath
}
