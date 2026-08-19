param(
    [ValidateRange(1, 60)]
    [int]$IntervalMinutes = 1,

    [ValidateSet('Scheduled', 'StartupResident')]
    [string]$Mode = 'Scheduled'
)

$ErrorActionPreference = 'Stop'

$ensureScript = Join-Path $PSScriptRoot 'ensure-romanvoice-running.ps1'
$watchScript = Join-Path $PSScriptRoot 'watch-romanvoice-background.ps1'
$startupTaskName = 'RomanVoice Background Startup'
$watchdogTaskName = 'RomanVoice Background Watchdog'
$managedTaskNames = @($startupTaskName, $watchdogTaskName)
$startupFolder = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$startupVbs = Join-Path $startupFolder 'RomanVoice Background Watchdog.vbs'
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskArguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ensureScript + '" -Quiet'
$watchCommand = 'powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchScript + '" -IntervalSeconds 60'

if (-not (Test-Path -LiteralPath $ensureScript)) {
    throw "Missing watchdog script: $ensureScript"
}
if (-not (Test-Path -LiteralPath $watchScript)) {
    throw "Missing resident watchdog script: $watchScript"
}

function Get-ManagedTaskXml {
    param([Parameter(Mandatory = $true)][string]$TaskName)

    $output = & schtasks.exe /Query /TN $TaskName /XML 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($output -join [Environment]::NewLine)
}

function Save-ManagedTaskSnapshots {
    $snapshots = @{}
    foreach ($taskName in $managedTaskNames) {
        $snapshots[$taskName] = Get-ManagedTaskXml -TaskName $taskName
    }
    return $snapshots
}

function Restore-ManagedTaskSnapshots {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Snapshots,
        [Parameter(Mandatory = $true)][string[]]$AttemptedTaskNames
    )

    foreach ($taskName in $managedTaskNames) {
        $snapshot = [string]$Snapshots[$taskName]
        if ($snapshot) {
            $temporaryXml = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($temporaryXml, $snapshot, [Text.Encoding]::Unicode)
                & schtasks.exe /Create /TN $taskName /XML $temporaryXml /F | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "[warn] Could not restore the prior task definition: $taskName"
                }
            } finally {
                Remove-Item -LiteralPath $temporaryXml -Force -ErrorAction SilentlyContinue
            }
        } elseif ($AttemptedTaskNames -contains $taskName) {
            & schtasks.exe /Delete /TN $taskName /F | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[ok] Rolled back partial scheduled task: $taskName"
            } else {
                Write-Host "[warn] Could not remove partial scheduled task: $taskName"
            }
        }
    }
}

function Register-RomanVoiceTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][ValidateSet('Logon', 'Minute')][string]$TriggerKind
    )

    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $taskArguments
    $principal = New-ScheduledTaskPrincipal `
        -UserId $currentUser `
        -LogonType Interactive `
        -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    if ($TriggerKind -eq 'Logon') {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
    } else {
        $triggerAt = Get-Date
        $trigger = New-ScheduledTaskTrigger -Daily -At $triggerAt
        $repeatTemplate = New-ScheduledTaskTrigger `
            -Once `
            -At $triggerAt `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
            -RepetitionDuration (New-TimeSpan -Days 1)
        $trigger.Repetition = $repeatTemplate.Repetition
    }

    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description 'RomanVoice hidden health/start one-shot; the tray app remains the runtime owner.'
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
}

function Resolve-AccountSid {
    param([Parameter(Mandatory = $true)][string]$Account)

    if ($Account -match '^S-\d-') {
        return [Security.Principal.SecurityIdentifier]::new($Account).Value
    }
    return ([Security.Principal.NTAccount]::new($Account)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
}

function Test-InteractiveCurrentUserPrincipal {
    param(
        [Parameter(Mandatory = $true)]$Principal,
        [Parameter(Mandatory = $true)][string]$ExpectedSid
    )

    try {
        $principalSid = Resolve-AccountSid -Account ([string]$Principal.UserId)
    } catch {
        return $false
    }
    return (
        $principalSid -eq $ExpectedSid -and
        [string]$Principal.LogonType -eq 'Interactive' -and
        [string]$Principal.RunLevel -eq 'Limited'
    )
}

function Assert-RomanVoiceTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][ValidateSet('Logon', 'Minute')][string]$TriggerKind
    )

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if (-not $task.Settings.Enabled -or -not $task.Settings.Hidden) {
        throw "Scheduled task is not enabled and hidden: $TaskName"
    }
    if (-not (Test-InteractiveCurrentUserPrincipal -Principal $task.Principal -ExpectedSid $currentUserSid)) {
        throw "Scheduled task is not limited to the current interactive user: $TaskName"
    }

    $actions = @($task.Actions)
    if ($actions.Count -ne 1 -or [string]$actions[0].Execute -notmatch '(?i)(^|\\)powershell\.exe$') {
        throw "Scheduled task action is not the hidden PowerShell one-shot: $TaskName"
    }
    if ([string]$actions[0].Arguments -ne $taskArguments) {
        throw "Scheduled task action arguments changed unexpectedly: $TaskName"
    }

    $taskXml = Get-ManagedTaskXml -TaskName $TaskName
    if (-not $taskXml) {
        throw "Could not query scheduled task XML: $TaskName"
    }
    [xml]$xml = $taskXml
    $namespace = [Xml.XmlNamespaceManager]::new($xml.NameTable)
    $namespace.AddNamespace('task', 'http://schemas.microsoft.com/windows/2004/02/mit/task')

    $hiddenNode = $xml.SelectSingleNode('/task:Task/task:Settings/task:Hidden', $namespace)
    if (-not $hiddenNode -or $hiddenNode.InnerText -ne 'true') {
        throw "Scheduled task XML does not require hidden execution: $TaskName"
    }

    $xmlPrincipalSid = $xml.SelectSingleNode('/task:Task/task:Principals/task:Principal/task:UserId', $namespace)
    $xmlLogonType = $xml.SelectSingleNode('/task:Task/task:Principals/task:Principal/task:LogonType', $namespace)
    if (-not $xmlPrincipalSid -or $xmlPrincipalSid.InnerText -ne $currentUserSid) {
        throw "Scheduled task XML does not resolve to the current user SID: $TaskName"
    }
    if (-not $xmlLogonType -or $xmlLogonType.InnerText -ne 'InteractiveToken') {
        throw "Scheduled task XML is not interactive-token only: $TaskName"
    }

    if ($TriggerKind -eq 'Logon') {
        if (-not $xml.SelectSingleNode('/task:Task/task:Triggers/task:LogonTrigger', $namespace)) {
            throw "Scheduled task is missing its logon trigger: $TaskName"
        }
    } else {
        $intervalNode = $xml.SelectSingleNode('/task:Task/task:Triggers/task:CalendarTrigger/task:Repetition/task:Interval', $namespace)
        $durationNode = $xml.SelectSingleNode('/task:Task/task:Triggers/task:CalendarTrigger/task:Repetition/task:Duration', $namespace)
        if (
            -not $intervalNode -or
            $intervalNode.InnerText -ne "PT$($IntervalMinutes)M" -or
            -not $durationNode -or
            $durationNode.InnerText -ne 'P1D'
        ) {
            throw "Scheduled task does not have the requested minute cadence: $TaskName"
        }
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
    Set-Content -LiteralPath $startupVbs -Value $vbs -Encoding ASCII
    Write-Host "[ok] Installed startup watchdog: $startupVbs"
}

function Get-ResidentWatchdogs {
    @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.Name -match '^powershell\.exe$' -and
                $_.CommandLine -and
                $_.CommandLine.IndexOf($watchScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
            }
    )
}

function Start-ResidentWatchdog {
    $runningWatchdogs = @(Get-ResidentWatchdogs)
    if ($runningWatchdogs.Count -gt 0) {
        Write-Host "[ok] Resident watchdog already running (pid=$($runningWatchdogs[0].ProcessId))."
        return
    }

    $watchArgs = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchScript + '" -IntervalSeconds 60'
    Start-Process -FilePath 'powershell.exe' -ArgumentList $watchArgs -WindowStyle Hidden
    Start-Sleep -Seconds 1
    if (@(Get-ResidentWatchdogs).Count -eq 0) {
        throw 'Startup-folder watchdog was written, but its hidden resident process did not start.'
    }
    Write-Host '[ok] Resident watchdog started for this session.'
}

function Establish-StartupResidentFallback {
    Install-StartupVbs
    Start-ResidentWatchdog
}

function Remove-ManagedScheduledTasks {
    foreach ($taskName in $managedTaskNames) {
        & schtasks.exe /Delete /TN $taskName /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-ManagedTaskXml -TaskName $taskName)) {
            throw "Could not remove scheduled task after StartupResident fallback was verified: $taskName"
        }
        Write-Host "[ok] Removed scheduled task: $taskName"
    }
}

function Retire-StartupResidentFallback {
    if (Test-Path -LiteralPath $startupVbs) {
        Remove-Item -LiteralPath $startupVbs -Force -ErrorAction Stop
        Write-Host "[ok] Retired startup watchdog: $startupVbs"
    }

    foreach ($watchdog in @(Get-ResidentWatchdogs)) {
        Stop-Process -Id $watchdog.ProcessId -Force -ErrorAction Stop
        Write-Host "[ok] Retired resident watchdog process: $($watchdog.ProcessId)"
    }
}

# Install entry point
Write-Host ''
Write-Host "Installing RomanVoice background watchdog in $Mode mode"
Write-Host '--------------------------------------------------------'

$scheduledTasksInstalled = $false
if ($Mode -eq 'StartupResident') {
    Establish-StartupResidentFallback
    Remove-ManagedScheduledTasks
    Write-Host '[ok] StartupResident fallback is now the active watchdog owner.'
} else {
    $snapshots = Save-ManagedTaskSnapshots
    $attemptedTaskNames = [Collections.Generic.List[string]]::new()
    try {
        $attemptedTaskNames.Add($startupTaskName)
        Register-RomanVoiceTask -TaskName $startupTaskName -TriggerKind Logon
        $attemptedTaskNames.Add($watchdogTaskName)
        Register-RomanVoiceTask -TaskName $watchdogTaskName -TriggerKind Minute

        Assert-RomanVoiceTask -TaskName $startupTaskName -TriggerKind Logon
        Assert-RomanVoiceTask -TaskName $watchdogTaskName -TriggerKind Minute
        $scheduledTasksInstalled = $true
    } catch {
        $scheduledFailure = $_
        Restore-ManagedTaskSnapshots -Snapshots $snapshots -AttemptedTaskNames $attemptedTaskNames.ToArray()
        try {
            Establish-StartupResidentFallback
        } catch {
            throw "Scheduled watchdog setup failed ($($scheduledFailure.Exception.Message)) and StartupResident fallback could not be verified ($($_.Exception.Message))."
        }
        Write-Host "[warn] Scheduled watchdog setup failed: $($scheduledFailure.Exception.Message)"
        Write-Host '[warn] StartupResident fallback remains active and verified.'
    }

    if ($scheduledTasksInstalled) {
        Retire-StartupResidentFallback
        Write-Host "[ok] Scheduled watchdog is active: logon check plus every $IntervalMinutes minute(s)."
    }
}

& $ensureScript -Quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[warn] Immediate RomanVoice check exited with code $LASTEXITCODE; the active watchdog will retry."
}

Write-Host ''
Write-Host 'To remove this behavior:'
Write-Host '    scripts\remove-background-watchdog.ps1'
Write-Host 'To restore the verified StartupResident fallback:'
Write-Host '    scripts\install-background-watchdog.ps1 -Mode StartupResident'
Write-Host ''
