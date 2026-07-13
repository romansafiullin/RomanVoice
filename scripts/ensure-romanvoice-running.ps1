param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$commonScript = Join-Path $PSScriptRoot 'romanvoice-watchdog-common.ps1'
. $commonScript
$logDir = Join-Path $env:LOCALAPPDATA 'RomanVoice'
$logFile = Join-Path $logDir 'startup-watchdog.log'
$healthFailureFile = Join-Path $logDir 'startup-health-failures.txt'
$listenerFailureFile = Join-Path $logDir 'startup-listener-failures.json'
$failureThreshold = 3
$listenerStartupGraceSeconds = 120

function Write-WatchdogLog {
    param([string]$Message)

    Write-RomanVoiceWatchdogLog -Path $logFile -Message $Message -Quiet:$Quiet
}

$ensureMutex = [Threading.Mutex]::new($false, 'Local\RomanVoiceEnsureRunning')
try {
    $ownsEnsureMutex = $ensureMutex.WaitOne([TimeSpan]::FromSeconds(10))
} catch [Threading.AbandonedMutexException] {
    $ownsEnsureMutex = $true
}
if (-not $ownsEnsureMutex) {
    Write-WatchdogLog 'Another RomanVoice startup check is still running; skipping duplicate check.'
    $ensureMutex.Dispose()
    exit 0
}

try {
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

function Test-AuthenticatedServiceHealth {
    param([int]$Port)

    $token = [string]$env:ROMANVOICE_SERVICE_TOKEN
    if (-not $token) {
        $tokenFile = if ($env:ROMANVOICE_SERVICE_TOKEN_FILE) {
            $env:ROMANVOICE_SERVICE_TOKEN_FILE
        } else {
            Join-Path $env:APPDATA 'RomanVoice\service_token.txt'
        }
        if (Test-Path -LiteralPath $tokenFile) {
            $token = (Get-Content -Raw -LiteralPath $tokenFile).Trim()
        }
    }
    if (-not $token) {
        return $false
    }

    try {
        $response = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$Port/v1/health" `
            -Headers @{ Authorization = "Bearer $token" } `
            -TimeoutSec 3 `
            -Method Get
        return [bool]$response.ok
    } catch {
        return $false
    }
}

function Register-HealthFailure {
    $count = 0
    if (Test-Path -LiteralPath $healthFailureFile) {
        $raw = (Get-Content -Raw -LiteralPath $healthFailureFile).Trim()
        [void][int]::TryParse($raw, [ref]$count)
    }
    $count++
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Set-Content -LiteralPath $healthFailureFile -Value $count -Encoding ASCII
    return $count
}

function Reset-HealthFailures {
    Remove-Item -LiteralPath $healthFailureFile -Force -ErrorAction SilentlyContinue
}

function Register-ListenerFailure {
    param([Parameter(Mandatory=$true)][int]$ProcessId)

    $count = 1
    $firstFailureUtc = [DateTime]::UtcNow
    if (Test-Path -LiteralPath $listenerFailureFile) {
        try {
            $state = Get-Content -Raw -LiteralPath $listenerFailureFile | ConvertFrom-Json
            if ([int]$state.ProcessId -eq $ProcessId) {
                $count = [int]$state.FailureCount + 1
                $firstFailureUtc = [DateTime]::Parse(
                    [string]$state.FirstFailureUtc,
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::RoundtripKind
                )
            }
        } catch {
            $count = 1
            $firstFailureUtc = [DateTime]::UtcNow
        }
    }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $newState = [pscustomobject]@{
        ProcessId = $ProcessId
        FailureCount = $count
        FirstFailureUtc = $firstFailureUtc.ToString('o')
    }
    $newState | ConvertTo-Json -Compress | Set-Content -LiteralPath $listenerFailureFile -Encoding ASCII
    return $newState
}

function Reset-ListenerFailures {
    Remove-Item -LiteralPath $listenerFailureFile -Force -ErrorAction SilentlyContinue
}

function Get-PreferredRomanVoiceRootProcess {
    param(
        [Parameter(Mandatory=$true)]$Process,
        [Parameter(Mandatory=$true)][string]$PreferredPythonw
    )

    $preferred = $PreferredPythonw.ToLowerInvariant()
    $executablePath = [string]$Process.ExecutablePath
    if (
        $executablePath -and
        $executablePath.ToLowerInvariant() -eq $preferred -and
        [string]$Process.CommandLine -match 'app_qt\.py'
    ) {
        return $Process
    }

    $parentPid = [int]$Process.ParentProcessId
    if ($parentPid -le 0) {
        return $null
    }

    try {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $parentPid" -ErrorAction Stop
    } catch {
        return $null
    }
    $parentExecutablePath = [string]$parent.ExecutablePath
    if (
        $parentExecutablePath -and
        $parentExecutablePath.ToLowerInvariant() -eq $preferred -and
        [string]$parent.CommandLine -match 'app_qt\.py'
    ) {
        return $parent
    }
    return $null
}

function Stop-OwnedRomanVoiceProcessTree {
    param(
        [Parameter(Mandatory=$true)]$RootProcess,
        [Parameter(Mandatory=$true)][string]$PreferredPythonw
    )

    $rootPid = [int]$RootProcess.ProcessId
    $currentRoot = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction Stop
    $rootExecutable = [string]$currentRoot.ExecutablePath
    if (
        -not $rootExecutable -or
        $rootExecutable.ToLowerInvariant() -ne $PreferredPythonw.ToLowerInvariant() -or
        [string]$currentRoot.CommandLine -notmatch 'app_qt\.py'
    ) {
        throw "Refusing to stop pid=$rootPid because it is no longer the owned RomanVoice root."
    }

    $allProcesses = @(Get-CimInstance Win32_Process)
    $ownedIds = [Collections.Generic.List[int]]::new()
    $ownedIds.Add($rootPid)
    for ($index = 0; $index -lt $ownedIds.Count; $index++) {
        $parentPid = $ownedIds[$index]
        foreach ($child in $allProcesses | Where-Object { [int]$_.ParentProcessId -eq $parentPid }) {
            $childPid = [int]$child.ProcessId
            if (-not $ownedIds.Contains($childPid)) {
                $ownedIds.Add($childPid)
            }
        }
    }

    $stopOrder = $ownedIds.ToArray()
    [array]::Reverse($stopOrder)
    foreach ($ownedPid in $stopOrder) {
        Stop-Process -Id $ownedPid -Force -ErrorAction SilentlyContinue
    }
}

function Test-PreferredRomanVoiceProcess {
    param(
        [Parameter(Mandatory=$true)]$Process,
        [Parameter(Mandatory=$true)][string]$PreferredPythonw
    )

    return $null -ne (Get-PreferredRomanVoiceRootProcess `
        -Process $Process `
        -PreferredPythonw $PreferredPythonw)
}

$venvPythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$servicePort = if ($env:ROMANVOICE_SERVICE_PORT) { [int]$env:ROMANVOICE_SERVICE_PORT } else { 8799 }
$running = @(Get-RomanVoiceProcess)
$serviceOwners = @(Get-ServicePortOwner -Port $servicePort)
if ($serviceOwners.Count -gt 0) {
    Reset-ListenerFailures
    $ownerPid = [int]$serviceOwners[0]
    $owner = $running | Where-Object { [int]$_.ProcessId -eq $ownerPid } | Select-Object -First 1
    if ($owner -and (Test-PreferredRomanVoiceProcess -Process $owner -PreferredPythonw $venvPythonw)) {
        if (Test-AuthenticatedServiceHealth -Port $servicePort) {
            Reset-HealthFailures
            Write-WatchdogLog "RomanVoice healthy (pid=$ownerPid, authenticated port=$servicePort)."
            exit 0
        }

        $failureCount = Register-HealthFailure
        if ($failureCount -lt $failureThreshold) {
            Write-WatchdogLog "RomanVoice pid=$ownerPid owns port=$servicePort but authenticated health failed ($failureCount/$failureThreshold); waiting before restart."
            exit 3
        }

        $ownedRoot = Get-PreferredRomanVoiceRootProcess -Process $owner -PreferredPythonw $venvPythonw
        if (-not $ownedRoot) {
            Write-WatchdogLog 'RomanVoice process ownership changed during the health check; refusing the scoped restart and deferring.'
            exit 3
        }
        Write-WatchdogLog "RomanVoice pid=$ownerPid failed authenticated health $failureThreshold times; restarting owned process tree rooted at pid=$($ownedRoot.ProcessId)."
        Stop-OwnedRomanVoiceProcessTree -RootProcess $ownedRoot -PreferredPythonw $venvPythonw
        Start-Sleep -Seconds 2
        Reset-HealthFailures
        $running = @(Get-RomanVoiceProcess)
        $serviceOwners = @(Get-ServicePortOwner -Port $servicePort)
    }

    if ($serviceOwners.Count -gt 0) {
        $ownerExecutable = if ($owner) { [string]$owner.ExecutablePath } else { "unknown executable" }
        Write-WatchdogLog "RomanVoice service port $servicePort owned by non-preferred pid=$ownerPid ($ownerExecutable); expected $venvPythonw."
        exit 0
    }
}

$preferredRunning = @(
    $running | Where-Object {
        Test-PreferredRomanVoiceProcess -Process $_ -PreferredPythonw $venvPythonw
    }
)
if ($preferredRunning.Count -gt 0) {
    Reset-HealthFailures
    $ownedRoot = Get-PreferredRomanVoiceRootProcess `
        -Process $preferredRunning[0] `
        -PreferredPythonw $venvPythonw
    if (-not $ownedRoot) {
        Write-WatchdogLog 'RomanVoice process ownership changed during the listener check; deferring to the next check.'
        exit 3
    }
    $listenerFailureState = Register-ListenerFailure -ProcessId ([int]$ownedRoot.ProcessId)
    $listenerFailureCount = [int]$listenerFailureState.FailureCount
    $firstFailureUtc = [DateTime]::Parse(
        [string]$listenerFailureState.FirstFailureUtc,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    )
    $listenerFailureAgeSeconds = [int]([DateTime]::UtcNow - $firstFailureUtc).TotalSeconds
    if (
        $listenerFailureCount -lt $failureThreshold -or
        $listenerFailureAgeSeconds -lt $listenerStartupGraceSeconds
    ) {
        Write-WatchdogLog "RomanVoice owned root pid=$($ownedRoot.ProcessId) has no listener on port $servicePort ($listenerFailureCount/$failureThreshold checks, ${listenerFailureAgeSeconds}s/${listenerStartupGraceSeconds}s); allowing bounded startup time."
        exit 3
    }

    Write-WatchdogLog "RomanVoice owned root pid=$($ownedRoot.ProcessId) still has no listener after $failureThreshold checks; restarting only that owned process tree."
    Stop-OwnedRomanVoiceProcessTree -RootProcess $ownedRoot -PreferredPythonw $venvPythonw
    Start-Sleep -Seconds 2
    Reset-ListenerFailures
    $running = @(Get-RomanVoiceProcess)
    $preferredRunning = @(
        $running | Where-Object {
            Test-PreferredRomanVoiceProcess -Process $_ -PreferredPythonw $venvPythonw
        }
    )
    if ($preferredRunning.Count -gt 0) {
        Write-WatchdogLog "Another owned RomanVoice process remains after the scoped restart; deferring a new start to the next check."
        exit 3
    }
} else {
    Reset-ListenerFailures
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
} finally {
    if ($ownsEnsureMutex) {
        try {
            $ensureMutex.ReleaseMutex()
        } catch [ApplicationException] {
            # The owning thread may already be unwinding after a terminating error.
        }
    }
    $ensureMutex.Dispose()
}
