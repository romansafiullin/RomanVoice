from pathlib import Path


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
    assert "RomanVoiceEnsureRunning" in script
    assert "WaitOne([TimeSpan]::FromSeconds(10))" in script
    assert "Test-AuthenticatedServiceHealth" in script
    assert '"http://127.0.0.1:$Port/v1/health"' in script
    assert 'Authorization = "Bearer $token"' in script
    assert "$failureCount -lt 3" in script
    assert "Stop-Process -Id $ownerPid -Force" in script


def test_cmd_background_launchers_expose_service_for_phone_clients():
    for launcher in ("romanvoice.cmd", "romanvoice-background.cmd"):
        script = (PROJECT_ROOT / "scripts" / launcher).read_text(encoding="utf-8")

        assert "ROMANVOICE_SERVICE_HOST" in script
        assert "0.0.0.0" in script


def test_watchdog_installer_quotes_resident_script_path():
    script = (PROJECT_ROOT / "scripts" / "install-background-watchdog.ps1").read_text(
        encoding="utf-8"
    )

    assert '$watchArgs = ' in script
    assert '-File "' in script
    assert '" -IntervalSeconds 60' in script
    assert "-ArgumentList $watchArgs" in script


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
