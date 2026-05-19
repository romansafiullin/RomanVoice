param(
    [ValidateRange(1, 60)]
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'

$ensureScript = Join-Path $PSScriptRoot 'ensure-romanvoice-running.ps1'
$watchScript = Join-Path $PSScriptRoot 'watch-romanvoice-background.ps1'
$startupTaskName = 'RomanVoice Background Startup'
$watchdogTaskName = 'RomanVoice Background Watchdog'
$startupFolder = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$startupVbs = Join-Path $startupFolder 'RomanVoice Background Watchdog.vbs'

if (-not (Test-Path $ensureScript)) {
    throw "Missing watchdog script: $ensureScript"
}
if (-not (Test-Path $watchScript)) {
    throw "Missing resident watchdog script: $watchScript"
}

$taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ensureScript + '" -Quiet'
$watchCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchScript + '" -IntervalSeconds 60'

function Register-RomanVoiceTask {
    param(
        [string]$TaskName,
        [string[]]$ScheduleArgs
    )

    $args = @('/Create', '/TN', $TaskName, '/TR', $taskCommand, '/F', '/RL', 'LIMITED') + $ScheduleArgs
    $output = & schtasks.exe @args 2>&1
    if ($output) {
        $output | Out-Host
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register scheduled task: $TaskName"
    }
}

function Install-StartupVbs {
    New-Item -ItemType Directory -Path $startupFolder -Force | Out-Null
    $escapedCommand = $watchCommand.Replace('"', '""')
    $runLine = 'shell.Run "' + $escapedCommand + '", 0, False'
    $vbs = @(
        'Set shell = CreateObject("WScript.Shell")',
        $runLine
    )
    Set-Content -Path $startupVbs -Value $vbs -Encoding ASCII
    Write-Host "[ok] Installed startup watchdog: $startupVbs"
}

function Start-ResidentWatchdog {
    $runningWatchdogs = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.Name -match '^powershell\.exe$' -and
                $_.CommandLine -match 'watch-romanvoice-background\.ps1'
            }
    )
    if ($runningWatchdogs.Count -gt 0) {
        Write-Host "[ok] Resident watchdog already running (pid=$($runningWatchdogs[0].ProcessId))."
        return
    }

    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-WindowStyle', 'Hidden',
        '-File', $watchScript,
        '-IntervalSeconds', '60'
    ) -WindowStyle Hidden
    Start-Sleep -Seconds 1
    Write-Host '[ok] Resident watchdog started for this session.'
}

Write-Host ''
Write-Host 'Installing RomanVoice background startup/watchdog tasks'
Write-Host '------------------------------------------------------'

$scheduledTasksInstalled = $false
try {
    Register-RomanVoiceTask -TaskName $startupTaskName -ScheduleArgs @('/SC', 'ONLOGON')
    Register-RomanVoiceTask -TaskName $watchdogTaskName -ScheduleArgs @('/SC', 'MINUTE', '/MO', [string]$IntervalMinutes)
    $scheduledTasksInstalled = $true
} catch {
    Write-Host "[warn] Scheduled Task registration failed: $($_.Exception.Message)"
    Write-Host "[warn] Falling back to a Startup-folder resident watchdog."
    Install-StartupVbs
    Start-ResidentWatchdog
}

& $ensureScript -Quiet
if ($LASTEXITCODE -ne 0) {
    throw 'Watchdog was registered, but immediate RomanVoice start/check failed.'
}

Write-Host ''
if ($scheduledTasksInstalled) {
    Write-Host "[ok] RomanVoice will start at logon."
    Write-Host "[ok] Scheduled watchdog will check every $IntervalMinutes minute(s) and restart it if missing."
} else {
    Write-Host '[ok] RomanVoice resident watchdog will start at logon.'
    Write-Host '[ok] Resident watchdog will check every 60 second(s) and restart it if missing.'
}
Write-Host ''
Write-Host 'To remove this behavior:'
Write-Host '    scripts\remove-background-watchdog.ps1'
Write-Host ''
