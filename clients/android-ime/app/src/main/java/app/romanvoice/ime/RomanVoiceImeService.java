package app.romanvoice.ime;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.inputmethodservice.InputMethodService;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;

public class RomanVoiceImeService extends InputMethodService {
    private static final String TAG = "RomanVoiceIme";
    private static final int SAMPLE_RATE = 16000;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private TextView statusView;
    private Button micButton;
    private Button cancelButton;
    private Button nextKeyboardButton;

    private volatile boolean recording;
    private AudioRecord audioRecord;
    private Thread audioThread;
    private RomanVoiceStreamClient client;
    private String lastPartialText = "";

    @Override
    public View onCreateInputView() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(12), dp(10), dp(12), dp(10));
        root.setBackgroundColor(0xFFF6F7F4);

        LinearLayout topRow = new LinearLayout(this);
        topRow.setOrientation(LinearLayout.HORIZONTAL);
        topRow.setGravity(Gravity.CENTER_VERTICAL);

        statusView = new TextView(this);
        statusView.setText("Checking RomanVoice");
        statusView.setTextColor(0xFF25312C);
        statusView.setSingleLine(false);
        topRow.addView(statusView, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        Button settingsButton = new Button(this);
        settingsButton.setText("Settings");
        settingsButton.setOnClickListener(view -> openSettings());
        topRow.addView(settingsButton, compactButtonParams());

        root.addView(topRow, matchWidth());

        LinearLayout actionRow = new LinearLayout(this);
        actionRow.setOrientation(LinearLayout.HORIZONTAL);
        actionRow.setGravity(Gravity.CENTER_VERTICAL);

        micButton = new Button(this);
        micButton.setText("Mic");
        micButton.setOnClickListener(view -> toggleRecording());
        actionRow.addView(micButton, new LinearLayout.LayoutParams(0, dp(56), 1f));

        cancelButton = new Button(this);
        cancelButton.setText("Cancel");
        cancelButton.setVisibility(View.GONE);
        cancelButton.setOnClickListener(view -> cancelRecording());
        actionRow.addView(cancelButton, new LinearLayout.LayoutParams(0, dp(56), 1f));

        nextKeyboardButton = new Button(this);
        nextKeyboardButton.setText("Keyboard");
        nextKeyboardButton.setOnClickListener(view -> switchKeyboard());
        actionRow.addView(nextKeyboardButton, new LinearLayout.LayoutParams(0, dp(56), 1f));

        root.addView(actionRow, matchWidth());
        return root;
    }

    @Override
    public void onStartInputView(android.view.inputmethod.EditorInfo info, boolean restarting) {
        super.onStartInputView(info, restarting);
        pingService();
    }

    @Override
    public void onFinishInput() {
        stopRecording(false);
        super.onFinishInput();
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
            setStatus("Microphone permission needed");
            openSettings();
            return;
        }

        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (streamUrl == null || streamUrl.trim().isEmpty() || streamUrl.contains("100.x.x.x")) {
            setStatus("Set RomanVoice URL");
            openSettings();
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            setStatus("Set RomanVoice token");
            openSettings();
            return;
        }

        setStatus("Connecting");
        micButton.setEnabled(false);
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
                client = streamClient;
                startAudioPump();
                mainHandler.post(() -> {
                    setRecordingControls(true);
                    setStatus("Listening");
                });
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice stream connection failed", exception);
                cleanupClient();
                mainHandler.post(() -> {
                    setRecordingControls(false);
                    setStatus(shortError(exception));
                });
            }
        }, "RomanVoiceConnect").start();
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
                        Log.w(TAG, "Failed to send audio chunk", exception);
                        mainHandler.post(() -> handleStreamError(shortError(exception)));
                        break;
                    }
                }
            }
        }, "RomanVoiceAudioPump");
        audioThread.start();
    }

    private void stopRecording(boolean requestFinal) {
        boolean wasRecording = recording;
        recording = false;
        stopAudioRecord();

        if (requestFinal && client != null) {
            setStatus("Finishing");
            micButton.setEnabled(false);
            if (cancelButton != null) {
                cancelButton.setVisibility(View.GONE);
            }
            new Thread(() -> {
                try {
                    client.sendStop();
                } catch (IOException exception) {
                    mainHandler.post(() -> handleStreamError(shortError(exception)));
                }
            }, "RomanVoiceStop").start();
        } else {
            cleanupClient();
            if (wasRecording) {
                mainHandler.post(() -> {
                    setRecordingControls(false);
                    setStatus("Ready");
                });
            }
        }
    }

    private void cancelRecording() {
        boolean hadClient = client != null;
        boolean wasRecording = recording;
        recording = false;
        stopAudioRecord();
        clearComposingText();
        cleanupClient();
        setRecordingControls(false);
        lastPartialText = "";
        setStatus(wasRecording || hadClient ? "Canceled" : "Ready");
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
        lastPartialText = text == null ? "" : text;
        InputConnection connection = getCurrentInputConnection();
        if (connection != null) {
            connection.setComposingText(lastPartialText, 1);
        }
        setStatus("Listening");
    }

    private void handleFinal(String text) {
        stopAudioRecord();
        String finalText = text == null ? "" : text;
        InputConnection connection = getCurrentInputConnection();
        if (connection != null) {
            if (finalText.isEmpty()) {
                clearComposingText();
            } else {
                connection.commitText(finalText, 1);
            }
        }
        cleanupClient();
        setRecordingControls(false);
        lastPartialText = "";
        setStatus(finalText.isEmpty() ? "No speech detected" : "Ready");
    }

    private void handleStreamError(String message) {
        recording = false;
        stopAudioRecord();
        cleanupClient();
        clearComposingText();
        setRecordingControls(false);
        lastPartialText = "";
        setStatus(message == null || message.isEmpty() ? "RomanVoice offline" : message);
    }

    private void cleanupClient() {
        RomanVoiceStreamClient streamClient = client;
        client = null;
        if (streamClient != null) {
            streamClient.close();
        }
    }

    private void pingService() {
        setStatus("Checking RomanVoice");
        new Thread(() -> {
            String message;
            try {
                String healthUrl = streamUrlToHealthUrl(RomanVoicePreferences.streamUrl(this));
                Log.i(TAG, "Checking RomanVoice health: " + healthUrl);
                HttpURLConnection connection = (HttpURLConnection) new URL(healthUrl).openConnection();
                connection.setConnectTimeout(1500);
                connection.setReadTimeout(1500);
                connection.setRequestProperty("Authorization", "Bearer " + RomanVoicePreferences.token(this));
                int code = connection.getResponseCode();
                message = code == 200 ? "Ready" : "RomanVoice offline";
                connection.disconnect();
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice health check failed", exception);
                message = "RomanVoice offline";
            }
            String finalMessage = message;
            mainHandler.post(() -> setStatus(finalMessage));
        }, "RomanVoiceHealth").start();
    }

    private String streamUrlToHealthUrl(String streamUrl) {
        URI uri = URI.create(streamUrl);
        String scheme = "wss".equalsIgnoreCase(uri.getScheme()) ? "https" : "http";
        int port = uri.getPort();
        StringBuilder url = new StringBuilder();
        url.append(scheme).append("://").append(uri.getHost());
        if (port >= 0) {
            url.append(":").append(port);
        }
        url.append("/v1/health");
        return url.toString();
    }

    private boolean hasRecordPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void openSettings() {
        Intent intent = new Intent(this, SettingsActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
    }

    private void switchKeyboard() {
        InputMethodManager manager = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
        if (manager != null) {
            manager.showInputMethodPicker();
        }
    }

    private void setStatus(String text) {
        if (statusView != null) {
            statusView.setText(text);
        }
    }

    private void setRecordingControls(boolean isRecording) {
        if (micButton != null) {
            micButton.setText(isRecording ? "Stop" : "Mic");
            micButton.setEnabled(true);
        }
        if (cancelButton != null) {
            cancelButton.setVisibility(isRecording ? View.VISIBLE : View.GONE);
        }
    }

    private void clearComposingText() {
        InputConnection connection = getCurrentInputConnection();
        if (connection == null) {
            return;
        }
        if (!lastPartialText.isEmpty()) {
            connection.setComposingText("", 1);
        }
        connection.finishComposingText();
    }

    private String shortError(Exception exception) {
        String message = exception.getMessage();
        if (message == null || message.trim().isEmpty()) {
            return "RomanVoice offline";
        }
        if (message.length() > 80) {
            return message.substring(0, 80);
        }
        return message;
    }

    private LinearLayout.LayoutParams matchWidth() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private LinearLayout.LayoutParams compactButtonParams() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(44)
        );
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private final class StreamListener implements RomanVoiceStreamClient.Listener {
        @Override
        public void onReady() {
            Log.i(TAG, "RomanVoice stream ready");
            mainHandler.post(() -> setStatus("Connected"));
        }

        @Override
        public void onStarted() {
            Log.i(TAG, "RomanVoice stream started");
            mainHandler.post(() -> setStatus("Listening"));
        }

        @Override
        public void onPartial(String text) {
            Log.d(TAG, "RomanVoice partial length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handlePartial(text));
        }

        @Override
        public void onFinal(String text) {
            Log.i(TAG, "RomanVoice final length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handleFinal(text));
        }

        @Override
        public void onError(String message) {
            Log.w(TAG, "RomanVoice stream error: " + message);
            mainHandler.post(() -> handleStreamError(message));
        }
    }
}
