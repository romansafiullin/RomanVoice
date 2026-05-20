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


def test_phone_installer_defaults_to_floating_mic_workflow():
    script = (ANDROID_IME_ROOT / "install-to-connected-phone.ps1").read_text(
        encoding="utf-8"
    )

    assert "[bool]$EnableFloatingMic = $true" in script
    assert "[switch]$SetRomanVoiceKeyboard" in script
    assert "Enable-FloatingMicService" in script
    assert "expandedComponent" in script
    assert "enabled_accessibility_services \"$next\"" in script
    assert "not enabled by Android" in script
    assert "Resolve-NormalKeyboard" in script
    preinstall_stop = "& $Adb shell am force-stop app.romanvoice.ime | Out-Null"
    assert script.index(preinstall_stop) < script.rindex("Install-DebugApk")
    assert "ime set app.romanvoice.ime/.RomanVoiceImeService" in script
    assert "if ($SetRomanVoiceKeyboard)" in script


def test_android_manifest_declares_floating_accessibility_service():
    manifest = (ANDROID_IME_ROOT / "app" / "src" / "main" / "AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    service_xml = (
        ANDROID_IME_ROOT / "app" / "src" / "main" / "res" / "xml" / "accessibility_service.xml"
    ).read_text(encoding="utf-8")

    assert ".RomanVoiceFloatingService" in manifest
    assert "android.permission.BIND_ACCESSIBILITY_SERVICE" in manifest
    assert "@xml/accessibility_service" in manifest
    assert "android:canRetrieveWindowContent=\"true\"" in service_xml


def test_floating_service_uses_accessibility_overlay_and_set_text():
    source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoiceFloatingService.java"
    ).read_text(encoding="utf-8")

    assert "TYPE_ACCESSIBILITY_OVERLAY" in source
    assert "ACTION_SET_TEXT" in source
    assert "RomanVoiceStreamClient" in source
