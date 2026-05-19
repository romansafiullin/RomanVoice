param(
    [switch]$StopRunning
)

$ErrorActionPreference = 'Continue'

$taskNames = @(
    'RomanVoice Background Startup',
    'RomanVoice Background Watchdog'
)
$startupVbs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\RomanVoice Background Watchdog.vbs'

Write-Host ''
Write-Host 'Removing RomanVoice background startup/watchdog tasks'
Write-Host '----------------------------------------------------'

foreach ($taskName in $taskNames) {
    & schtasks.exe /Delete /TN $taskName /F | Out-Host
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[ok] Removed task: $taskName"
    } else {
        Write-Host "[skip] Task was not present or could not be removed: $taskName"
    }
}

if (Test-Path $startupVbs) {
    Remove-Item -LiteralPath $startupVbs -Force
    Write-Host "[ok] Removed startup watchdog: $startupVbs"
} else {
    Write-Host "[skip] Startup watchdog was not present: $startupVbs"
}

Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^powershell\.exe$' -and
        $_.CommandLine -match 'watch-romanvoice-background\.ps1'
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "[ok] Stopped resident watchdog process: $($_.ProcessId)"
    }

if ($StopRunning) {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^pythonw?\.exe$' -and
            $_.CommandLine -match 'app_qt\.py'
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "[ok] Stopped RomanVoice process: $($_.ProcessId)"
        }
}

Write-Host ''
