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


def test_android_manifest_declares_quick_settings_tile_service():
    manifest = (ANDROID_IME_ROOT / "app" / "src" / "main" / "AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    styles = (ANDROID_IME_ROOT / "app" / "src" / "main" / "res" / "values" / "styles.xml").read_text(
        encoding="utf-8"
    )
    tile_source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoiceTileService.java"
    ).read_text(encoding="utf-8")
    tile_action_source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoiceTileActionActivity.java"
    ).read_text(encoding="utf-8")

    assert ".RomanVoiceTileService" in manifest
    assert ".RomanVoiceTileActionActivity" in manifest
    assert "@style/TileActionTheme" in manifest
    assert "windowNoDisplay" in styles
    assert "android.permission.BIND_QUICK_SETTINGS_TILE" in manifest
    assert "android.service.quicksettings.action.QS_TILE" in manifest
    assert "@drawable/ic_romanvoice_tile" in manifest
    assert "extends TileService" in tile_source
    assert "RomanVoiceFloatingService.isAvailableForTile()" in tile_source
    assert "startActivityAndCollapseCompat(intent)" in tile_source
    assert "RomanVoiceFloatingService::requestToggleFromTile" in tile_action_source
    assert "TOGGLE_AFTER_FINISH_MS" in tile_action_source
    assert 'tile.setSubtitle("Listening")' in tile_source
    assert 'tile.setSubtitle("Connecting")' in tile_source
    assert 'tile.setSubtitle("Unlock first")' in tile_source
    assert "GLOBAL_ACTION_BACK" not in tile_source


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


def test_floating_service_has_tile_hook_and_cancel_path():
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

    assert "static boolean requestToggleFromTile()" in source
    assert "GLOBAL_ACTION_BACK" not in source
    assert "private void cancelRecording()" in source
    assert "removeLiveDictationText()" in source
    assert 'cancelButton.setText("X")' in source
    assert "private static final boolean SHOW_CANCEL_BUTTON = false" in source
    assert "cancelButton.setOnClickListener(view -> cancelRecording())" in source
    assert "overlayView.setVisibility(View.GONE)" in source
    assert "statusView.setVisibility(View.GONE)" in source
    assert "setPillState(isRecording ? PILL_COLOR_RECORDING : PILL_COLOR_IDLE, isRecording)" in source
    assert "setPillState(PILL_COLOR_RECORDED, true)" in source
    assert "showIdleNotice(\"Tap a text field first\")" in source
    assert "Toast.makeText(this, text, Toast.LENGTH_SHORT).show()" in source


def test_floating_service_replaces_live_dictation_span_not_start_snapshot():
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

    assert "baseText" not in source
    assert "resolveReplacementRange(target, currentText)" in source
    assert "findLiveDictationRange(currentText)" in source
    assert "RomanVoiceTextRange.findLiveDictationRange" in source
    assert "currentText.substring(0, start)" in source


def test_ime_service_has_cancel_path_for_composing_text():
    source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoiceImeService.java"
    ).read_text(encoding="utf-8")

    assert 'cancelButton.setText("Cancel")' in source
    assert "private void cancelRecording()" in source
    assert "clearComposingText()" in source
    assert 'setStatus(wasRecording || hadClient ? "Canceled" : "Ready")' in source


def test_settings_activity_can_prompt_for_quick_settings_tile():
    source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "SettingsActivity.java"
    ).read_text(encoding="utf-8")

    assert "requestAddTileService" in source
    assert "StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ADDED" in source
    assert 'tileButton.setText("Add RomanVoice Quick Settings tile")' in source


def test_floating_service_ignores_message_placeholder_text():
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

    assert "node.getHintText()" in source
    assert "isKnownPlaceholder" in source
    assert "RCS message" in source
    assert "com.google.android.apps.messaging" in source
