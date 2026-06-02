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
    private static final long CONNECTING_TIMEOUT_MS = 10000;
    private static final long STOP_SEND_TIMEOUT_MS = 10000;
    private static final long FINAL_RESULT_TIMEOUT_MS = 90000;
    private static final long ERROR_RESET_MS = 3000;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private TextView statusView;
    private Button micButton;
    private Button cancelButton;
    private Button nextKeyboardButton;
    private Runnable connectingTimeoutRunnable;
    private Runnable stopSendTimeoutRunnable;
    private Runnable finalResultTimeoutRunnable;
    private Runnable errorResetRunnable;

    private volatile RomanVoiceRecordingPhase phase = RomanVoiceRecordingPhase.IDLE;
    private volatile AudioRecord audioRecord;
    private Thread audioThread;
    private volatile RomanVoiceStreamClient client;
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
                handlePhaseTimeout("Connecting timed out - try again");
            }
        };
        mainHandler.postDelayed(connectingTimeoutRunnable, CONNECTING_TIMEOUT_MS);
    }

    private void scheduleStopSendTimeout() {
        stopSendTimeoutRunnable = () -> {
            stopSendTimeoutRunnable = null;
            if (phase == RomanVoiceRecordingPhase.FINISHING) {
                handlePhaseTimeout("Finishing timed out - try again");
            }
        };
        mainHandler.postDelayed(stopSendTimeoutRunnable, STOP_SEND_TIMEOUT_MS);
    }

    private void scheduleFinalResultTimeout() {
        finalResultTimeoutRunnable = () -> {
            finalResultTimeoutRunnable = null;
            if (phase == RomanVoiceRecordingPhase.FINISHING) {
                handlePhaseTimeout("Finishing timed out - try again");
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
                setStatus("Ready");
            }
        };
        mainHandler.postDelayed(errorResetRunnable, ERROR_RESET_MS);
    }

    private void handlePhaseTimeout(String message) {
        setPhase(RomanVoiceRecordingPhase.ERROR);
        stopAudioRecord();
        cleanupClient();
        clearComposingText();
        setRecordingControls(false);
        lastPartialText = "";
        setStatus(message);
    }

    private void toggleRecording() {
        if (isRecording()) {
            stopRecording(true);
        } else if (phase == RomanVoiceRecordingPhase.ERROR) {
            setPhase(RomanVoiceRecordingPhase.IDLE);
            startRecording();
        } else if (!isBusyStartingOrFinishing()) {
            startRecording();
        }
    }

    private void startRecording() {
        if (phase != RomanVoiceRecordingPhase.IDLE) {
            return;
        }
        if (!hasRecordPermission()) {
            setStatus("Microphone permission needed");
            openSettings();
            return;
        }

        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (streamUrl == null || streamUrl.trim().isEmpty() || RomanVoicePreferences.isDefaultStreamUrl(streamUrl)) {
            setStatus("Set RomanVoice URL");
            openSettings();
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            setStatus("Set RomanVoice token");
            openSettings();
            return;
        }

        setPhase(RomanVoiceRecordingPhase.CONNECTING);
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
                if (phase != RomanVoiceRecordingPhase.CONNECTING) {
                    streamClient.close();
                    return;
                }
                client = streamClient;
                startAudioPump();
                mainHandler.post(() -> {
                    if (phase == RomanVoiceRecordingPhase.RECORDING) {
                        setRecordingControls(true);
                        setStatus("Listening");
                    }
                });
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice stream connection failed", exception);
                cleanupClient();
                mainHandler.post(() -> {
                    setPhase(RomanVoiceRecordingPhase.ERROR);
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
                    Log.w(TAG, "Audio read failed", exception);
                    mainHandler.post(() -> handleStreamError(shortError(exception)));
                    break;
                }
                RomanVoiceStreamClient streamClient = client;
                if (read > 0 && streamClient != null) {
                    try {
                        streamClient.sendAudio(buffer, read);
                    } catch (IOException exception) {
                        Log.w(TAG, "Failed to send audio chunk", exception);
                        mainHandler.post(() -> handleStreamError(shortError(exception)));
                        break;
                    } catch (RuntimeException exception) {
                        Log.w(TAG, "Audio pump failed", exception);
                        mainHandler.post(() -> handleStreamError(shortError(exception)));
                        break;
                    }
                }
            }
        }, "RomanVoiceAudioPump");
        audioThread.start();
    }

    private void stopRecording(boolean requestFinal) {
        boolean wasRecording = isRecording();
        stopAudioRecord();

        if (requestFinal && client != null) {
            setPhase(RomanVoiceRecordingPhase.FINISHING);
            setStatus("Finishing");
            micButton.setEnabled(false);
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
            }, "RomanVoiceStop").start();
        } else {
            setPhase(RomanVoiceRecordingPhase.IDLE);
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
        boolean wasRecording = isRecording();
        setPhase(RomanVoiceRecordingPhase.IDLE);
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
        if (phase != RomanVoiceRecordingPhase.RECORDING) {
            return;
        }
        lastPartialText = text == null ? "" : text;
        InputConnection connection = getCurrentInputConnection();
        if (connection != null) {
            connection.setComposingText(lastPartialText, 1);
        }
        setStatus("Listening");
    }

    private void handleFinal(String text) {
        if (phase != RomanVoiceRecordingPhase.FINISHING) {
            return;
        }
        stopAudioRecord();
        setPhase(RomanVoiceRecordingPhase.IDLE);
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
        setPhase(RomanVoiceRecordingPhase.ERROR);
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
        if (phase == RomanVoiceRecordingPhase.CONNECTING
                || phase == RomanVoiceRecordingPhase.RECORDING
                || phase == RomanVoiceRecordingPhase.FINISHING) {
            return;
        }
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
                message = "RomanVoice slow - tap mic to try";
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
