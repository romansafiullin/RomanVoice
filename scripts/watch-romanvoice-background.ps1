param(
    [ValidateRange(10, 3600)]
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = 'Continue'

$ensureScript = Join-Path $PSScriptRoot 'ensure-romanvoice-running.ps1'
$logDir = Join-Path $env:LOCALAPPDATA 'RomanVoice'
$logFile = Join-Path $logDir 'startup-watchdog.log'

function Write-WatchdogLog {
    param([string]$Message)

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Add-Content -Path $logFile -Value "$timestamp $Message" -Encoding UTF8
}

Write-WatchdogLog "RomanVoice resident watchdog started (interval=${IntervalSeconds}s)."

while ($true) {
    try {
        & $ensureScript -Quiet
    } catch {
        Write-WatchdogLog "Watchdog check failed: $($_.Exception.Message)"
    }

    Start-Sleep -Seconds $IntervalSeconds
}
