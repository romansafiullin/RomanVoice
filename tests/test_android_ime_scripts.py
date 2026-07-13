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
    assert "uninstall $PackageName" in script


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
    preinstall_stop = "& $Adb shell am force-stop $PackageName | Out-Null"
    assert script.index(preinstall_stop) < script.rindex("Install-DebugApk")
    assert "ime set $PackageName/.RomanVoiceImeService" in script
    assert "if ($SetRomanVoiceKeyboard)" in script
    assert "[switch]$PreferLan" in script
    assert "function Resolve-TailscaleExe" in script
    assert "function Resolve-TailscaleIp" in script
    assert "Tailscale\\tailscale.exe" in script
    assert "ws://$Address`:8799/v1/transcribe/stream" in script


def test_phone_installer_fails_closed_to_tailscale_and_preserves_endpoint():
    script = (ANDROID_IME_ROOT / "install-to-connected-phone.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Test-TailscaleHost" in script
    assert "$bytes[0] -eq 100" in script
    assert "$bytes[1] -ge 64 -and $bytes[1] -le 127" in script
    assert "Test-TailscaleStreamUrl $ExistingStreamUrl" in script
    assert "$StreamUrl = $ExistingStreamUrl" in script
    assert "will not silently fall back to LAN" in script
    assert "Assert-ApprovedStreamUrl $StreamUrl" in script
    assert "$PreferLan -and (Test-PrivateLanHost $uri.DnsSafeHost)" in script
    assert "Resolve-LanIp" in script
    assert '<boolean name="allow_lan_stream" value="$AllowLanStream" />' in script
    assert 'Get-PreferenceBooleanValue $ReadbackXml "allow_lan_stream"' in script


def test_phone_installer_streams_secrets_without_temporary_token_files():
    script = (ANDROID_IME_ROOT / "install-to-connected-phone.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Write-AppPreferencesFromStdin" in script
    assert "$Content | & $Adb shell $writeCommand" in script
    assert "Set-Content" not in script
    assert "& $Adb push" not in script
    assert "rm -f $LegacyAndroidPreferencesPath" in script
    assert "Remove-Item -LiteralPath $LegacyWindowsPreferencesPath -Force" in script
    assert "chmod 700 shared_prefs" in script
    assert "chmod 600 $PreferencesPath" in script
    assert "Get-Sha256Fingerprint" in script
    assert "$ReadbackTokenFingerprint -ne $TokenFingerprint" in script
    assert "Token fingerprint verified: sha256:$TokenFingerprint" in script


def test_phone_tile_health_checker_covers_host_heartbeat_and_accessibility_state():
    script = (PROJECT_ROOT / "scripts" / "check-phone-tile-health.ps1").read_text(
        encoding="utf-8"
    )

    assert "/v1/phone/status" in script
    assert "enabled_accessibility_services" in script
    assert '$floatingComponent = "$PackageName/$PackageName.RomanVoiceFloatingService"' in script
    assert "STATE_UNAVAILABLE" not in script
    assert "RomanVoice Floating Mic accessibility service is not enabled" in script
    assert "Get-NetTCPConnection -LocalPort 8799" in script
    assert "0.0.0.0" in script


def test_phone_tile_health_rejects_implicit_lan_and_probes_tailscale_path():
    script = (PROJECT_ROOT / "scripts" / "check-phone-tile-health.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$AllowLanOnly" in script
    assert "Test-TailscaleHost $streamUri.DnsSafeHost" in script
    assert "and $AllowLanOnly" in script
    assert "configured for LAN only" in script
    assert "$TailscalePackageName = 'com.tailscale.ipn'" in script
    assert "pidof $TailscalePackageName" in script
    assert "dumpsys connectivity" in script
    assert "Transports:\\s+.*\\bVPN\\b" in script
    assert "always_on_vpn_app" in script
    assert "function Invoke-PhoneWebSocketProbe" in script
    assert "toybox nc" in script
    assert "101\\s+Switching Protocols" in script
    assert script.count("direct phone-to-service health could not be proven") == 2
    assert "Get-PreferenceBooleanValue $prefsText 'allow_lan_stream'" in script
    assert "LAN developer override is not explicitly disabled" in script
    assert "LAN URL without the installer-only LAN override" in script


def test_phone_tile_health_compares_non_secret_token_fingerprints():
    script = (PROJECT_ROOT / "scripts" / "check-phone-tile-health.ps1").read_text(
        encoding="utf-8"
    )

    assert "Get-Sha256Fingerprint $token" in script
    assert "Get-Sha256Fingerprint $phoneToken" in script
    assert "$phoneTokenFingerprint -eq $desktopTokenFingerprint" in script
    assert "Phone and desktop token fingerprints match" in script
    assert '"Authorization: Bearer $PhoneToken"' in script
    assert "$request | & $AdbPath shell $remoteCommand" in script
    assert "Write-Output $phoneToken" not in script
    assert "Write-Output $token" not in script


def test_phone_tile_health_rejects_stale_android_build_identity():
    script = (PROJECT_ROOT / "scripts" / "check-phone-tile-health.ps1").read_text(
        encoding="utf-8"
    )

    assert "clients\\android-ime\\version.properties" in script
    assert '$ExpectedVersionName = "$($VersionProperties.versionName)-debug"' in script
    assert "dumpsys package $PackageName" in script
    assert "versionCode=(\\d+)" in script
    assert "versionName=([^\\s]+)" in script
    assert "Android build is stale or unexpected" in script


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
    assert "SHOW_CANCEL_BUTTON" not in source
    assert "cancelButton.setOnClickListener(view -> cancelRecording())" in source
    assert 'micButton.setText("Start")' in source
    assert 'micButton.setContentDescription("Start RomanVoice dictation")' in source
    assert '(isRecording ? "Stop" : "Start")' in source
    assert "overlayView.setVisibility(View.GONE)" in source
    assert "private TextView statusView" not in source
    assert "statusView.setVisibility(View.VISIBLE)" not in source
    assert "overlayView.setOnClickListener(view -> toggleRecording())" in source
    assert "RESTART_WINDOW_VISIBLE_MS = 8000" in source
    assert "cancelIdleOverlayHide()" in source
    assert "setPillState(color, true)" in source
    assert "scheduleIdleOverlayHide(RESTART_WINDOW_VISIBLE_MS)" in source
    assert "scheduleIdleOverlayHide(IDLE_NOTICE_VISIBLE_MS)" in source
    assert "phase == RomanVoiceRecordingPhase.ERROR" in source
    assert 'setBusyControls("Finishing", PILL_COLOR_RECORDED)' in source
    assert 'failPreflight("Tap a text field first", "focused_field_missing", false)' in source
    assert "Toast.makeText(this, text, Toast.LENGTH_SHORT).show()" in source
    assert "CONNECTION_FAILED_NOTICE =\n            RomanVoiceConnectionMessage.NETWORK_FAILED" in source
    assert 'handlePhaseTimeout(CONNECTION_FAILED_NOTICE, "connection_timeout")' in source
    assert "RomanVoiceConnectionMessage.from(exception)" in source
    assert "Toast.makeText(this, text, Toast.LENGTH_LONG).show()" in source
    assert "PHONE_HEARTBEAT_INTERVAL_MS" in source
    assert "startPhoneHeartbeat()" in source
    assert "reportPhoneHeartbeat(\"destroyed\", false)" in source
    assert '"connection_failed"' in source
    assert "reportPhoneHeartbeat(event, false)" in source


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
        assert "ERROR_RESET_MS" not in source
        assert "sessionGeneration" in source
        assert "isCurrentSession" in source
        assert "private void setPhase(RomanVoiceRecordingPhase nextPhase)" in source
        assert "clearPhaseWatchdogs()" in source
        assert "isBusyStartingOrFinishing()" in source
        assert "phase != RomanVoiceRecordingPhase.IDLE" in source
        assert "phase == RomanVoiceRecordingPhase.ERROR" in source
        assert "RomanVoiceRecordingPhase.CONNECTING" in source
        assert "RomanVoiceRecordingPhase.FINISHING" in source
        assert "handlePhaseTimeout(\"Finishing timed out - try again" in source
        assert "RomanVoicePreferences.isDefaultStreamUrl(streamUrl)" in source

    assert "TILE_TOGGLE_DEBOUNCE_MS = 750" in floating_source
    assert "lastTileToggleElapsedMs" in floating_source
    assert "setOverlayClickTargetsEnabled(false)" not in floating_source
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
    assert "RomanVoiceKeepAlivePolicy.nextAction" in source
    assert "Action.WAIT" in source
    assert 'setEndpointIdentificationAlgorithm("HTTPS")' in source


def test_android_stream_client_validates_handshake_and_caps_untrusted_input():
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

    assert "MAX_HTTP_HEADER_BYTES = 16384" in source
    assert "MAX_INBOUND_FRAME_BYTES = 4L * 1024L * 1024L" in source
    assert 'responseHeaderValue(response, "Sec-WebSocket-Accept")' in source
    assert "expectedWebSocketAccept(key)" in source
    assert "length > MAX_INBOUND_FRAME_BYTES" in source
    assert source.index("length > MAX_INBOUND_FRAME_BYTES") < source.index(
        "readExact((int) length)"
    )
    assert "buffer.size() >= MAX_HTTP_HEADER_BYTES" in source
    assert "server sent a masked WebSocket frame" in source


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
    assert 'setStatus(wasBusy || hadClient ? "Canceled" : "Ready")' in source


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
    assert "RomanVoicePreferences.isApprovedStreamUrl(this, streamUrl)" in source
    assert "RomanVoicePreferences.allowLanStream(this)" in source
    assert 'tokenField.setError("RomanVoice token is required")' in source


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
    assert source.index('!"com.google.android.apps.messaging".contentEquals(packageName)') < source.index(
        'return "RCS message".equalsIgnoreCase(normalized)'
    )


def test_android_surfaces_fail_closed_and_probe_auth_before_claiming_ready():
    preferences = (
        ANDROID_IME_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "RomanVoicePreferences.java"
    ).read_text(encoding="utf-8")
    floating = (
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
    ime = (
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
    tile = (
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

    assert "KEY_ALLOW_LAN_STREAM" in preferences
    assert "isApprovedStreamUrl(Context context, String streamUrl)" in preferences
    assert "isTailscaleHost(normalizedHost)" in preferences
    assert "allowLanStream && isPrivateLanHost(normalizedHost)" in preferences
    assert "checkIdleServiceHealth();" in floating
    assert "requestHealthCheckForTile()" in floating
    assert "lastSuccessfulHealthCheckElapsedMs" in floating
    assert "!service.isIdleHealthFresh()" in floating
    assert "SystemClock.elapsedRealtime() - lastSuccess < PHONE_HEARTBEAT_INTERVAL_MS" in floating
    assert "if (phase == RomanVoiceRecordingPhase.IDLE)" in floating
    assert 'healthUrl.append("/v1/health")' in floating
    assert '"Authorization", "Bearer " + token.trim()' in floating
    assert "completeSession(generation, RomanVoiceRecordingPhase.IDLE)" in floating
    assert "keepOverlayHidden" in floating
    assert 'handleStreamError(generation, finalFailure, "idle_health_failed")' in floating
    assert "retryFailureNotice = failureNotice" in floating
    assert "canceledUnverifiedRetry" in floating
    assert "markConnectionVerified(generation)" in floating
    assert floating.count("if (idleHealthCheck)") >= 2
    assert "invalidateSession(RomanVoiceRecordingPhase.IDLE);" in floating
    assert "RomanVoiceFloatingService.requestHealthCheckForTile();" in tile
    assert "RomanVoiceRecordingPhase.ERROR" in ime
    assert "if (!finalHealthy)" in ime


def test_android_runtime_keeps_failures_visible_and_cancel_available_while_busy():
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

    assert "scheduleErrorReset" not in floating_source
    assert "scheduleErrorReset" not in ime_source
    assert "return TileState.ERROR" in floating_source
    assert "case ERROR:" in tile_source
    assert 'setBusyControls("Connecting", PILL_COLOR_CONNECTING)' in floating_source
    assert 'setBusyControls("Finishing", PILL_COLOR_RECORDED)' in floating_source
    assert "cancelButton.setVisibility(View.VISIBLE)" in floating_source
    assert "cancelButton.setVisibility(View.VISIBLE)" in ime_source
    assert "isBusyStartingOrFinishing()) {\n            cancelRecording();" in floating_source
    assert "isBusyStartingOrFinishing()) {\n            cancelRecording();" in ime_source


def test_floating_insertion_is_pinned_to_expected_field_and_content():
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

    assert "targetFingerprint = fingerprint(node)" in source
    assert "expectedFieldText = currentText" in source
    assert "!targetFingerprint.equals(fingerprint(target))" in source
    assert "RomanVoiceTextRange.hasExpectedContent(currentText, expectedFieldText)" in source
    assert "Text field changed - dictation preserved" in source
    assert "expectedFieldText = nextText" in source
    assert "bestPartialText" in source
    assert "RomanVoiceTextRange.chooseFinalText(text, bestPartialText)" in source
    assert "if (next.trim().isEmpty())" in source


def test_android_stream_shutdown_and_audio_errors_are_terminal_and_generation_guarded():
    stream_source = (
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

    assert "notifyUnexpectedDisconnect" in stream_source
    assert "RomanVoice stream closed unexpectedly" in stream_source
    assert "interruptAndJoin(readerThread)" in stream_source
    assert "thread.join(THREAD_JOIN_TIMEOUT_MS)" in floating_source
    assert "thread.join(THREAD_JOIN_TIMEOUT_MS)" in ime_source
    assert "if (read < 0)" in floating_source
    assert "if (read < 0)" in ime_source
    assert "handleRecordingError" in floating_source
    assert "handleRecordingError" in ime_source
    assert "isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)" in floating_source
    assert "isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)" in ime_source
    assert "new StreamListener(generation)" in floating_source
    assert "new StreamListener(generation)" in ime_source
    assert "activateClientSession(generation, streamClient)" in floating_source
    assert "activateClientSession(generation, streamClient)" in ime_source


def test_android_requests_identify_non_secret_client_surface():
    stream_source = (
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
    heartbeat_source = (
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

    assert "X-RomanVoice-Client" in stream_source
    assert '"android-floating"' in (
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
    assert '"android-ime"' in ime_source
    assert '"android-ime-health"' in ime_source
    assert "X-RomanVoice-Client" in heartbeat_source


def test_floating_failure_heartbeats_report_unavailable():
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

    assert "reportPhoneHeartbeat(event, false)" in source
    assert "phase != RomanVoiceRecordingPhase.ERROR" in source
