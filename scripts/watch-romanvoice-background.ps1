param(
    [ValidateRange(10, 3600)]
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = 'Continue'

$ensureScript = Join-Path $PSScriptRoot 'ensure-romanvoice-running.ps1'
$commonScript = Join-Path $PSScriptRoot 'romanvoice-watchdog-common.ps1'
. $commonScript
$logDir = Join-Path $env:LOCALAPPDATA 'RomanVoice'
$logFile = Join-Path $logDir 'startup-watchdog.log'

function Write-WatchdogLog {
    param([string]$Message)

    Write-RomanVoiceWatchdogLog -Path $logFile -Message $Message -Quiet
}

$watchdogMutex = [Threading.Mutex]::new($false, 'Local\RomanVoiceBackgroundWatchdog')
try {
    $ownsWatchdogMutex = $watchdogMutex.WaitOne(0)
} catch [Threading.AbandonedMutexException] {
    $ownsWatchdogMutex = $true
}
if (-not $ownsWatchdogMutex) {
    exit 0
}

Write-WatchdogLog "RomanVoice resident watchdog started (interval=${IntervalSeconds}s)."

try {
    while ($true) {
        try {
            & $ensureScript -Quiet
        } catch {
            Write-WatchdogLog "Watchdog check failed: $($_.Exception.Message)"
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    $watchdogMutex.ReleaseMutex()
    $watchdogMutex.Dispose()
}
