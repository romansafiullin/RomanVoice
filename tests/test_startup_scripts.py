from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_background_launcher_exposes_service_for_phone_clients():
    script = (PROJECT_ROOT / "scripts" / "ensure-romanvoice-running.ps1").read_text(
        encoding="utf-8"
    )

    assert "ROMANVOICE_SERVICE_HOST" in script
    assert "'0.0.0.0'" in script


def test_cmd_background_launchers_expose_service_for_phone_clients():
    for launcher in ("romanvoice.cmd", "romanvoice-background.cmd"):
        script = (PROJECT_ROOT / "scripts" / launcher).read_text(encoding="utf-8")

        assert "ROMANVOICE_SERVICE_HOST" in script
        assert "0.0.0.0" in script
