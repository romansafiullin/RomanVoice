param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $env:LOCALAPPDATA 'RomanVoice'
$logFile = Join-Path $logDir 'startup-watchdog.log'

function Write-WatchdogLog {
    param([string]$Message)

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$timestamp $Message"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    if (-not $Quiet) {
        Write-Host $line
    }
}

function Get-RomanVoiceProcess {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^pythonw?\.exe$' -and
            $_.CommandLine -match 'app_qt\.py'
        }
}

$running = @(Get-RomanVoiceProcess)
if ($running.Count -gt 0) {
    Write-WatchdogLog "RomanVoice already running (pid=$($running[0].ProcessId))."
    exit 0
}

$env:ROMANVOICE_START_HIDDEN = '1'
$env:ROMANVOICE_ENABLE_GLOBAL_HOTKEYS = '1'
$env:ROMANVOICE_SERVICE_HOST = if ($env:ROMANVOICE_SERVICE_HOST) { $env:ROMANVOICE_SERVICE_HOST } else { '0.0.0.0' }
Remove-Item Env:\ROMANVOICE_FORCE_SHOW -ErrorAction SilentlyContinue

$venvPythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
if (Test-Path $venvPythonw) {
    Write-WatchdogLog "Starting RomanVoice from $venvPythonw"
    Start-Process -FilePath $venvPythonw -ArgumentList 'app_qt.py' -WorkingDirectory $repoRoot -WindowStyle Hidden
} else {
    Write-WatchdogLog "Starting RomanVoice through uv because .venv pythonw was not found"
    Start-Process -FilePath 'uv' -ArgumentList @('run', '--python', '3.12', 'pythonw', 'app_qt.py') -WorkingDirectory $repoRoot -WindowStyle Hidden
}

Start-Sleep -Seconds 3
$afterStart = @(Get-RomanVoiceProcess)
if ($afterStart.Count -eq 0) {
    Write-WatchdogLog 'RomanVoice did not appear after start attempt.'
    exit 2
}

Write-WatchdogLog "RomanVoice started (pid=$($afterStart[0].ProcessId))."
exit 0
