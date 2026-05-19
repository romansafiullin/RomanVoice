from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANDROID_IME_ROOT = PROJECT_ROOT / "clients" / "android-ime"


def test_debug_apk_build_uses_durable_debug_keystore():
    script = (ANDROID_IME_ROOT / "build-debug-apk.ps1").read_text(encoding="utf-8")

    assert "android-ime-debug.keystore" in script
    assert "PreviousBuildKeystore" in script
    assert 'if (-not (Test-Path $Keystore))' in script


def test_phone_installer_recovers_from_debug_signature_mismatch():
    script = (ANDROID_IME_ROOT / "install-to-connected-phone.ps1").read_text(
        encoding="utf-8"
    )

    assert "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in script
    assert "uninstall app.romanvoice.ime" in script
