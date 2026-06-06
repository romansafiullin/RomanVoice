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

function Get-ServicePortOwner {
    param([int]$Port)

    try {
        @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    } catch {
        @()
    }
}

function Test-PreferredRomanVoiceProcess {
    param(
        [Parameter(Mandatory=$true)]$Process,
        [Parameter(Mandatory=$true)][string]$PreferredPythonw
    )

    $preferred = $PreferredPythonw.ToLowerInvariant()
    $executablePath = [string]$Process.ExecutablePath
    if ($executablePath -and $executablePath.ToLowerInvariant() -eq $preferred) {
        return $true
    }

    $parentPid = [int]$Process.ParentProcessId
    if ($parentPid -le 0) {
        return $false
    }

    try {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $parentPid" -ErrorAction Stop
    } catch {
        return $false
    }
    $parentExecutablePath = [string]$parent.ExecutablePath
    return $parentExecutablePath -and $parentExecutablePath.ToLowerInvariant() -eq $preferred
}

$venvPythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$servicePort = if ($env:ROMANVOICE_SERVICE_PORT) { [int]$env:ROMANVOICE_SERVICE_PORT } else { 8799 }
$running = @(Get-RomanVoiceProcess)
$serviceOwners = @(Get-ServicePortOwner -Port $servicePort)
if ($serviceOwners.Count -gt 0) {
    $ownerPid = [int]$serviceOwners[0]
    $owner = $running | Where-Object { [int]$_.ProcessId -eq $ownerPid } | Select-Object -First 1
    if ($owner -and (Test-PreferredRomanVoiceProcess -Process $owner -PreferredPythonw $venvPythonw)) {
        Write-WatchdogLog "RomanVoice already running (pid=$ownerPid, owns port=$servicePort)."
        exit 0
    }

    $ownerExecutable = if ($owner) { [string]$owner.ExecutablePath } else { "unknown executable" }
    Write-WatchdogLog "RomanVoice service port $servicePort owned by non-preferred pid=$ownerPid ($ownerExecutable); expected $venvPythonw."
    exit 0
}

$preferredRunning = @(
    $running | Where-Object {
        Test-PreferredRomanVoiceProcess -Process $_ -PreferredPythonw $venvPythonw
    }
)
if ($preferredRunning.Count -gt 0) {
    Write-WatchdogLog "RomanVoice already running (pid=$($preferredRunning[0].ProcessId), service port $servicePort not listening yet)."
    exit 0
}

if ($running.Count -gt 0) {
    Write-WatchdogLog "Ignoring non-preferred RomanVoice process pid=$($running[0].ProcessId); service port $servicePort is not listening."
}

$env:ROMANVOICE_START_HIDDEN = '1'
$env:ROMANVOICE_ENABLE_GLOBAL_HOTKEYS = '1'
$env:ROMANVOICE_SERVICE_HOST = if ($env:ROMANVOICE_SERVICE_HOST) { $env:ROMANVOICE_SERVICE_HOST } else { '0.0.0.0' }
Remove-Item Env:\ROMANVOICE_FORCE_SHOW -ErrorAction SilentlyContinue

if (Test-Path $venvPythonw) {
    Write-WatchdogLog "Starting RomanVoice from $venvPythonw"
    Start-Process -FilePath $venvPythonw -ArgumentList 'app_qt.py' -WorkingDirectory $repoRoot -WindowStyle Hidden
} else {
    Write-WatchdogLog "Starting RomanVoice through uv because .venv pythonw was not found"
    Start-Process -FilePath 'uv' -ArgumentList @('run', '--python', '3.12', 'pythonw', 'app_qt.py') -WorkingDirectory $repoRoot -WindowStyle Hidden
}

Start-Sleep -Seconds 3
$afterStart = @(Get-RomanVoiceProcess)
$preferredAfterStart = @(
    $afterStart | Where-Object {
        Test-PreferredRomanVoiceProcess -Process $_ -PreferredPythonw $venvPythonw
    }
)
if ($preferredAfterStart.Count -eq 0) {
    Write-WatchdogLog 'RomanVoice did not appear after start attempt.'
    exit 2
}

$afterPortOwners = @(Get-ServicePortOwner -Port $servicePort)
$portOwnerLabel = if ($afterPortOwners.Count -gt 0) { [string]$afterPortOwners[0] } else { "none" }
Write-WatchdogLog "RomanVoice started (pid=$($preferredAfterStart[0].ProcessId), portOwner=$portOwnerLabel)."
exit 0
