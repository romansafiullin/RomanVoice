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
    private static final long THREAD_JOIN_TIMEOUT_MS = 750;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private TextView statusView;
    private Button micButton;
    private Button cancelButton;
    private Button nextKeyboardButton;
    private Runnable connectingTimeoutRunnable;
    private Runnable stopSendTimeoutRunnable;
    private Runnable finalResultTimeoutRunnable;

    private volatile RomanVoiceRecordingPhase phase = RomanVoiceRecordingPhase.IDLE;
    private volatile AudioRecord audioRecord;
    private volatile Thread audioThread;
    private volatile RomanVoiceStreamClient client;
    private volatile int sessionGeneration;
    private volatile int clientGeneration;
    private String lastPartialText = "";
    private String bestPartialText = "";

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

    private void handlePhaseTimeout(String message) {
        handleStreamError(sessionGeneration, message);
    }

    private void toggleRecording() {
        if (isRecording()) {
            stopRecording(true);
        } else if (phase == RomanVoiceRecordingPhase.ERROR) {
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            startRecording();
        } else if (isBusyStartingOrFinishing()) {
            cancelRecording();
        } else if (!isBusyStartingOrFinishing()) {
            startRecording();
        }
    }

    private void startRecording() {
        if (phase != RomanVoiceRecordingPhase.IDLE) {
            return;
        }
        if (!hasRecordPermission()) {
            invalidateSession(RomanVoiceRecordingPhase.ERROR);
            setRecordingControls(false);
            setStatus("Microphone permission needed");
            openSettings();
            return;
        }

        String streamUrl = RomanVoicePreferences.streamUrl(this);
        String token = RomanVoicePreferences.token(this);
        if (streamUrl == null || streamUrl.trim().isEmpty() || RomanVoicePreferences.isDefaultStreamUrl(streamUrl)) {
            invalidateSession(RomanVoiceRecordingPhase.ERROR);
            setRecordingControls(false);
            setStatus("Set RomanVoice URL");
            openSettings();
            return;
        }
        if (token == null || token.trim().isEmpty()) {
            invalidateSession(RomanVoiceRecordingPhase.ERROR);
            setRecordingControls(false);
            setStatus("Set RomanVoice token");
            openSettings();
            return;
        }

        int generation = beginSession();
        bestPartialText = "";
        lastPartialText = "";
        setStatus("Connecting");
        micButton.setEnabled(false);
        if (cancelButton != null) {
            cancelButton.setEnabled(true);
            cancelButton.setVisibility(View.VISIBLE);
        }

        new Thread(() -> {
            RomanVoiceStreamClient streamClient = null;
            try {
                Log.i(TAG, "Connecting to RomanVoice stream: " + streamUrl);
                streamClient = new RomanVoiceStreamClient(
                        streamUrl,
                        token,
                        "android-ime",
                        new StreamListener(generation)
                );
                streamClient.connect();
                streamClient.sendStart(SAMPLE_RATE, RomanVoicePreferences.polish(this));
                if (!isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
                    streamClient.close();
                    return;
                }
                client = streamClient;
                clientGeneration = generation;
                startAudioPump(generation, streamClient);
                mainHandler.post(() -> {
                    if (isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
                        setRecordingControls(true);
                        setStatus("Listening");
                    }
                });
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice stream connection failed", exception);
                if (streamClient != null) {
                    streamClient.close();
                }
                mainHandler.post(() -> {
                    if (isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
                        handleStreamError(generation, RomanVoiceConnectionMessage.from(exception));
                    }
                });
            }
        }, "RomanVoiceConnect").start();
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
                    Log.w(TAG, "Audio read failed", exception);
                    mainHandler.post(() -> handleStreamError(
                            generation,
                            "Microphone stopped - try again"
                    ));
                    break;
                }
                if (read < 0) {
                    Log.w(TAG, "Audio read returned error " + read);
                    mainHandler.post(() -> handleStreamError(
                            generation,
                            "Microphone stopped - try again"
                    ));
                    break;
                }
                if (read > 0) {
                    try {
                        streamClient.sendAudio(buffer, read);
                    } catch (IOException exception) {
                        Log.w(TAG, "Failed to send audio chunk", exception);
                        mainHandler.post(() -> handleStreamError(
                                generation,
                                RomanVoiceConnectionMessage.from(exception)
                        ));
                        break;
                    } catch (RuntimeException exception) {
                        Log.w(TAG, "Audio pump failed", exception);
                        mainHandler.post(() -> handleStreamError(
                                generation,
                                RomanVoiceConnectionMessage.from(exception)
                        ));
                        break;
                    }
                }
            }
        }, "RomanVoiceAudioPump");
        audioThread.start();
    }

    private void stopRecording(boolean requestFinal) {
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
            setStatus("Finishing");
            micButton.setEnabled(false);
            if (cancelButton != null) {
                cancelButton.setEnabled(true);
                cancelButton.setVisibility(View.VISIBLE);
            }
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
                            RomanVoiceConnectionMessage.from(exception)
                    ));
                } catch (RuntimeException exception) {
                    mainHandler.post(() -> handleStreamError(
                            generation,
                            RomanVoiceConnectionMessage.from(exception)
                    ));
                }
            }, "RomanVoiceStop").start();
        } else {
            invalidateSession(RomanVoiceRecordingPhase.IDLE);
            stopAudioRecord();
            cleanupClient();
            mainHandler.post(() -> {
                setRecordingControls(false);
                setStatus("Ready");
            });
        }
    }

    private void cancelRecording() {
        boolean hadClient = client != null;
        boolean wasBusy = phase == RomanVoiceRecordingPhase.CONNECTING
                || phase == RomanVoiceRecordingPhase.RECORDING
                || phase == RomanVoiceRecordingPhase.FINISHING;
        invalidateSession(RomanVoiceRecordingPhase.IDLE);
        stopAudioRecord();
        clearComposingText();
        cleanupClient();
        setRecordingControls(false);
        lastPartialText = "";
        bestPartialText = "";
        setStatus(wasBusy || hadClient ? "Canceled" : "Ready");
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
        lastPartialText = next;
        if (!next.isEmpty()) {
            bestPartialText = next;
        }
        InputConnection connection = getCurrentInputConnection();
        if (connection != null) {
            connection.setComposingText(lastPartialText, 1);
        }
        setStatus("Listening");
    }

    private void handleFinal(int generation, String text) {
        if (!isCurrentSession(generation, RomanVoiceRecordingPhase.FINISHING)) {
            return;
        }
        stopAudioRecord();
        String finalText = RomanVoiceTextRange.chooseFinalText(text, bestPartialText);
        cleanupClient(generation);
        if (!completeSession(generation, RomanVoiceRecordingPhase.IDLE)) {
            return;
        }
        InputConnection connection = getCurrentInputConnection();
        if (connection != null) {
            if (finalText.isEmpty()) {
                clearComposingText();
            } else {
                connection.commitText(finalText, 1);
            }
        }
        setRecordingControls(false);
        lastPartialText = "";
        bestPartialText = "";
        setStatus(finalText.isEmpty() ? "No speech detected" : "Ready");
    }

    private void handleStreamError(int generation, String message) {
        if (!completeSession(generation, RomanVoiceRecordingPhase.ERROR)) {
            return;
        }
        stopAudioRecord();
        cleanupClient(generation);
        InputConnection connection = getCurrentInputConnection();
        if (connection != null && !bestPartialText.isEmpty()) {
            connection.commitText(bestPartialText, 1);
        } else {
            clearComposingText();
        }
        setRecordingControls(false);
        lastPartialText = "";
        bestPartialText = "";
        setStatus(message == null || message.isEmpty()
                ? RomanVoiceConnectionMessage.NETWORK_FAILED
                : message);
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

    private void pingService() {
        if (phase != RomanVoiceRecordingPhase.IDLE) {
            return;
        }
        setStatus("Checking RomanVoice");
        int generation = sessionGeneration;
        new Thread(() -> {
            String message;
            try {
                String healthUrl = streamUrlToHealthUrl(RomanVoicePreferences.streamUrl(this));
                Log.i(TAG, "Checking RomanVoice health: " + healthUrl);
                HttpURLConnection connection = (HttpURLConnection) new URL(healthUrl).openConnection();
                connection.setConnectTimeout(1500);
                connection.setReadTimeout(1500);
                connection.setRequestProperty("Authorization", "Bearer " + RomanVoicePreferences.token(this));
                connection.setRequestProperty("X-RomanVoice-Client", "android-ime-health");
                int code = connection.getResponseCode();
                if (code == 200) {
                    message = "Ready";
                } else if (code == 401 || code == 403) {
                    message = RomanVoiceConnectionMessage.AUTH_FAILED;
                } else {
                    message = "RomanVoice unavailable (HTTP " + code + ")";
                }
                connection.disconnect();
            } catch (Exception exception) {
                Log.w(TAG, "RomanVoice health check failed", exception);
                message = RomanVoiceConnectionMessage.from(exception);
            }
            String finalMessage = message;
            mainHandler.post(() -> {
                if (generation == sessionGeneration && phase == RomanVoiceRecordingPhase.IDLE) {
                    setStatus(finalMessage);
                }
            });
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
        private final int generation;

        StreamListener(int generation) {
            this.generation = generation;
        }

        @Override
        public void onReady() {
            Log.i(TAG, "RomanVoice stream ready");
            mainHandler.post(() -> {
                if (isCurrentSession(generation, RomanVoiceRecordingPhase.CONNECTING)) {
                    setStatus("Connected");
                }
            });
        }

        @Override
        public void onStarted() {
            Log.i(TAG, "RomanVoice stream started");
            mainHandler.post(() -> {
                if (isCurrentSession(generation, RomanVoiceRecordingPhase.RECORDING)) {
                    setStatus("Listening");
                }
            });
        }

        @Override
        public void onPartial(String text) {
            Log.d(TAG, "RomanVoice partial length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handlePartial(generation, text));
        }

        @Override
        public void onFinal(String text) {
            Log.i(TAG, "RomanVoice final length=" + (text == null ? 0 : text.length()));
            mainHandler.post(() -> handleFinal(generation, text));
        }

        @Override
        public void onError(String message) {
            Log.w(TAG, "RomanVoice stream error: " + message);
            mainHandler.post(() -> handleStreamError(
                    generation,
                    RomanVoiceConnectionMessage.fromMessage(message)
            ));
        }
    }
}
