from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_background_launcher_exposes_service_for_phone_clients():
    script = (PROJECT_ROOT / "scripts" / "ensure-romanvoice-running.ps1").read_text(
        encoding="utf-8"
    )

    assert "ROMANVOICE_SERVICE_HOST" in script
    assert "'0.0.0.0'" in script


def test_background_launcher_checks_service_port_owner_before_starting_duplicate():
    script = (PROJECT_ROOT / "scripts" / "ensure-romanvoice-running.ps1").read_text(
        encoding="utf-8"
    )

    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in script
    assert "Test-PreferredRomanVoiceProcess" in script
    assert ".venv\\Scripts\\pythonw.exe" in script
    assert "$Process.ParentProcessId" in script
    assert "owned by non-preferred" in script
    assert "expected $venvPythonw" in script
    assert "exit 4" in script
    assert "RomanVoiceEnsureRunning" in script
    assert "WaitOne([TimeSpan]::FromSeconds(10))" in script
    assert "$ensureMutex.ReleaseMutex()" in script
    assert "$ensureMutex.Dispose()" in script
    assert "} finally {" in script
    assert "Test-AuthenticatedServiceHealth" in script
    assert '"http://127.0.0.1:$Port/v1/health"' in script
    assert 'Authorization = "Bearer $token"' in script
    assert "$failureCount -lt $failureThreshold" in script
    assert "Register-ListenerFailure" in script
    assert "startup-listener-failures.json" in script
    assert "$listenerFailureCount -lt $failureThreshold" in script
    assert "$listenerFailureAgeSeconds -lt $listenerStartupGraceSeconds" in script
    assert "FirstFailureUtc" in script
    assert "Stop-OwnedRomanVoiceProcessTree" in script
    assert "Refusing to stop pid=$rootPid" in script
    assert "Another owned RomanVoice process remains" in script


def test_cmd_background_launchers_delegate_to_one_duplicate_safe_owner():
    scripts = PROJECT_ROOT / "scripts"
    canonical = (scripts / "romanvoice.cmd").read_text(encoding="utf-8")
    assert "ensure-romanvoice-running.ps1" in canonical
    assert "-WindowStyle Hidden" in canonical
    assert "uv run" not in canonical

    for launcher in ("romanvoice-background.cmd", "openwhisper.cmd"):
        script = (scripts / launcher).read_text(encoding="utf-8")
        assert "romanvoice.cmd" in script


def test_explicit_ui_launcher_matches_service_and_firewall_interpreter_shape():
    script = (PROJECT_ROOT / "scripts" / "romanvoice-ui.cmd").read_text(
        encoding="utf-8"
    )

    assert "ROMANVOICE_FORCE_SHOW=1" in script
    assert "ROMANVOICE_SERVICE_HOST" in script
    assert "0.0.0.0" in script
    assert ".venv\\Scripts\\pythonw.exe" in script
    assert "uv run --python 3.12 pythonw app_qt.py" in script
    assert "python app_qt.py" not in script


def test_path_installer_checks_the_supported_uv_environment():
    script = (PROJECT_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert ".venv\\Scripts\\pythonw.exe" in script
    assert "uv sync --python 3.12" in script
    assert "python -m venv venv" not in script


def test_watchdog_installer_uses_transactional_hidden_scheduled_tasks_by_default():
    script = (PROJECT_ROOT / "scripts" / "install-background-watchdog.ps1").read_text(
        encoding="utf-8"
    )

    assert "[string]$Mode = 'Scheduled'" in script
    assert "[ValidateSet('Scheduled', 'StartupResident')]" in script
    assert "$startupTaskName = 'RomanVoice Background Startup'" in script
    assert "$watchdogTaskName = 'RomanVoice Background Watchdog'" in script
    assert "New-ScheduledTaskAction" in script
    assert "New-ScheduledTaskPrincipal" in script
    assert "-LogonType Interactive" in script
    assert "-RunLevel Limited" in script
    assert "New-ScheduledTaskSettingsSet" in script
    assert "-MultipleInstances IgnoreNew" in script
    assert "Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force" in script
    assert "New-ScheduledTaskTrigger -AtLogOn -User $currentUser" in script
    assert "New-ScheduledTaskTrigger -Daily -At $triggerAt" in script
    assert "-RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)" in script
    assert "-RepetitionDuration (New-TimeSpan -Days 1)" in script
    assert "-NoLogo -NoProfile -NonInteractive" in script
    assert "-WindowStyle Hidden" in script
    assert "Assert-RomanVoiceTask" in script
    assert "task:Settings/task:Hidden" in script
    assert '"PT$($IntervalMinutes)M"' in script
    assert "task:Principals/task:Principal/task:UserId" in script
    assert "task:Principals/task:Principal/task:LogonType" in script
    assert "Test-InteractiveCurrentUserPrincipal" in script
    assert "Restore-ManagedTaskSnapshots" in script
    assert "[ok] Rolled back partial scheduled task" in script
    assert "$attemptedTaskNames.Add($startupTaskName)" in script
    assert "$attemptedTaskNames.Add($watchdogTaskName)" in script
    assert "Establish-StartupResidentFallback" in script
    assert "Retire-StartupResidentFallback" in script


def test_watchdog_principal_validation_accepts_native_current_user_representation():
    installer = PROJECT_ROOT / "scripts" / "install-background-watchdog.ps1"
    source = str(installer).replace("'", "''")
    harness = f"""
$installer = Get-Content -Raw -LiteralPath '{source}'
$definitionStart = $installer.IndexOf('function Resolve-AccountSid')
$definitionEnd = $installer.IndexOf('# Install entry point')
$definitions = $installer.Substring($definitionStart, $definitionEnd - $definitionStart)
Invoke-Expression $definitions
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$nativeCurrentUser = [pscustomobject]@{{
    UserId = $env:USERNAME
    LogonType = 'Interactive'
    RunLevel = 'Limited'
}}
if (-not (Test-InteractiveCurrentUserPrincipal -Principal $nativeCurrentUser -ExpectedSid $currentSid)) {{
    throw 'The native ScheduledTasks current-user representation was rejected.'
}}
$passwordPrincipal = [pscustomobject]@{{
    UserId = $env:USERNAME
    LogonType = 'Password'
    RunLevel = 'Limited'
}}
if (Test-InteractiveCurrentUserPrincipal -Principal $passwordPrincipal -ExpectedSid $currentSid) {{
    throw 'Password logon was accepted.'
}}
$servicePrincipal = [pscustomobject]@{{
    UserId = 'SYSTEM'
    LogonType = 'ServiceAccount'
    RunLevel = 'Limited'
}}
if (Test-InteractiveCurrentUserPrincipal -Principal $servicePrincipal -ExpectedSid $currentSid) {{
    throw 'Service principal was accepted.'
}}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", harness],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_watchdog_installer_keeps_the_resident_fallback_hidden_and_safe_to_restore():
    script = (PROJECT_ROOT / "scripts" / "install-background-watchdog.ps1").read_text(
        encoding="utf-8"
    )

    assert '$watchArgs = ' in script
    assert '-File "' in script
    assert '" -IntervalSeconds 60' in script
    assert "-ArgumentList $watchArgs" in script
    assert "Start-ResidentWatchdog" in script
    assert "Remove-ManagedScheduledTasks" in script
    assert "Establish-StartupResidentFallback\n    Remove-ManagedScheduledTasks" in script
    assert "IndexOf($watchScript, [StringComparison]::OrdinalIgnoreCase)" in script
    assert "start_whisper_watcher.vbs" not in script


def test_resident_watchdog_has_single_instance_and_rotating_logs():
    watchdog = (PROJECT_ROOT / "scripts" / "watch-romanvoice-background.ps1").read_text(
        encoding="utf-8"
    )
    common = (PROJECT_ROOT / "scripts" / "romanvoice-watchdog-common.ps1").read_text(
        encoding="utf-8"
    )

    assert "RomanVoiceBackgroundWatchdog" in watchdog
    assert "WaitOne(0)" in watchdog
    assert "ReleaseMutex()" in watchdog
    assert "RomanVoiceWatchdogLogMaxBytes = 2MB" in common
    assert "RomanVoiceWatchdogLogBackupCount = 3" in common
    assert "Rotate-RomanVoiceWatchdogLog" in common
