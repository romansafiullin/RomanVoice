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
import android.widget.TextView;
import android.widget.Toast;

import java.io.IOException;

public class RomanVoiceFloatingService extends AccessibilityService {
    private static final String TAG = "RomanVoiceFloat";
    private static final int SAMPLE_RATE = 16000;
    private static final int PILL_COLOR_IDLE = 0xEE2F7D4C;
    private static final int PILL_COLOR_CONNECTING = 0xEE5E6252;
    private static final int PILL_COLOR_RECORDING = 0xEEC8372D;
    private static final int PILL_COLOR_RECORDED = 0xEE2F7D4C;
    private static final int PILL_COLOR_ERROR = 0xEE7A3129;
    private static final boolean SHOW_CANCEL_BUTTON = false;
    private static final int TILE_FOCUS_RETRY_COUNT = 8;
    private static final long TILE_FOCUS_RETRY_DELAY_MS = 150;
    private static final long IDLE_NOTICE_VISIBLE_MS = 1800;
    private static final long RESTART_WINDOW_VISIBLE_MS = 8000;
    private static final long PHONE_HEARTBEAT_INTERVAL_MS = 60000;
    private static final long CONNECTING_TIMEOUT_MS = 10000;
    private static final long STOP_SEND_TIMEOUT_MS = 10000;
    private static final long FINAL_RESULT_TIMEOUT_MS = 90000;
    private static final long ERROR_RESET_MS = 3000;
    private static final long TILE_TOGGLE_DEBOUNCE_MS = 750;

    private static volatile RomanVoiceFloatingService activeService;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private WindowManager windowManager;
    private WindowManager.LayoutParams overlayParams;
    private LinearLayout overlayView;
    private Button micButton;
    private Button cancelButton;
    private TextView statusView;
    private Runnable hideIdleOverlayRunnable;
    private Runnable tileFocusRetryRunnable;
    private Runnable phoneHeartbeatRunnable;
    private Runnable connectingTimeoutRunnable;
    private Runnable stopSendTimeoutRunnable;
    private Runnable finalResultTimeoutRunnable;
    private Runnable errorResetRunnable;

    private volatile RomanVoiceRecordingPhase phase = RomanVoiceRecordingPhase.IDLE;
    private volatile AudioRecord audioRecord;
    private Thread audioThread;
    private volatile RomanVoiceStreamClient client;
    private long lastTileToggleElapsedMs;

    private int insertionStart = 0;
    private int insertionEnd = 0;
    private String lastDictationText = "";

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
        return TileState.READY;
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
        setStatus("Ready");
        notifyTileStateChanged();
        startPhoneHeartbeat();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (phase == RomanVoiceRecordingPhase.IDLE
                && overlayView != null
                && overlayView.getVisibility() == View.VISIBLE
                && statusView != null) {
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
        stopRecording(false);
        reportPhoneHeartbeat("destroyed", false);
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

        statusView = new TextView(this);
        statusView.setTextColor(Color.WHITE);
        statusView.setTextSize(12f);
        statusView.setSingleLine(true);
        statusView.setPadding(dp(8), 0, dp(2), 0);
        statusView.setVisibility(View.VISIBLE);
        statusView.setOnClickListener(view -> toggleRecording());
        statusView.setOnLongClickListener(view -> {
            cancelRecording();
            return true;
        });
        overlayView.addView(statusView, new LinearLayout.LayoutParams(dp(116), dp(46)));

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
        } else if (nextPhase == RomanVoiceRecordingPhase.ERROR) {
            scheduleErrorReset();
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
        if (errorResetRunnable != null) {
            mainHandler.removeCallbacks(errorResetRunnable);
            errorResetRunnable = null;
        }
    }

    private void scheduleConnectingTimeout() {
        connectingTimeoutRunnable = () -> {
            connectingTimeoutRunnable = null;
            if (phase == RomanVoiceRecordingPhase.CONNECTING) {
                handlePhaseTimeout("Connecting timed out - try again", "connection_timeout");
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

    private void scheduleErrorReset() {
        errorResetRunnable = () -> {
            errorResetRunnable = null;
            if (phase == RomanVoiceRecordingPhase.ERROR) {
                setPhase(RomanVoiceRecordingPhase.IDLE);
                setRecordingControls(false);
                notifyTileStateChanged();
                reportPhoneHeartbeat("ready");
            }
        };
        mainHandler.postDelayed(errorResetRunnable, ERROR_RESET_MS);
    }

    private void handlePhaseTimeout(String message, String event) {
        setPhase(RomanVoiceRecordingPhase.ERROR);
        stopAudioRecord();
        cleanupClient();
        setRecordingControls(false);
        setStatus(message);
        setPillColor(PILL_COLOR_ERROR);
        notifyTileStateChanged();
        reportPhoneHeartbeat(event);
    }

    private void toggleRecording() {
        if (isRecording()) {
            stopRecording(true);
        } else if (phase == RomanVoiceRecordingPhase.ERROR) {
            setPhase(RomanVoiceRecordingPhase.IDLE);
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        } else if (!isBusyStartingOrFinishing()) {
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        }
    }

    private void toggleRecordingFromTile() {
        if (isRecording()) {
            cancelTileFocusRetry();
            stopRecording(true);
        } else if (phase == RomanVoiceRecordingPhase.ERROR) {
            setPhase(RomanVoiceRecordingPhase.IDLE);
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        } else if (!isBusyStartingOrFinishing()) {
            startRecording(true, TILE_FOCUS_RETRY_COUNT);
        }
    }

    private void startRecording(boolean retryMissingFocus, int retriesRemaining) {
        if (phase != RomanVoiceRecordingPhase.IDLE) {
            return;
        }
        cancelIdleOverlayHide();
        if (!hasRecordPermission()) {
            cancelTileFocusRetry();
            showIdleNotice("Grant mic");
            openSettings();
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
            showIdleNotice("Tap a text field first");
            return;
        }
        cancelTileFocusRetry();
        captureInsertionState(target);
        recycleNode(target);

        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (streamUrl == null || streamUrl.trim().isEmpty() || RomanVoicePreferences.isDefaultStreamUrl(streamUrl)) {
            cancelTileFocusRetry();
            showIdleNotice("Set URL");
            openSettings();
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            cancelTileFocusRetry();
            showIdleNotice("Set token");
            openSettings();
            return;
        }

        setPhase(RomanVoiceRecordingPhase.CONNECTING);
        notifyTileStateChanged();
        reportPhoneHeartbeat("connecting");
        setStatus("Connecting");
        setPillState(PILL_COLOR_CONNECTING, true);
        micButton.setEnabled(false);
        setOverlayClickTargetsEnabled(false);
        if (cancelButton != null) {
            cancelButton.setVisibility(View.GONE);
        }

        new Thread(() -> {
            try {
                Log.i(TAG, "Connecting to RomanVoice stream: " + streamUrl);
                RomanVoiceStreamClient streamClient = new RomanVoiceStreamClient(
                        streamUrl,
                        token,
                        new StreamListener()
                );
                streamClient.connect();
                streamClient.sendStart(SAMPLE_RATE, RomanVoicePreferences.polish(this));
                if (phase != RomanVoiceRecordingPhase.CONNECTING) {
                    streamClient.close();
                    return;
                }
                client = streamClient;
                startAudioPump();
                mainHandler.post(() -> {
                    if (phase == RomanVoiceRecordingPhase.RECORDING) {
                        setRecordingControls(true);
                        notifyTileStateChanged();
                        reportPhoneHeartbeat("recording");
                    }
                });
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice floating connection failed", exception);
                cleanupClient();
                mainHandler.post(() -> {
                    setPhase(RomanVoiceRecordingPhase.ERROR);
                    setRecordingControls(false);
                    setStatus(shortError(exception));
                    setPillColor(PILL_COLOR_ERROR);
                    notifyTileStateChanged();
                    reportPhoneHeartbeat("connection_failed");
                });
            }
        }, "RomanVoiceFloatConnect").start();
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

    private void startAudioPump() throws IOException {
        int minBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
        );
        int bufferSize = Math.max(minBuffer, SAMPLE_RATE / 5 * 2);
        audioRecord = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize * 2
        );
        if (audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
            throw new IOException("Microphone failed to initialize");
        }

        audioRecord.startRecording();
        setPhase(RomanVoiceRecordingPhase.RECORDING);
        audioThread = new Thread(() -> {
            byte[] buffer = new byte[bufferSize];
            while (phase == RomanVoiceRecordingPhase.RECORDING) {
                AudioRecord record = audioRecord;
                if (record == null) {
                    break;
                }
                int read;
                try {
                    read = record.read(buffer, 0, buffer.length);
                } catch (RuntimeException exception) {
                    Log.w(TAG, "Floating audio read failed", exception);
                    mainHandler.post(() -> handleStreamError(shortError(exception)));
                    break;
                }
                RomanVoiceStreamClient streamClient = client;
                if (read > 0 && streamClient != null) {
                    try {
                        streamClient.sendAudio(buffer, read);
                    } catch (IOException exception) {
                        Log.w(TAG, "Failed to send floating audio chunk", exception);
                        mainHandler.post(() -> handleStreamError(shortError(exception)));
                        break;
                    } catch (RuntimeException exception) {
                        Log.w(TAG, "Floating audio pump failed", exception);
                        mainHandler.post(() -> handleStreamError(shortError(exception)));
                        break;
                    }
                }
            }
        }, "RomanVoiceFloatAudio");
        audioThread.start();
    }

    private void stopRecording(boolean requestFinal) {
        cancelTileFocusRetry();
        boolean wasRecording = isRecording();
        stopAudioRecord();

        if (requestFinal && client != null) {
            setPhase(RomanVoiceRecordingPhase.FINISHING);
            notifyTileStateChanged();
            setStatus("Finishing");
            cancelIdleOverlayHide();
            setPillState(PILL_COLOR_RECORDED, true);
            micButton.setEnabled(false);
            setOverlayClickTargetsEnabled(false);
            if (cancelButton != null) {
                cancelButton.setVisibility(View.GONE);
            }
            new Thread(() -> {
                try {
                    RomanVoiceStreamClient streamClient = client;
                    if (streamClient != null) {
                        streamClient.sendStop();
                    }
                    mainHandler.post(this::cancelStopSendTimeout);
                } catch (IOException exception) {
                    mainHandler.post(() -> handleStreamError(shortError(exception)));
                } catch (RuntimeException exception) {
                    mainHandler.post(() -> handleStreamError(shortError(exception)));
                }
            }, "RomanVoiceFloatStop").start();
        } else {
            setPhase(RomanVoiceRecordingPhase.IDLE);
            notifyTileStateChanged();
            cleanupClient();
            if (wasRecording) {
                mainHandler.post(() -> {
                    setRecordingControls(false);
                    setStatus("Ready");
                    notifyTileStateChanged();
                    reportPhoneHeartbeat("ready");
                });
            }
        }
    }

    private void cancelRecording() {
        cancelTileFocusRetry();
        boolean hadClient = client != null;
        boolean wasRecording = isRecording();
        setPhase(RomanVoiceRecordingPhase.IDLE);
        stopAudioRecord();
        removeLiveDictationText();
        cleanupClient();
        setRecordingControls(false);
        setPillColor(PILL_COLOR_IDLE);
        resetLiveDictationState();
        if (wasRecording || hadClient) {
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
        if (record != null) {
            try {
                record.stop();
            } catch (IllegalStateException ignored) {
            }
            record.release();
        }
    }

    private void handlePartial(String text) {
        if (phase != RomanVoiceRecordingPhase.RECORDING) {
            return;
        }
        String next = text == null ? "" : text;
        writeDictationText(next);
    }

    private void handleFinal(String text) {
        if (phase != RomanVoiceRecordingPhase.FINISHING) {
            return;
        }
        stopAudioRecord();
        setPhase(RomanVoiceRecordingPhase.IDLE);
        String finalText = text == null ? "" : text;
        if (!finalText.equals(lastDictationText)) {
            writeDictationText(finalText);
        }
        cleanupClient();
        setRecordingControls(false);
        setStatus(finalText.isEmpty() ? "No speech" : "Ready");
        setPillColor(finalText.isEmpty() ? PILL_COLOR_ERROR : PILL_COLOR_RECORDED);
        resetLiveDictationState();
        notifyTileStateChanged();
        reportPhoneHeartbeat("final");
    }

    private void handleStreamError(String message) {
        setPhase(RomanVoiceRecordingPhase.ERROR);
        stopAudioRecord();
        cleanupClient();
        setRecordingControls(false);
        setStatus(message == null || message.isEmpty() ? "Offline" : message);
        setPillColor(PILL_COLOR_ERROR);
        resetLiveDictationState();
        notifyTileStateChanged();
        reportPhoneHeartbeat("stream_error");
    }

    private void writeDictationText(String dictationText) {
        AccessibilityNodeInfo target = findFocusedEditableNode();
        if (target == null) {
            setStatus("Field lost");
            return;
        }

        String currentText = getEditableText(target);
        int[] range = resolveReplacementRange(target, currentText);
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
            int cursor = insertionEnd;
            Bundle selection = new Bundle();
            selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, cursor);
            selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, cursor);
            target.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selection);
        } else {
            setStatus("Cannot write");
        }
        recycleNode(target);
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
    }

    private int[] resolveReplacementRange(AccessibilityNodeInfo node, String currentText) {
        int[] liveRange = findLiveDictationRange(currentText);
        if (liveRange != null) {
            return liveRange;
        }

        int start = node.getTextSelectionStart();
        int end = node.getTextSelectionEnd();
        if (start < 0 || end < 0) {
            start = clamp(insertionEnd, 0, currentText.length());
            end = start;
        }

        start = clamp(start, 0, currentText.length());
        end = clamp(end, 0, currentText.length());
        if (start > end) {
            int previousStart = start;
            start = end;
            end = previousStart;
        }
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

        String currentText = getEditableText(target);
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
        if ("RCS message".equalsIgnoreCase(normalized)) {
            return true;
        }

        CharSequence packageName = node.getPackageName();
        if (packageName == null
                || !"com.google.android.apps.messaging".contentEquals(packageName)) {
            return false;
        }

        return "Text message".equalsIgnoreCase(normalized)
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

    private void cleanupClient() {
        RomanVoiceStreamClient streamClient = client;
        client = null;
        if (streamClient != null) {
            streamClient.close();
        }
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
        if (statusView != null) {
            statusView.setText(text);
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

    private void setRecordingControls(boolean isRecording) {
        cancelIdleOverlayHide();
        setPillState(isRecording ? PILL_COLOR_RECORDING : PILL_COLOR_IDLE, true);
        if (micButton != null) {
            micButton.setText(isRecording ? "Stop" : "Start");
            micButton.setContentDescription(
                    isRecording ? "Stop RomanVoice dictation" : "Start RomanVoice dictation"
            );
            micButton.setEnabled(true);
        }
        setOverlayClickTargetsEnabled(true);
        setStatus(isRecording ? "Listening" : "Ready");
        if (cancelButton != null) {
            cancelButton.setVisibility(SHOW_CANCEL_BUTTON && isRecording ? View.VISIBLE : View.GONE);
        }
        if (!isRecording) {
            scheduleIdleOverlayHide(RESTART_WINDOW_VISIBLE_MS);
        }
    }

    private void setOverlayClickTargetsEnabled(boolean enabled) {
        if (micButton != null) {
            micButton.setEnabled(enabled);
        }
        if (statusView != null) {
            statusView.setEnabled(enabled);
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
            if (phase == RomanVoiceRecordingPhase.IDLE && overlayView != null) {
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
                reportPhoneHeartbeat("heartbeat");
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
        reportPhoneHeartbeat(event, true);
    }

    private void reportPhoneHeartbeat(String event, boolean available) {
        RomanVoicePhoneHeartbeat.reportAsync(
                this,
                "floating",
                event,
                available,
                phase == RomanVoiceRecordingPhase.RECORDING,
                phase == RomanVoiceRecordingPhase.CONNECTING
        );
    }

    private String shortError(Exception exception) {
        String message = exception.getMessage();
        if (message == null || message.trim().isEmpty()) {
            return "Offline";
        }
        if (message.length() > 24) {
            return message.substring(0, 24);
        }
        return message;
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
        @Override
        public void onReady() {
            Log.i(TAG, "RomanVoice floating stream ready");
            mainHandler.post(() -> setPillColor(PILL_COLOR_CONNECTING));
        }

        @Override
        public void onStarted() {
            Log.i(TAG, "RomanVoice floating stream started");
            mainHandler.post(() -> {
                setStatus("Listening");
                setPillState(PILL_COLOR_RECORDING, true);
            });
        }

        @Override
        public void onPartial(String text) {
            Log.d(TAG, "RomanVoice floating partial length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handlePartial(text));
        }

        @Override
        public void onFinal(String text) {
            Log.i(TAG, "RomanVoice floating final length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handleFinal(text));
        }

        @Override
        public void onError(String message) {
            Log.w(TAG, "RomanVoice floating stream error: " + message);
            mainHandler.post(() -> handleStreamError(message));
        }
    }
}
