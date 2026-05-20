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
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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

import java.io.IOException;

@SuppressWarnings("deprecation")
public class RomanVoiceFloatingService extends AccessibilityService {
    private static final String TAG = "RomanVoiceFloat";
    private static final int SAMPLE_RATE = 16000;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private WindowManager windowManager;
    private WindowManager.LayoutParams overlayParams;
    private LinearLayout overlayView;
    private Button micButton;
    private TextView statusView;

    private volatile boolean recording;
    private AudioRecord audioRecord;
    private Thread audioThread;
    private RomanVoiceStreamClient client;

    private String baseText = "";
    private int insertionStart = 0;
    private int insertionEnd = 0;
    private String lastDictationText = "";

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        showOverlay();
        setStatus("Ready");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (!recording && statusView != null) {
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
        removeOverlay();
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
        overlayView.setBackground(roundedBackground(0xEE25312C));

        micButton = new Button(this);
        micButton.setText("RV");
        micButton.setTextColor(Color.WHITE);
        micButton.setOnClickListener(view -> toggleRecording());
        overlayView.addView(micButton, new LinearLayout.LayoutParams(dp(54), dp(46)));

        statusView = new TextView(this);
        statusView.setTextColor(Color.WHITE);
        statusView.setTextSize(12f);
        statusView.setSingleLine(true);
        statusView.setPadding(dp(8), 0, dp(2), 0);
        overlayView.addView(statusView, new LinearLayout.LayoutParams(dp(116), dp(46)));

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
    }

    private void removeOverlay() {
        if (overlayView != null && windowManager != null) {
            windowManager.removeView(overlayView);
            overlayView = null;
        }
    }

    private void toggleRecording() {
        if (recording) {
            stopRecording(true);
        } else {
            startRecording();
        }
    }

    private void startRecording() {
        if (!hasRecordPermission()) {
            setStatus("Grant mic");
            openSettings();
            return;
        }

        AccessibilityNodeInfo target = findFocusedEditableNode();
        if (target == null) {
            setStatus("Tap a field");
            return;
        }
        captureInsertionState(target);
        recycleNode(target);

        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (streamUrl == null || streamUrl.trim().isEmpty() || streamUrl.contains("100.x.x.x")) {
            setStatus("Set URL");
            openSettings();
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            setStatus("Set token");
            openSettings();
            return;
        }

        setStatus("Connecting");
        micButton.setEnabled(false);

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
                client = streamClient;
                startAudioPump();
                mainHandler.post(() -> {
                    micButton.setText("Stop");
                    micButton.setEnabled(true);
                    setStatus("Listening");
                });
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice floating connection failed", exception);
                cleanupClient();
                mainHandler.post(() -> {
                    micButton.setText("RV");
                    micButton.setEnabled(true);
                    setStatus(shortError(exception));
                });
            }
        }, "RomanVoiceFloatConnect").start();
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

        recording = true;
        audioRecord.startRecording();
        audioThread = new Thread(() -> {
            byte[] buffer = new byte[bufferSize];
            while (recording) {
                int read = audioRecord.read(buffer, 0, buffer.length);
                if (read > 0 && client != null) {
                    try {
                        client.sendAudio(buffer, read);
                    } catch (IOException exception) {
                        Log.w(TAG, "Failed to send floating audio chunk", exception);
                        mainHandler.post(() -> handleStreamError(shortError(exception)));
                        break;
                    }
                }
            }
        }, "RomanVoiceFloatAudio");
        audioThread.start();
    }

    private void stopRecording(boolean requestFinal) {
        boolean wasRecording = recording;
        recording = false;
        stopAudioRecord();

        if (requestFinal && client != null) {
            setStatus("Finishing");
            micButton.setEnabled(false);
            new Thread(() -> {
                try {
                    client.sendStop();
                } catch (IOException exception) {
                    mainHandler.post(() -> handleStreamError(shortError(exception)));
                }
            }, "RomanVoiceFloatStop").start();
        } else {
            cleanupClient();
            if (wasRecording) {
                mainHandler.post(() -> {
                    micButton.setText("RV");
                    micButton.setEnabled(true);
                    setStatus("Ready");
                });
            }
        }
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
        String next = text == null ? "" : text;
        writeDictationText(next);
        lastDictationText = next;
        setStatus("Listening");
    }

    private void handleFinal(String text) {
        stopAudioRecord();
        String finalText = text == null ? "" : text;
        if (!finalText.equals(lastDictationText)) {
            writeDictationText(finalText);
        }
        cleanupClient();
        micButton.setText("RV");
        micButton.setEnabled(true);
        setStatus(finalText.isEmpty() ? "No speech" : "Ready");
        lastDictationText = "";
    }

    private void handleStreamError(String message) {
        recording = false;
        stopAudioRecord();
        cleanupClient();
        micButton.setText("RV");
        micButton.setEnabled(true);
        setStatus(message == null || message.isEmpty() ? "Offline" : message);
        lastDictationText = "";
    }

    private void writeDictationText(String dictationText) {
        AccessibilityNodeInfo target = findFocusedEditableNode();
        if (target == null) {
            setStatus("Field lost");
            return;
        }

        String nextText =
                baseText.substring(0, insertionStart)
                        + dictationText
                        + baseText.substring(insertionEnd);
        Bundle arguments = new Bundle();
        arguments.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                nextText
        );
        boolean changed = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
        if (changed) {
            int cursor = insertionStart + dictationText.length();
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
        baseText = getEditableText(node);

        int start = node.getTextSelectionStart();
        int end = node.getTextSelectionEnd();
        if (start < 0 || end < 0) {
            start = baseText.length();
            end = start;
        }
        insertionStart = Math.max(0, Math.min(start, baseText.length()));
        insertionEnd = Math.max(0, Math.min(end, baseText.length()));
        if (insertionStart > insertionEnd) {
            int previousStart = insertionStart;
            insertionStart = insertionEnd;
            insertionEnd = previousStart;
        }
        lastDictationText = "";
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
        recycleNode(root);
        if (focused == null) {
            return null;
        }
        if (!focused.isEditable()) {
            recycleNode(focused);
            return null;
        }
        return focused;
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
        if (node != null) {
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
            mainHandler.post(() -> setStatus("Connected"));
        }

        @Override
        public void onStarted() {
            Log.i(TAG, "RomanVoice floating stream started");
            mainHandler.post(() -> setStatus("Listening"));
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
