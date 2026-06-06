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
    assert "[switch]$PreferLan" in script
    assert "function Resolve-TailscaleExe" in script
    assert "function Resolve-TailscaleIp" in script
    assert "Tailscale\\tailscale.exe" in script
    assert "ws://$Address`:8799/v1/transcribe/stream" in script


def test_phone_tile_health_checker_covers_host_heartbeat_and_accessibility_state():
    script = (PROJECT_ROOT / "scripts" / "check-phone-tile-health.ps1").read_text(
        encoding="utf-8"
    )

    assert "/v1/phone/status" in script
    assert "enabled_accessibility_services" in script
    assert "app.romanvoice.ime/app.romanvoice.ime.RomanVoiceFloatingService" in script
    assert "STATE_UNAVAILABLE" not in script
    assert "RomanVoice Floating Mic accessibility service is not enabled" in script
    assert "Get-NetTCPConnection -LocalPort 8799" in script
    assert "0.0.0.0" in script


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
    assert 'tile.setSubtitle("Recording")' in tile_source
    assert 'tile.setSubtitle("Connecting")' in tile_source
    assert 'tile.setSubtitle("Finishing")' in tile_source
    assert 'tile.setSubtitle("Ready")' in tile_source
    assert 'tile.setContentDescription("RomanVoice ready. Tap to start dictation.")' in tile_source
    assert 'tile.setContentDescription("RomanVoice recording. Tap to stop dictation.")' in tile_source
    assert 'tile.setSubtitle("Unlock first")' in tile_source
    assert 'tile.setSubtitle("Enable Floating Mic")' in tile_source
    assert 'tile.setContentDescription("Enable RomanVoice Floating Mic before dictating.")' in tile_source
    assert 'tile.setState(Tile.STATE_INACTIVE)' in tile_source
    assert "RomanVoicePhoneHeartbeat.reportAsync" in tile_source
    assert "floating_service_unavailable" in tile_source
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
    assert 'micButton.setText("Start")' in source
    assert 'micButton.setContentDescription("Start RomanVoice dictation")' in source
    assert 'micButton.setText(isRecording ? "Stop" : "Start")' in source
    assert "overlayView.setVisibility(View.GONE)" in source
    assert "private TextView statusView" not in source
    assert "statusView.setVisibility(View.VISIBLE)" not in source
    assert "overlayView.setOnClickListener(view -> toggleRecording())" in source
    assert "RESTART_WINDOW_VISIBLE_MS = 8000" in source
    assert "cancelIdleOverlayHide()" in source
    assert "setPillState(isRecording ? PILL_COLOR_RECORDING : PILL_COLOR_IDLE, true)" in source
    assert "scheduleIdleOverlayHide(RESTART_WINDOW_VISIBLE_MS)" in source
    assert "scheduleIdleOverlayHide(IDLE_NOTICE_VISIBLE_MS)" in source
    assert "setPillState(PILL_COLOR_RECORDED, true)" in source
    assert "showIdleNotice(\"Tap a text field first\")" in source
    assert "Toast.makeText(this, text, Toast.LENGTH_SHORT).show()" in source
    assert 'CONNECTION_FAILED_NOTICE = "No PC connection - check Wi-Fi/VPN"' in source
    assert 'handlePhaseTimeout(CONNECTION_FAILED_NOTICE, "connection_timeout")' in source
    assert "showFailureNotice(CONNECTION_FAILED_NOTICE)" in source
    assert "Toast.makeText(this, text, Toast.LENGTH_LONG).show()" in source
    assert "PHONE_HEARTBEAT_INTERVAL_MS" in source
    assert "startPhoneHeartbeat()" in source
    assert "reportPhoneHeartbeat(\"destroyed\", false)" in source
    assert "reportPhoneHeartbeat(\"connection_failed\")" in source


def test_android_phone_heartbeat_posts_to_host_service():
    source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoicePhoneHeartbeat.java"
    ).read_text(encoding="utf-8")

    assert "HttpURLConnection" in source
    assert 'url.append("/v1/phone/heartbeat")' in source
    assert 'connection.setRequestProperty("Authorization", "Bearer " + token)' in source
    assert 'payload.put("surface"' in source
    assert 'payload.put("event"' in source
    assert 'payload.put("available"' in source


def test_floating_service_retries_focus_after_quick_settings_tile():
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

    assert "TILE_FOCUS_RETRY_COUNT" in source
    assert "TILE_FOCUS_RETRY_DELAY_MS" in source
    assert "startRecording(true, TILE_FOCUS_RETRY_COUNT)" in source
    assert "scheduleTileFocusRetry(retriesRemaining - 1)" in source
    assert "setStatus(\"Finding field\")" in source
    assert "cancelTileFocusRetry()" in source


def test_android_voice_surfaces_use_phase_guards_and_watchdogs():
    floating_source = (
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
    ime_source = (
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
    phase_source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoiceRecordingPhase.java"
    ).read_text(encoding="utf-8")

    for phase in ["IDLE", "CONNECTING", "RECORDING", "FINISHING", "ERROR"]:
        assert phase in phase_source

    for source in [floating_source, ime_source]:
        assert "CONNECTING_TIMEOUT_MS = 10000" in source
        assert "STOP_SEND_TIMEOUT_MS = 10000" in source
        assert "FINAL_RESULT_TIMEOUT_MS = 90000" in source
        assert "ERROR_RESET_MS = 3000" in source
        assert "private void setPhase(RomanVoiceRecordingPhase nextPhase)" in source
        assert "clearPhaseWatchdogs()" in source
        assert "isBusyStartingOrFinishing()" in source
        assert "phase != RomanVoiceRecordingPhase.IDLE" in source
        assert "phase == RomanVoiceRecordingPhase.ERROR" in source
        assert "phase != RomanVoiceRecordingPhase.CONNECTING" in source
        assert "phase != RomanVoiceRecordingPhase.FINISHING" in source
        assert "handlePhaseTimeout(\"Finishing timed out - try again" in source
        assert "RomanVoicePreferences.isDefaultStreamUrl(streamUrl)" in source

    assert "TILE_TOGGLE_DEBOUNCE_MS = 750" in floating_source
    assert "lastTileToggleElapsedMs" in floating_source
    assert "setOverlayClickTargetsEnabled(false)" in floating_source
    assert "setOverlayClickTargetsEnabled(true)" in floating_source


def test_android_audio_loops_snapshot_shared_recorder_and_client():
    floating_source = (
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
    ime_source = (
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

    for source in [floating_source, ime_source]:
        assert "private volatile AudioRecord audioRecord;" in source
        assert "private volatile RomanVoiceStreamClient client;" in source
        assert "AudioRecord record = audioRecord;" in source
        assert "RomanVoiceStreamClient streamClient = client;" in source
        assert "record.read(buffer, 0, buffer.length)" in source
        assert "streamClient.sendAudio(buffer, read)" in source
        assert "catch (RuntimeException exception)" in source


def test_android_stream_client_uses_connect_timeout_and_ping_keepalive():
    source = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoiceStreamClient.java"
    ).read_text(encoding="utf-8")

    assert "CONNECT_TIMEOUT_MS = 10000" in source
    assert "PING_INTERVAL_MS = 5000" in source
    assert "PONG_TIMEOUT_MS = 12000" in source
    assert "socket.connect(new InetSocketAddress" in source
    assert "socket.setSoTimeout(0)" in source
    assert "keepAliveThread" in source
    assert "sendFrame(0x9, new byte[]{})" in source
    assert "frame.opcode == 0xA" in source
    assert "outstandingPingAtMs = 0" in source
    assert "RomanVoice stream ping timed out" in source


def test_floating_service_falls_back_to_focused_editable_descendant():
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

    assert "root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)" in source
    assert "findFocusedEditableDescendant(root)" in source
    assert "node.isAccessibilityFocused()" in source


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
