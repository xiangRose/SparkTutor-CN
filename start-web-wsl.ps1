param(
    [ValidateRange(1, 65535)][int]$Port = 8888,
    [string]$ListenAddress = '0.0.0.0',
    [string]$Distribution = 'Ubuntu',
    [string]$PythonPath = '',
    [string]$ProjectPath = '',
    [string]$DataDir = '',
    [string]$DockerHost = 'unix:///var/run/docker.sock',
    [string]$DockerExecutable = '/usr/bin/docker',
    [string]$DockerService = '',
    [string]$ExecutorImage = 'sparktutor-executor:4.1.1',
    [string]$WslExecutable = 'wsl.exe',
    [switch]$DryRun,
    [switch]$TrustedLocal
)

$ErrorActionPreference = 'Stop'
if ($DryRun -and $TrustedLocal) { throw '-DryRun cannot be combined with -TrustedLocal.' }
if ($Distribution -notmatch '^[A-Za-z0-9._ -]{1,100}$') { throw 'Invalid WSL distribution name.' }
if (-not $DockerExecutable.StartsWith('/')) { throw 'Use an absolute Linux Docker executable path.' }
if (-not $DockerHost.StartsWith('unix:///')) { throw 'The WSL launcher requires a local Unix Docker socket.' }
if ($DockerService -and $DockerService -notmatch '^[A-Za-z0-9_.@-]+\.service$') { throw 'Invalid project Docker service name.' }
if ($ExecutorImage -notmatch '^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$') { throw 'Invalid executor image name.' }
$wslCommand = Get-Command $WslExecutable -CommandType Application -ErrorAction Stop | Select-Object -First 1
$wslProgram = $wslCommand.Source
$wslBase = @('--distribution', $Distribution, '--user', 'root')
$script:sparkTutorWslExitCode = 0

function Invoke-SparkTutorWsl([string[]]$ArgumentList) {
    # Windows PowerShell can promote benign WSL stderr notices to terminating
    # errors. Native exit status decides success; preserve stderr for logs.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $wslProgram @ArgumentList
        $script:sparkTutorWslExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Convert-SparkTutorWslPath([string]$Value) {
    if ($Value.StartsWith('/')) { return $Value }
    $absolutePath = if ([IO.Path]::IsPathRooted($Value)) { [IO.Path]::GetFullPath($Value) } else {
        [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Value))
    }
    $convertedPath = Invoke-SparkTutorWsl ($wslBase + @('--exec', 'wslpath', '-a', '-u', $absolutePath)) 2>$null
    if ($script:sparkTutorWslExitCode -ne 0 -or -not $convertedPath) { throw 'Could not convert the selected project/data directory to a WSL path.' }
    return ($convertedPath | Out-String).Trim()
}

$linuxProject = Convert-SparkTutorWslPath $(if ($ProjectPath) { $ProjectPath } else { $PSScriptRoot })
if (-not $PythonPath) { $PythonPath = $linuxProject.TrimEnd('/') + '/.venv/bin/python' }
if (-not $PythonPath.StartsWith('/')) { throw 'Use the absolute Linux Python path for WSL.' }
$selectedData = if ($DataDir) { $DataDir } elseif ($TrustedLocal) {
    Join-Path $PSScriptRoot '.sparktutor-trusted-local'
} else { Join-Path $PSScriptRoot '.sparktutor-web' }
$linuxData = Convert-SparkTutorWslPath $selectedData
$wslLocation = @('--distribution', $Distribution, '--user', 'root', '--cd', $linuxProject, '--exec')
$environmentArgs = @('env', '-u', 'DOCKER_CONTEXT', "PYTHONPATH=$linuxProject/src",
                     "DOCKER_HOST=$DockerHost", "SPARKTUTOR_ISOLATED_DOCKER_EXECUTABLE=$DockerExecutable",
                     "SPARKTUTOR_ISOLATED_IMAGE=$ExecutorImage")

Write-Output ("Using WSL: " + $Distribution)
Write-Output ("Website: http://localhost:" + $Port)
Write-Output ("Learning data: " + $selectedData)
Invoke-SparkTutorWsl ($wslLocation + $environmentArgs + @($PythonPath, '-c', 'import sys; assert sys.version_info >= (3, 10); import sparktutor.web.server'))
if ($script:sparkTutorWslExitCode -ne 0) { throw 'The selected WSL Python environment cannot import this project. Install its dependencies or correct the configured paths.' }

if (-not $DryRun -and -not $TrustedLocal) {
    if ($DockerService) {
        Invoke-SparkTutorWsl ($wslBase + @('--exec', 'systemctl', 'start', $DockerService))
        if ($script:sparkTutorWslExitCode -ne 0) { throw 'The project Docker execution service could not start.' }
    }
    $dockerStatus = Invoke-SparkTutorWsl ($wslLocation + $environmentArgs + @($DockerExecutable, 'version', '--format', '{{.Server.Os}}'))
    if ($script:sparkTutorWslExitCode -ne 0 -or ($dockerStatus | Out-String).Trim() -ne 'linux') {
        throw 'The account execution service is not ready. Check the selected Linux Docker daemon.'
    }
    $imageStatus = Invoke-SparkTutorWsl ($wslLocation + $environmentArgs + @($DockerExecutable, 'image', 'inspect', '--format', '{{.Os}}', $ExecutorImage))
    if ($script:sparkTutorWslExitCode -ne 0 -or ($imageStatus | Out-String).Trim() -ne 'linux') {
        throw 'The Spark executor image is not ready. Build containers/executor before starting account practice.'
    }
    Write-Output 'Account practice: isolated Spark execution is enabled.'
}

$webArgs = @('-X', 'utf8', '-m', 'sparktutor')
if ($DryRun) { $webArgs += '--dry-run' }
$webArgs += @('web', '--host', $ListenAddress, '--port', "$Port", '--data-dir', $linuxData)
if ($TrustedLocal) { $webArgs += '--trusted-local' }
if (-not $DryRun -and -not $TrustedLocal) { $webArgs += '--isolated-execution' }
Invoke-SparkTutorWsl ($wslLocation + $environmentArgs + @($PythonPath) + $webArgs)
exit $script:sparkTutorWslExitCode
