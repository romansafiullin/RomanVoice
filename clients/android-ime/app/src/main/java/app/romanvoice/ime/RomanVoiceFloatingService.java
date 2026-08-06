package app.romanvoice.ime;

import android.Manifest;
import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.Toast;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;

public class RomanVoiceFloatingService extends AccessibilityService {
    private static final String TAG = "RomanVoiceFloat";
    private static final int SAMPLE_RATE = 16000;
    private static final int PILL_COLOR_IDLE = 0xEE2F7D4C;
    private static final int PILL_COLOR_CONNECTING = 0xEE5E6252;
    private static final int PILL_COLOR_RECORDING = 0xEEC8372D;
    private static final int PILL_COLOR_RECORDED = 0xEE2F7D4C;
    private static final int PILL_COLOR_ERROR = 0xEE7A3129;
    private static final int TILE_FOCUS_RETRY_COUNT = 8;
    private static final long TILE_FOCUS_RETRY_DELAY_MS = 150;
    private static final long IDLE_NOTICE_VISIBLE_MS = 1800;
    private static final long RESTART_WINDOW_VISIBLE_MS = 8000;
    private static final long PHONE_HEARTBEAT_INTERVAL_MS = 60000;
    private static final long CONNECTING_TIMEOUT_MS = 10000;
    private static final long STOP_SEND_TIMEOUT_MS = 10000;
    private static final long FINAL_RESULT_TIMEOUT_MS = 90000;
    private static final long TILE_TOGGLE_DEBOUNCE_MS = 750;
    private static final long THREAD_JOIN_TIMEOUT_MS = 750;
    private static final String CONNECTION_FAILED_NOTICE =
            RomanVoiceConnectionMessage.NETWORK_FAILED;

    private static volatile RomanVoiceFloatingService activeService;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private WindowManager windowManager;
    private WindowManager.LayoutParams overlayParams;
    private LinearLayout overlayView;
    private Button micButton;
    private Button cancelButton;
    private Runnable hideIdleOverlayRunnable;
    private Runnable tileFocusRetryRunnable;
    private Runnable phoneHeartbeatRunnable;
    private Runnable connectingTimeoutRunnable;
    private Runnable stopSendTimeoutRunnable;
    private Runnable finalResultTimeoutRunnable;

    private volatile RomanVoiceRecordingPhase phase = RomanVoiceRecordingPhase.IDLE;
    private volatile AudioRecord audioRecord;
    private volatile Thread audioThread;
    private volatile RomanVoiceStreamClient client;
    private volatile int sessionGeneration;
    private volatile int clientGeneration;
    private volatile int verifiedConnectionGeneration = -1;
    private volatile boolean idleHealthCheck;
    private volatile boolean retryableIdleHealthFailure;
    private volatile long lastSuccessfulHealthCheckElapsedMs;
    private volatile String retryFailureNotice = "";
    private volatile String failureReason = "";
    private long lastTileToggleElapsedMs;

    private int insertionStart = 0;
    private int insertionEnd = 0;
    private String lastDictationText = "";
    private String bestPartialText = "";
    private String expectedFieldText = "";
    private String targetFingerprint = "";
    private volatile String failureNotice = "";

    static boolean isAvailableForTile() {
        return activeService != null;
    }

    static boolean isRecordingForTile() {
        RomanVoiceFloatingService service = activeService;
        return service != null && service.phase == RomanVoiceRecordingPhase.RECORDING;
    }

    static TileState getTileStateForTile() {
        RomanVoiceFloatingService service = activeService;
        if (service == null) {
            return TileState.UNAVAILABLE;
        }
        if (service.phase == RomanVoiceRecordingPhase.RECORDING) {
            return TileState.LISTENING;
        }
        if (service.phase == RomanVoiceRecordingPhase.CONNECTING) {
            return TileState.CONNECTING;
        }
        if (service.phase == RomanVoiceRecordingPhase.FINISHING) {
            return TileState.FINISHING;
        }
        if (service.phase == RomanVoiceRecordingPhase.ERROR) {
            return TileState.ERROR;
        }
        return TileState.READY;
    }

    static String getTileFailureForTile() {
        RomanVoiceFloatingService service = activeService;
        return service == null ? "" : service.failureNotice;
    }

    static void requestHealthCheckForTile() {
        RomanVoiceFloatingService service = activeService;
        if (service != null
                && service.phase == RomanVoiceRecordingPhase.IDLE
                && !service.isIdleHealthFresh()) {
            service.checkIdleServiceHealth();
        }
    }

    static boolean requestToggleFromTile() {
        RomanVoiceFloatingService service = activeService;
        if (service == null) {
            return false;
        }
        long now = SystemClock.elapsedRealtime();
        if (now - service.lastTileToggleElapsedMs < TILE_TOGGLE_DEBOUNCE_MS) {
            return true;
        }
        service.lastTileToggleElapsedMs = now;
        service.mainHandler.post(service::toggleRecordingFromTile);
        return true;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        activeService = this;
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        showOverlay();
        checkIdleServiceHealth();
        startPhoneHeartbeat();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (phase == RomanVoiceRecordingPhase.IDLE
                && overlayView != null
                && overlayView.getVisibility() == View.VISIBLE) {
            AccessibilityNodeInfo node = findFocusedEditableNode();
            setStatus(node == null ? "Tap a text field" : "Ready");
            recycleNode(node);
        }
    }

    @Override
    public void onInterrupt() {
        stopRecording(false);
    }

    @Override
    public void onDestroy() {
        stopRecording(false, false);
        reportPhoneHeartbeat("destroyed", false, false, false, "destroyed");
        cancelPhoneHeartbeat();
        clearPhaseWatchdogs();
        removeOverlay();
        if (activeService == this) {
            activeService = null;
        }
        notifyTileStateChanged();
        super.onDestroy();
    }

    private void showOverlay() {
        if (overlayView != null || windowManager == null) {
            return;
        }

        overlayView = new LinearLayout(this);
        overlayView.setOrientation(LinearLayout.HORIZONTAL);
        overlayView.setGravity(Gravity.CENTER_VERTICAL);
        overlayView.setPadding(dp(8), dp(6), dp(8), dp(6));
        setPillColor(PILL_COLOR_IDLE);

        micButton = new Button(this);
        micButton.setText("Start");
        micButton.setContentDescription("Start RomanVoice dictation");
        micButton.setTextColor(Color.WHITE);
        micButton.setBackgroundColor(Color.TRANSPARENT);
        micButton.setMinWidth(0);
        micButton.setMinHeight(0);
        micButton.setPadding(0, 0, 0, 0);
        micButton.setOnClickListener(view -> toggleRecording());
        micButton.setOnLongClickListener(view -> {
            cancelRecording();
            return true;
        });
        overlayView.addView(micButton, new LinearLayout.LayoutParams(dp(54), dp(46)));

        cancelButton = new Button(this);
        cancelButton.setText("X");
        cancelButton.setTextColor(Color.WHITE);
        cancelButton.setContentDescription("Cancel dictation");
        cancelButton.setVisibility(View.GONE);
        cancelButton.setOnClickListener(view -> cancelRecording());
        overlayView.addView(cancelButton, new LinearLayout.LayoutParams(dp(46), dp(46)));

        overlayView.setOnClickListener(view -> toggleRecording());
        overlayView.setOnLongClickListener(view -> {
            cancelRecording();
            return true;
        });
        overlayView.setOnTouchListener(new DragTouchListener());

        overlayParams = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
                PixelFormat.TRANSLUCENT
        );
        overlayParams.gravity = Gravity.TOP | Gravity.START;
        overlayParams.x = dp(16);
        overlayParams.y = dp(160);

        windowManager.addView(overlayView, overlayParams);
        overlayView.setVisibility(View.GONE);
    }

    private void removeOverlay() {
        if (overlayView != null && windowManager != null) {
            windowManager.removeView(overlayView);
            overlayView = null;
        }
    }

    private boolean isRecording() {
        return phase == RomanVoiceRecordingPhase.RECORDING;
    }

    private boolean isBusyStartingOrFinishing() {
        return phase == RomanVoiceRecordingPhase.CONNECTING
                || phase == RomanVoiceRecordingPhase.FINISHING;
    }

    private void setPhase(RomanVoiceRecordingPhase nextPhase) {
        clearPhaseWatchdogs();
        phase = nextPhase;
        if (nextPhase == RomanVoiceRecordingPhase.CONNECTING) {
            scheduleConnectingTimeout();
        } else if (nextPhase == RomanVoiceRecordingPhase.FINISHING) {
            scheduleStopSendTimeout();
            scheduleFinalResultTimeout();
        }
    }

    private void clearPhaseWatchdogs() {
        if (connectingTimeoutRunnable != null) {
            mainHandler.removeCallbacks(connectingTimeoutRunnable);
            connectingTimeoutRunnable = null;
        }
        if (stopSendTimeoutRunnable != null) {
            mainHandler.removeCallbacks(stopSendTimeoutRunnable);
            stopSendTimeoutRunnable = null;
        }
        if (finalResultTimeoutRunnable != null) {
            mainHandler.removeCallbacks(finalResultTimeoutRunnable);
            finalResultTimeoutRunnable = null;
        }
    }

    private void scheduleConnectingTimeout() {
        connectingTimeoutRunnable = () -> {
            connectingTimeoutRunnable = null;
            if (phase == RomanVoiceRecordingPhase.CONNECTING) {
                handlePhaseTimeout(CONNECTION_FAILED_NOTICE, "connection_timeout");
            }
        };
        mainHandler.postDelayed(connectingTimeoutRunnable, CONNECTING_TIMEOUT_MS);
    }

    private void scheduleStopSendTimeout() {
        stopSendTimeoutRunnable = () -> {
            stopSendTimeoutRunnable = null;
            if (phase == RomanVoiceRecordingPhase.FINISHING) {
                handlePhaseTimeout("Finishing timed out - try again", "stop_timeout");
            }
        };
        mainHandler.postDelayed(stopSendTimeoutRunnable, STOP_SEND_TIMEOUT_MS);
    }

    private void scheduleFinalResultTimeout() {
        finalResultTimeoutRunnable = () -> {
            finalResultTimeoutRunnable = null;
            if (phase == RomanVoiceRecordingPhase.FINISHING) {
                handlePhaseTimeout("Finishing timed out - try again", "final_timeout");
            }
        };
        mainHandler.postDelayed(finalResultTimeoutRunnable, FINAL_RESULT_TIMEOUT_MS);
    }

    private void cancelStopSendTimeout() {
        if (stopSendTimeoutRunnable != null) {
            mainHandler.removeCallbacks(stopSendTimeoutRunnable);
            stopSendTimeoutRunnable = null;
        }
    }

    private void handlePhaseTimeout(String message, String event) {
        handleStreamError(sessionGeneration, message, event);
    }

    private void toggleRecording() {
        if (idleHealthCheck) {
            idleHealthCheck = false;
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        } else if (isRecording()) {
            stopRecording(true);
        } else if (phase == RomanVoiceRecordingPhase.ERROR) {
            retryFailureNotice = failureNotice;
            failureNotice = "";
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        } else if (!isBusyStartingOrFinishing()) {
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        }
    }

    private void toggleRecordingFromTile() {
        if (idleHealthCheck) {
            idleHealthCheck = false;
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        } else if (isRecording()) {
            cancelTileFocusRetry();
            stopRecording(true);
        } else if (isBusyStartingOrFinishing()) {
            cancelRecording();
        } else if (phase == RomanVoiceRecordingPhase.ERROR) {
            retryFailureNotice = failureNotice;
            failureNotice = "";
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        } else if (!isBusyStartingOrFinishing()) {
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        }
    }

    private void startRecording(boolean retryMissingFocus, int retriesRemaining) {
        if (phase != RomanVoiceRecordingPhase.IDLE) {
            return;
        }
        retryableIdleHealthFailure = false;
        failureReason = "";
        cancelIdleOverlayHide();
        if (!hasRecordPermission()) {
            cancelTileFocusRetry();
            failPreflight("Microphone permission needed", "microphone_permission", true);
            return;
        }

        AccessibilityNodeInfo target = findFocusedEditableNode();
        if (target == null) {
            if (retryMissingFocus && retriesRemaining > 0) {
                scheduleTileFocusRetry(retriesRemaining - 1);
                return;
            }
            Log.i(TAG, "Tile/start ignored: no focused editable field");
            cancelTileFocusRetry();
            failPreflight("Tap a text field first", "focused_field_missing", false);
            return;
        }

        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (streamUrl == null || streamUrl.trim().isEmpty() || RomanVoicePreferences.isDefaultStreamUrl(streamUrl)) {
            cancelTileFocusRetry();
            recycleNode(target);
            failPreflight("Set RomanVoice URL", "stream_url_missing", true);
            return;
        }
        if (!RomanVoicePreferences.isApprovedStreamUrl(this, streamUrl)) {
            cancelTileFocusRetry();
            recycleNode(target);
            failPreflight("Use the Tailscale RomanVoice URL", "stream_url_invalid", true);
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            cancelTileFocusRetry();
            recycleNode(target);
            failPreflight("Set RomanVoice token", "service_token_missing", true);
            return;
        }
        cancelTileFocusRetry();
        captureInsertionState(target);
        recycleNode(target);

        int generation = beginSession();
        notifyTileStateChanged();
        reportPhoneHeartbeat("connecting");
        setBusyControls("Connecting", PILL_COLOR_CONNECTING);

        new Thread(() -> {
            RomanVoiceStreamClient streamClient = null;
            try {
                Log.i(TAG, "Connecting to RomanVoice stream: " + streamUrl);
                streamClient = new RomanVoiceStreamClient(
                        streamUrl,
                        token,
                        "android-floating",
                        new StreamListener(generation)
                );
                streamClient.connect();
                markConnectionVerified(generation);
                streamClient.sendStart(SAMPLE_RATE, RomanVoicePreferences.polish(this));
                if (!activateClientSession(generation, streamClient)) {
                    streamClient.close();
                    return;
                }
                startAudioPump(generation, streamClient);
                mainHandler.post(() -> {
                    if (isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
                        setRecordingControls(true);
                        notifyTileStateChanged();
                        reportPhoneHeartbeat("recording");
                    }
                });
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice floating connection failed", exception);
                if (streamClient != null) {
                    streamClient.close();
                }
                mainHandler.post(() -> {
                    if (isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
                        handleStreamError(
                                generation,
                                RomanVoiceConnectionMessage.from(exception),
                                "connection_failed"
                        );
                    }
                });
            }
        }, "RomanVoiceFloatConnect").start();
    }

    private void failPreflight(String message, String event, boolean openSettingsPage) {
        failPreflight(message, event, openSettingsPage, false);
    }

    private void failPreflight(
            String message,
            String event,
            boolean openSettingsPage,
            boolean silentOnPhoneUi
    ) {
        invalidateSession(RomanVoiceRecordingPhase.ERROR);
        retryableIdleHealthFailure = false;
        retryFailureNotice = "";
        failureReason = event;
        failureNotice = message;
        setRecordingControls(false);
        if (silentOnPhoneUi) {
            hideIdleHealthOverlay();
        } else {
            showFailureNotice(message);
        }
        notifyTileStateChanged();
        reportPhoneHeartbeat(event, false);
        if (openSettingsPage) {
            openSettings();
        }
    }

    private void checkIdleServiceHealth() {
        if (phase != RomanVoiceRecordingPhase.IDLE) {
            return;
        }
        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (!RomanVoicePreferences.isApprovedStreamUrl(this, streamUrl)) {
            failPreflight("Use the Tailscale RomanVoice URL", "idle_stream_url_invalid", false, true);
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            failPreflight("Set RomanVoice token", "idle_service_token_missing", false, true);
            return;
        }

        idleHealthCheck = true;
        int generation = beginSession();
        setStatus("Checking RomanVoice");
        setPillColor(PILL_COLOR_CONNECTING);
        notifyTileStateChanged();

        new Thread(() -> {
            HttpURLConnection connection = null;
            String failure = null;
            String errorReason = "";
            boolean retryableFailure = false;
            try {
                URI uri = URI.create(streamUrl);
                String scheme = "wss".equalsIgnoreCase(uri.getScheme()) ? "https" : "http";
                StringBuilder healthUrl = new StringBuilder();
                healthUrl.append(scheme).append("://").append(uri.getHost());
                if (uri.getPort() >= 0) {
                    healthUrl.append(":").append(uri.getPort());
                }
                healthUrl.append("/v1/health");

                connection = (HttpURLConnection) new URL(healthUrl.toString()).openConnection();
                connection.setConnectTimeout(2000);
                connection.setReadTimeout(2000);
                connection.setRequestProperty("Authorization", "Bearer " + token.trim());
                connection.setRequestProperty("X-RomanVoice-Client", "android-floating-health");
                int code = connection.getResponseCode();
                if (code == 401 || code == 403) {
                    failure = RomanVoiceConnectionMessage.AUTH_FAILED;
                    errorReason = "idle_health_auth_failed";
                } else if (code != 200) {
                    failure = "RomanVoice unavailable (HTTP " + code + ")";
                    errorReason = "idle_health_http_" + code;
                    retryableFailure = isRetryableIdleHealthHttpCode(code);
                }
            } catch (Exception exception) {
                failure = RomanVoiceConnectionMessage.from(exception);
                if (RomanVoiceConnectionMessage.NETWORK_FAILED.equals(failure)) {
                    errorReason = "idle_health_network_failed";
                    retryableFailure = true;
                } else if (RomanVoiceConnectionMessage.AUTH_FAILED.equals(failure)) {
                    errorReason = "idle_health_auth_failed";
                } else {
                    errorReason = "idle_health_stream_failed";
                }
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }

            String finalFailure = failure;
            String finalErrorReason = errorReason;
            boolean finalRetryableFailure = retryableFailure;
            mainHandler.post(() -> {
                if (finalFailure == null) {
                    if (!completeSession(generation, RomanVoiceRecordingPhase.IDLE)) {
                        return;
                    }
                    idleHealthCheck = false;
                    retryableIdleHealthFailure = false;
                    lastSuccessfulHealthCheckElapsedMs = SystemClock.elapsedRealtime();
                    retryFailureNotice = "";
                    failureReason = "";
                    failureNotice = "";
                    boolean keepOverlayHidden = overlayView == null
                            || overlayView.getVisibility() != View.VISIBLE;
                    setRecordingControls(false);
                    if (keepOverlayHidden && overlayView != null) {
                        overlayView.setVisibility(View.GONE);
                    }
                    notifyTileStateChanged();
                    reportPhoneHeartbeat("ready");
                } else if (isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
                    handleStreamError(
                            generation,
                            finalFailure,
                            "idle_health_failed",
                            finalErrorReason,
                            finalRetryableFailure
                    );
                }
            });
        }, "RomanVoiceFloatHealth").start();
    }

    private void retryIdleServiceHealth() {
        if (phase != RomanVoiceRecordingPhase.ERROR || !retryableIdleHealthFailure) {
            return;
        }
        invalidateSession(RomanVoiceRecordingPhase.IDLE);
        checkIdleServiceHealth();
    }

    private static boolean isRetryableIdleHealthHttpCode(int code) {
        return code == 408
                || code == 429
                || code == 502
                || code == 503
                || code == 504;
    }

    private boolean isIdleHealthFresh() {
        long lastSuccess = lastSuccessfulHealthCheckElapsedMs;
        return lastSuccess > 0
                && SystemClock.elapsedRealtime() - lastSuccess < PHONE_HEARTBEAT_INTERVAL_MS;
    }

    private void scheduleTileFocusRetry(int retriesRemaining) {
        if (tileFocusRetryRunnable != null) {
            mainHandler.removeCallbacks(tileFocusRetryRunnable);
        }
        setStatus("Finding field");
        setPillState(PILL_COLOR_CONNECTING, true);
        tileFocusRetryRunnable = () -> {
            tileFocusRetryRunnable = null;
            if (phase == RomanVoiceRecordingPhase.IDLE) {
                startRecording(true, retriesRemaining);
            }
        };
        mainHandler.postDelayed(tileFocusRetryRunnable, TILE_FOCUS_RETRY_DELAY_MS);
    }

    private void cancelTileFocusRetry() {
        if (tileFocusRetryRunnable != null) {
            mainHandler.removeCallbacks(tileFocusRetryRunnable);
            tileFocusRetryRunnable = null;
        }
    }

    private void startAudioPump(
            int generation,
            RomanVoiceStreamClient streamClient
    ) throws IOException {
        int minBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
        );
        int bufferSize = Math.max(minBuffer, SAMPLE_RATE / 5 * 2);
        AudioRecord nextRecord = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize * 2
        );
        if (nextRecord.getState() != AudioRecord.STATE_INITIALIZED) {
            nextRecord.release();
            throw new IOException("Microphone failed to initialize");
        }

        if (!isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
            nextRecord.release();
            return;
        }
        try {
            nextRecord.startRecording();
        } catch (RuntimeException exception) {
            nextRecord.release();
            throw new IOException("Microphone failed to start", exception);
        }
        if (!activateAudioSession(generation, nextRecord)) {
            try {
                nextRecord.stop();
            } catch (IllegalStateException ignored) {
            }
            nextRecord.release();
            return;
        }
        audioThread = new Thread(() -> {
            byte[] buffer = new byte[bufferSize];
            while (isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
                AudioRecord record = audioRecord;
                if (record == null || record != nextRecord) {
                    break;
                }
                int read;
                try {
                    read = record.read(buffer, 0, buffer.length);
                } catch (RuntimeException exception) {
                    Log.w(TAG, "Floating audio read failed", exception);
                    mainHandler.post(() -> handleRecordingError(
                            generation,
                            "Microphone stopped - try again",
                            "audio_read_failed"
                    ));
                    break;
                }
                if (read < 0) {
                    int errorCode = read;
                    Log.w(TAG, "Floating audio read returned error " + errorCode);
                    mainHandler.post(() -> handleRecordingError(
                            generation,
                            "Microphone stopped - try again",
                            "audio_read_failed"
                    ));
                    break;
                }
                if (read > 0) {
                    try {
                        streamClient.sendAudio(buffer, read);
                    } catch (IOException exception) {
                        Log.w(TAG, "Failed to send floating audio chunk", exception);
                        mainHandler.post(() -> handleRecordingError(
                                generation,
                                RomanVoiceConnectionMessage.from(exception),
                                "audio_send_failed"
                        ));
                        break;
                    } catch (RuntimeException exception) {
                        Log.w(TAG, "Floating audio pump failed", exception);
                        mainHandler.post(() -> handleRecordingError(
                                generation,
                                RomanVoiceConnectionMessage.from(exception),
                                "audio_send_failed"
                        ));
                        break;
                    }
                }
            }
        }, "RomanVoiceFloatAudio");
        audioThread.start();
    }

    private void stopRecording(boolean requestFinal) {
        stopRecording(requestFinal, true);
    }

    private void stopRecording(boolean requestFinal, boolean reportReady) {
        cancelTileFocusRetry();
        boolean wasRecording = isRecording();
        int generation = sessionGeneration;

        if (requestFinal
                && client != null
                && clientGeneration == generation
                && transitionSession(
                        generation,
                        RomanVoiceRecordingPhase.RECORDING,
                        RomanVoiceRecordingPhase.FINISHING
                )) {
            stopAudioRecord();
            notifyTileStateChanged();
            setBusyControls("Finishing", PILL_COLOR_RECORDED);
            new Thread(() -> {
                try {
                    RomanVoiceStreamClient streamClient = client;
                    if (streamClient != null && clientGeneration == generation) {
                        streamClient.sendStop();
                    }
                    mainHandler.post(() -> {
                        if (isCurrentSession(generation, RomanVoiceRecordingPhase.FINISHING)) {
                            cancelStopSendTimeout();
                        }
                    });
                } catch (IOException exception) {
                    mainHandler.post(() -> handleStreamError(
                            generation,
                            RomanVoiceConnectionMessage.from(exception),
                            "stop_send_failed"
                    ));
                } catch (RuntimeException exception) {
                    mainHandler.post(() -> handleStreamError(
                            generation,
                            RomanVoiceConnectionMessage.from(exception),
                            "stop_send_failed"
                    ));
                }
            }, "RomanVoiceFloatStop").start();
        } else {
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            stopAudioRecord();
            notifyTileStateChanged();
            cleanupClient();
            mainHandler.post(() -> {
                setRecordingControls(false);
                setStatus("Ready");
                notifyTileStateChanged();
                if (wasRecording && reportReady) {
                    reportPhoneHeartbeat("ready");
                }
            });
        }
    }

    private void cancelRecording() {
        cancelTileFocusRetry();
        retryableIdleHealthFailure = false;
        boolean canceledHealthCheck = idleHealthCheck;
        idleHealthCheck = false;
        boolean canceledUnverifiedRetry = !canceledHealthCheck
                && phase == RomanVoiceRecordingPhase.CONNECTING
                && verifiedConnectionGeneration != sessionGeneration
                && !retryFailureNotice.isEmpty();
        String restoredFailure = retryFailureNotice;
        boolean hadClient = client != null;
        boolean wasBusy = phase == RomanVoiceRecordingPhase.CONNECTING
                || phase == RomanVoiceRecordingPhase.RECORDING
                || phase == RomanVoiceRecordingPhase.FINISHING;
        invalidateSession(canceledHealthCheck || canceledUnverifiedRetry
                ? RomanVoiceRecordingPhase.ERROR
                : RomanVoiceRecordingPhase.IDLE);
        stopAudioRecord();
        removeLiveDictationText();
        cleanupClient();
        setRecordingControls(false);
        setPillColor(PILL_COLOR_IDLE);
        resetLiveDictationState();
        retryFailureNotice = "";
        failureNotice = canceledHealthCheck
                ? "Connection check canceled - tap to retry"
                : (canceledUnverifiedRetry ? restoredFailure : "");
        failureReason = canceledHealthCheck
                ? "idle_health_canceled"
                : (canceledUnverifiedRetry ? "connection_retry_canceled" : "");
        if (canceledHealthCheck || canceledUnverifiedRetry) {
            showFailureNotice(failureNotice);
        } else if (wasBusy || hadClient) {
            setStatus("Canceled");
        } else {
            setStatus("Ready");
        }
        notifyTileStateChanged();
        reportPhoneHeartbeat("canceled");
    }

    private void stopAudioRecord() {
        AudioRecord record = audioRecord;
        audioRecord = null;
        Thread thread = audioThread;
        audioThread = null;
        if (record != null) {
            try {
                record.stop();
            } catch (IllegalStateException ignored) {
            }
        }
        if (thread != null && thread != Thread.currentThread()) {
            thread.interrupt();
            try {
                thread.join(THREAD_JOIN_TIMEOUT_MS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        if (record != null) {
            record.release();
        }
    }

    private void handlePartial(int generation, String text) {
        if (!isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
            return;
        }
        String next = text == null ? "" : text;
        if (next.trim().isEmpty()) {
            return;
        }
        if (writeDictationText(generation, next) && !next.isEmpty()) {
            bestPartialText = next;
        }
    }

    private void handleFinal(int generation, String text) {
        if (!isCurrentSession(generation, RomanVoiceRecordingPhase.FINISHING)) {
            return;
        }
        stopAudioRecord();
        String finalText = RomanVoiceTextRange.chooseFinalText(text, bestPartialText);
        if (!finalText.equals(lastDictationText)) {
            if (!writeDictationText(generation, finalText)) {
                return;
            }
        }
        cleanupClient(generation);
        if (!completeSession(generation, RomanVoiceRecordingPhase.IDLE)) {
            return;
        }
        retryableIdleHealthFailure = false;
        failureReason = "";
        failureNotice = "";
        setRecordingControls(false);
        setStatus(finalText.isEmpty() ? "No speech" : "Ready");
        setPillColor(finalText.isEmpty() ? PILL_COLOR_ERROR : PILL_COLOR_RECORDED);
        resetLiveDictationState();
        notifyTileStateChanged();
        reportPhoneHeartbeat("final");
    }

    private void handleStreamError(int generation, String message, String event) {
        handleStreamError(generation, message, event, event, false);
    }

    private void handleStreamError(
            int generation,
            String message,
            String event,
            String errorReason,
            boolean retryableFailure
    ) {
        if (!completeSession(generation, RomanVoiceRecordingPhase.ERROR)) {
            return;
        }
        idleHealthCheck = false;
        retryFailureNotice = "";
        verifiedConnectionGeneration = -1;
        stopAudioRecord();
        cleanupClient(generation);
        failureNotice = message == null || message.isEmpty()
                ? CONNECTION_FAILED_NOTICE
                : message;
        failureReason = errorReason == null ? "" : errorReason;
        // Keep auth, configuration, stream, and device failures latched for explicit action.
        // A connection timeout may recover through the same authenticated, non-recording
        // health probe used for explicitly transient idle-health failures.
        boolean retryableIdleProbeFailure = "idle_health_failed".equals(event)
                && retryableFailure;
        boolean retryableConnectionTimeout = "connection_timeout".equals(event);
        retryableIdleHealthFailure = retryableIdleProbeFailure
                || retryableConnectionTimeout;
        boolean silentIdleHealthFailure = "idle_health_failed".equals(event);
        boolean showFailure = !silentIdleHealthFailure;
        setRecordingControls(false);
        if (showFailure) {
            showFailureNotice(failureNotice);
        } else {
            hideIdleHealthOverlay();
        }
        resetLiveDictationState();
        notifyTileStateChanged();
        reportPhoneHeartbeat(event, false);
    }

    private void handleRecordingError(int generation, String message, String event) {
        if (isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
            handleStreamError(generation, message, event);
        }
    }

    private boolean writeDictationText(int generation, String dictationText) {
        AccessibilityNodeInfo target = findFocusedEditableNode();
        if (target == null) {
            handleTargetSafetyFailure(generation, "Text field lost - dictation preserved");
            return false;
        }
        if (!targetFingerprint.equals(fingerprint(target))) {
            recycleNode(target);
            handleTargetSafetyFailure(generation, "Text field changed - dictation preserved");
            return false;
        }

        String currentText = getEditableText(target);
        if (!RomanVoiceTextRange.hasExpectedContent(currentText, expectedFieldText)) {
            recycleNode(target);
            handleTargetSafetyFailure(generation, "Text changed - dictation preserved");
            return false;
        }
        int[] range = resolveReplacementRange(target, currentText);
        if (range == null) {
            recycleNode(target);
            handleTargetSafetyFailure(generation, "Text range changed - dictation preserved");
            return false;
        }
        int start = range[0];
        int end = range[1];
        String nextText =
                currentText.substring(0, start)
                        + dictationText
                        + currentText.substring(end);
        Bundle arguments = new Bundle();
        arguments.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                nextText
        );
        boolean changed = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
        if (changed) {
            insertionStart = start;
            insertionEnd = start + dictationText.length();
            lastDictationText = dictationText;
            expectedFieldText = nextText;
            int cursor = insertionEnd;
            Bundle selection = new Bundle();
            selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, cursor);
            selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, cursor);
            target.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selection);
        } else {
            recycleNode(target);
            handleTargetSafetyFailure(generation, "Cannot write to this field");
            return false;
        }
        recycleNode(target);
        return true;
    }

    private void handleTargetSafetyFailure(int generation, String message) {
        handleStreamError(generation, message, "target_changed");
    }

    private void captureInsertionState(AccessibilityNodeInfo node) {
        String currentText = getEditableText(node);

        int start = node.getTextSelectionStart();
        int end = node.getTextSelectionEnd();
        if (start < 0 || end < 0) {
            start = currentText.length();
            end = start;
        }
        insertionStart = clamp(start, 0, currentText.length());
        insertionEnd = clamp(end, 0, currentText.length());
        if (insertionStart > insertionEnd) {
            int previousStart = insertionStart;
            insertionStart = insertionEnd;
            insertionEnd = previousStart;
        }
        lastDictationText = "";
        bestPartialText = "";
        expectedFieldText = currentText;
        targetFingerprint = fingerprint(node);
    }

    private int[] resolveReplacementRange(AccessibilityNodeInfo node, String currentText) {
        int[] liveRange = findLiveDictationRange(currentText);
        if (liveRange != null) {
            return liveRange;
        }
        if (!lastDictationText.isEmpty()) {
            return null;
        }

        int start = clamp(insertionStart, 0, currentText.length());
        int end = clamp(insertionEnd, start, currentText.length());
        return new int[]{start, end};
    }

    private int[] findLiveDictationRange(String currentText) {
        return RomanVoiceTextRange.findLiveDictationRange(
                currentText,
                insertionStart,
                insertionEnd,
                lastDictationText
        );
    }

    private void removeLiveDictationText() {
        AccessibilityNodeInfo target = findFocusedEditableNode();
        if (target == null || lastDictationText.isEmpty()) {
            recycleNode(target);
            return;
        }
        if (!targetFingerprint.equals(fingerprint(target))) {
            recycleNode(target);
            return;
        }

        String currentText = getEditableText(target);
        if (!RomanVoiceTextRange.hasExpectedContent(currentText, expectedFieldText)) {
            recycleNode(target);
            return;
        }
        int[] range = findLiveDictationRange(currentText);
        if (range == null) {
            recycleNode(target);
            return;
        }

        String nextText = currentText.substring(0, range[0]) + currentText.substring(range[1]);
        Bundle arguments = new Bundle();
        arguments.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                nextText
        );
        boolean changed = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
        if (changed) {
            Bundle selection = new Bundle();
            selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, range[0]);
            selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, range[0]);
            target.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selection);
        }
        recycleNode(target);
    }

    private void resetLiveDictationState() {
        insertionStart = 0;
        insertionEnd = 0;
        lastDictationText = "";
        bestPartialText = "";
        expectedFieldText = "";
        targetFingerprint = "";
    }

    private int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(value, max));
    }

    private String getEditableText(AccessibilityNodeInfo node) {
        CharSequence text = node.getText();
        String value = text == null ? "" : text.toString();
        if (value.isEmpty()) {
            return "";
        }

        CharSequence hint = node.getHintText();
        if (hint != null && value.contentEquals(hint)) {
            return "";
        }

        if (isKnownPlaceholder(node, value)) {
            return "";
        }

        return value;
    }

    private boolean isKnownPlaceholder(AccessibilityNodeInfo node, String value) {
        String normalized = value.trim();
        CharSequence packageName = node.getPackageName();
        if (packageName == null
                || !"com.google.android.apps.messaging".contentEquals(packageName)) {
            return false;
        }

        return "RCS message".equalsIgnoreCase(normalized)
                || "Text message".equalsIgnoreCase(normalized)
                || "Message".equalsIgnoreCase(normalized);
    }

    private AccessibilityNodeInfo findFocusedEditableNode() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            return null;
        }
        AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (isUsableFocusedEditable(focused)) {
            if (focused != root) {
                recycleNode(root);
            }
            return focused;
        }
        recycleNode(focused);

        if (isUsableFocusedEditable(root)) {
            return root;
        }
        AccessibilityNodeInfo fallback = findFocusedEditableDescendant(root);
        recycleNode(root);
        return fallback;
    }

    private AccessibilityNodeInfo findFocusedEditableDescendant(AccessibilityNodeInfo node) {
        if (node == null) {
            return null;
        }
        int childCount = node.getChildCount();
        for (int i = 0; i < childCount; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) {
                continue;
            }
            if (isUsableFocusedEditable(child)) {
                return child;
            }
            AccessibilityNodeInfo descendant = findFocusedEditableDescendant(child);
            recycleNode(child);
            if (descendant != null) {
                return descendant;
            }
        }
        return null;
    }

    private boolean isUsableFocusedEditable(AccessibilityNodeInfo node) {
        return node != null
                && node.isEditable()
                && (node.isFocused() || node.isAccessibilityFocused());
    }

    private String fingerprint(AccessibilityNodeInfo node) {
        if (node == null) {
            return "";
        }
        CharSequence packageName = node.getPackageName();
        CharSequence className = node.getClassName();
        String viewId = node.getViewIdResourceName();
        return String.valueOf(packageName)
                + "|" + node.getWindowId()
                + "|" + String.valueOf(viewId)
                + "|" + String.valueOf(className)
                + "|" + node.hashCode();
    }

    private void cleanupClient() {
        RomanVoiceStreamClient streamClient = client;
        client = null;
        clientGeneration = 0;
        if (streamClient != null) {
            streamClient.close();
        }
    }

    private void cleanupClient(int generation) {
        if (clientGeneration != generation) {
            return;
        }
        cleanupClient();
    }

    private boolean isCurrentSession(int generation, RomanVoiceRecordingPhase requiredPhase) {
        return generation == sessionGeneration && phase == requiredPhase;
    }

    private synchronized int beginSession() {
        int generation = ++sessionGeneration;
        setPhase(RomanVoiceRecordingPhase.CONNECTING);
        return generation;
    }

    private synchronized void invalidateSession(RomanVoiceRecordingPhase nextPhase) {
        sessionGeneration++;
        setPhase(nextPhase);
    }

    private synchronized boolean transitionSession(
            int generation,
            RomanVoiceRecordingPhase expectedPhase,
            RomanVoiceRecordingPhase nextPhase
    ) {
        if (!isCurrentSession(generation, expectedPhase)) {
            return false;
        }
        setPhase(nextPhase);
        return true;
    }

    private synchronized boolean activateAudioSession(int generation, AudioRecord record) {
        if (!isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
            return false;
        }
        audioRecord = record;
        setPhase(RomanVoiceRecordingPhase.RECORDING);
        return true;
    }

    private synchronized boolean activateClientSession(
            int generation,
            RomanVoiceStreamClient streamClient
    ) {
        if (!isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
            return false;
        }
        client = streamClient;
        clientGeneration = generation;
        return true;
    }

    private synchronized void markConnectionVerified(int generation) {
        if (isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
            verifiedConnectionGeneration = generation;
            retryFailureNotice = "";
        }
    }

    private synchronized boolean completeSession(
            int generation,
            RomanVoiceRecordingPhase nextPhase
    ) {
        if (generation != sessionGeneration) {
            return false;
        }
        sessionGeneration++;
        setPhase(nextPhase);
        return true;
    }

    private boolean hasRecordPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void openSettings() {
        Intent intent = new Intent(this, SettingsActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
    }

    private void setStatus(String text) {
        if (micButton != null && text != null && !text.trim().isEmpty()) {
            micButton.setContentDescription(text);
        }
    }

    private void showIdleNotice(String text) {
        setStatus(text);
        setPillColor(PILL_COLOR_ERROR);
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
        if (overlayView == null) {
            return;
        }
        overlayView.setVisibility(View.VISIBLE);
        scheduleIdleOverlayHide(IDLE_NOTICE_VISIBLE_MS);
    }

    private void showFailureNotice(String text) {
        setStatus(text);
        setPillColor(PILL_COLOR_ERROR);
        if (micButton != null) {
            micButton.setText(errorButtonLabel(text));
        }
        Toast.makeText(this, text, Toast.LENGTH_LONG).show();
        if (overlayView == null) {
            return;
        }
        overlayView.setVisibility(View.VISIBLE);
        scheduleIdleOverlayHide(IDLE_NOTICE_VISIBLE_MS);
    }

    private void hideIdleHealthOverlay() {
        cancelIdleOverlayHide();
        if (overlayView != null) {
            overlayView.setVisibility(View.GONE);
        }
    }

    private void setRecordingControls(boolean isRecording) {
        cancelIdleOverlayHide();
        int color = phase == RomanVoiceRecordingPhase.ERROR
                ? PILL_COLOR_ERROR
                : (isRecording ? PILL_COLOR_RECORDING : PILL_COLOR_IDLE);
        setPillState(color, true);
        if (micButton != null) {
            micButton.setText(
                    phase == RomanVoiceRecordingPhase.ERROR
                            ? errorButtonLabel(failureNotice)
                            : (isRecording ? "Stop" : "Start")
            );
            micButton.setContentDescription(
                    phase == RomanVoiceRecordingPhase.ERROR
                            ? failureNotice + ". Tap to retry."
                            : (isRecording
                                    ? "Stop RomanVoice dictation"
                                    : "Start RomanVoice dictation")
            );
            micButton.setEnabled(true);
        }
        setOverlayClickTargetsEnabled(true);
        if (phase != RomanVoiceRecordingPhase.ERROR) {
            setStatus(isRecording ? "Listening" : "Ready");
        }
        if (cancelButton != null) {
            cancelButton.setEnabled(true);
            cancelButton.setVisibility(isRecording ? View.VISIBLE : View.GONE);
        }
        if (!isRecording && phase == RomanVoiceRecordingPhase.IDLE) {
            scheduleIdleOverlayHide(RESTART_WINDOW_VISIBLE_MS);
        }
    }

    private void setBusyControls(String label, int color) {
        cancelIdleOverlayHide();
        setStatus(label);
        setPillState(color, true);
        if (micButton != null) {
            micButton.setText(label);
            micButton.setEnabled(false);
        }
        if (overlayView != null) {
            overlayView.setEnabled(true);
        }
        if (cancelButton != null) {
            cancelButton.setEnabled(true);
            cancelButton.setVisibility(View.VISIBLE);
        }
    }

    private String errorButtonLabel(String message) {
        String value = message == null ? "" : message.toLowerCase();
        if (value.contains("auth") || value.contains("token") || value.contains("url")) {
            return "Setup";
        }
        if (value.contains("field") || value.contains("text")) {
            return "Field";
        }
        if (value.contains("microphone")) {
            return "Mic";
        }
        if (value.contains("connection") || value.contains("wi-fi") || value.contains("vpn")) {
            return "Offline";
        }
        return "Error";
    }

    private void setOverlayClickTargetsEnabled(boolean enabled) {
        if (micButton != null) {
            micButton.setEnabled(enabled);
        }
        if (overlayView != null) {
            overlayView.setEnabled(enabled);
        }
    }

    private void cancelIdleOverlayHide() {
        if (hideIdleOverlayRunnable != null) {
            mainHandler.removeCallbacks(hideIdleOverlayRunnable);
            hideIdleOverlayRunnable = null;
        }
    }

    private void scheduleIdleOverlayHide(long delayMs) {
        cancelIdleOverlayHide();
        hideIdleOverlayRunnable = () -> {
            if ((phase == RomanVoiceRecordingPhase.IDLE
                    || phase == RomanVoiceRecordingPhase.ERROR)
                    && overlayView != null) {
                overlayView.setVisibility(View.GONE);
            }
            hideIdleOverlayRunnable = null;
        };
        mainHandler.postDelayed(hideIdleOverlayRunnable, delayMs);
    }

    private void setPillState(int color, boolean visible) {
        setPillColor(color);
        if (overlayView != null) {
            overlayView.setVisibility(visible ? View.VISIBLE : View.GONE);
        }
    }

    private void setPillColor(int color) {
        if (overlayView != null) {
            overlayView.setBackground(roundedBackground(color));
        }
    }

    private void notifyTileStateChanged() {
        RomanVoiceTileService.requestStateUpdate(this);
    }

    private void startPhoneHeartbeat() {
        cancelPhoneHeartbeat();
        phoneHeartbeatRunnable = new Runnable() {
            @Override
            public void run() {
                if (phase == RomanVoiceRecordingPhase.IDLE) {
                    checkIdleServiceHealth();
                } else if (phase == RomanVoiceRecordingPhase.ERROR
                        && retryableIdleHealthFailure) {
                    retryIdleServiceHealth();
                } else {
                    reportPhoneHeartbeat("heartbeat");
                }
                mainHandler.postDelayed(this, PHONE_HEARTBEAT_INTERVAL_MS);
            }
        };
        mainHandler.post(phoneHeartbeatRunnable);
    }

    private void cancelPhoneHeartbeat() {
        if (phoneHeartbeatRunnable != null) {
            mainHandler.removeCallbacks(phoneHeartbeatRunnable);
            phoneHeartbeatRunnable = null;
        }
    }

    private void reportPhoneHeartbeat(String event) {
        reportPhoneHeartbeat(
                event,
                phase == RomanVoiceRecordingPhase.IDLE
                        || phase == RomanVoiceRecordingPhase.RECORDING
                        || phase == RomanVoiceRecordingPhase.FINISHING
        );
    }

    private void reportPhoneHeartbeat(String event, boolean available) {
        reportPhoneHeartbeat(event, available, true, available, failureReason);
    }

    private void reportPhoneHeartbeat(
            String event,
            boolean available,
            boolean serviceAlive,
            boolean backendReady,
            String errorReason
    ) {
        RomanVoicePhoneHeartbeat.reportAsync(
                this,
                "floating",
                event,
                available,
                serviceAlive,
                backendReady,
                phase == RomanVoiceRecordingPhase.RECORDING,
                phase == RomanVoiceRecordingPhase.CONNECTING,
                errorReason
        );
    }

    private GradientDrawable roundedBackground(int color) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(16));
        return drawable;
    }

    private void recycleNode(AccessibilityNodeInfo node) {
        if (node != null && Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            node.recycle();
        }
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private final class DragTouchListener implements View.OnTouchListener {
        private int startX;
        private int startY;
        private float touchStartX;
        private float touchStartY;
        private boolean moved;

        @Override
        public boolean onTouch(View view, MotionEvent event) {
            if (overlayParams == null || windowManager == null) {
                return false;
            }
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    startX = overlayParams.x;
                    startY = overlayParams.y;
                    touchStartX = event.getRawX();
                    touchStartY = event.getRawY();
                    moved = false;
                    return false;
                case MotionEvent.ACTION_MOVE:
                    int nextX = startX + Math.round(event.getRawX() - touchStartX);
                    int nextY = startY + Math.round(event.getRawY() - touchStartY);
                    if (Math.abs(nextX - startX) > dp(4) || Math.abs(nextY - startY) > dp(4)) {
                        moved = true;
                    }
                    overlayParams.x = nextX;
                    overlayParams.y = nextY;
                    windowManager.updateViewLayout(overlayView, overlayParams);
                    return moved;
                case MotionEvent.ACTION_UP:
                    return moved;
                default:
                    return false;
            }
        }
    }

    private final class StreamListener implements RomanVoiceStreamClient.Listener {
        private final int generation;

        StreamListener(int generation) {
            this.generation = generation;
        }

        @Override
        public void onReady() {
            Log.i(TAG, "RomanVoice floating stream ready");
            mainHandler.post(() -> {
                if (isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
                    setPillColor(PILL_COLOR_CONNECTING);
                }
            });
        }

        @Override
        public void onStarted() {
            Log.i(TAG, "RomanVoice floating stream started");
            mainHandler.post(() -> {
                if (isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
                    setStatus("Listening");
                    setPillState(PILL_COLOR_RECORDING, true);
                }
            });
        }

        @Override
        public void onPartial(String text) {
            Log.d(TAG, "RomanVoice floating partial length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handlePartial(generation, text));
        }

        @Override
        public void onFinal(String text) {
            Log.i(TAG, "RomanVoice floating final length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handleFinal(generation, text));
        }

        @Override
        public void onError(String message) {
            Log.w(TAG, "RomanVoice floating stream error: " + message);
            mainHandler.post(() -> handleStreamError(
                    generation,
                    RomanVoiceConnectionMessage.fromMessage(message),
                    "stream_error"
            ));
        }
    }
}
