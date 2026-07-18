package app.romanvoice.ime;

import android.content.Context;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class RomanVoicePhoneHeartbeat {
    private static final String TAG = "RomanVoiceHeartbeat";

    private RomanVoicePhoneHeartbeat() {
    }

    static void reportAsync(
            Context context,
            String surface,
            String event,
            boolean available,
            boolean serviceAlive,
            boolean backendReady,
            boolean recording,
            boolean connecting,
            String errorReason
    ) {
        if (context == null) {
            return;
        }
        Context appContext = context.getApplicationContext();
        new Thread(
                () -> report(
                        appContext,
                        surface,
                        event,
                        available,
                        serviceAlive,
                        backendReady,
                        recording,
                        connecting,
                        errorReason
                ),
                "RomanVoicePhoneHeartbeat"
        ).start();
    }

    private static void report(
            Context context,
            String surface,
            String event,
            boolean available,
            boolean serviceAlive,
            boolean backendReady,
            boolean recording,
            boolean connecting,
            String errorReason
    ) {
        String streamUrl = RomanVoicePreferences.streamUrl(context);
        String token = RomanVoicePreferences.token(context);
        if (streamUrl == null
                || streamUrl.trim().isEmpty()
                || RomanVoicePreferences.isDefaultStreamUrl(streamUrl)
                || !RomanVoicePreferences.isApprovedStreamUrl(context, streamUrl)
                || token == null
                || token.trim().isEmpty()) {
            return;
        }

        HttpURLConnection connection = null;
        try {
            URL heartbeatUrl = new URL(streamUrlToHeartbeatUrl(streamUrl));
            JSONObject payload = new JSONObject();
            payload.put("surface", surface == null ? "" : surface);
            payload.put("event", event == null ? "" : event);
            payload.put("available", available);
            payload.put("service_alive", serviceAlive);
            payload.put("backend_ready", backendReady);
            payload.put("recording", recording);
            payload.put("connecting", connecting);
            payload.put("error_reason", errorReason == null ? "" : errorReason);
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);

            connection = (HttpURLConnection) heartbeatUrl.openConnection();
            connection.setConnectTimeout(1500);
            connection.setReadTimeout(1500);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty(
                    "X-RomanVoice-Client",
                    "android-" + safeSurface(surface)
            );
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Content-Length", String.valueOf(body.length));
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
            }
            int code = connection.getResponseCode();
            if (code < 200 || code >= 300) {
                Log.w(TAG, "RomanVoice phone heartbeat failed with HTTP " + code);
            }
        } catch (Exception exception) {
            Log.w(TAG, "RomanVoice phone heartbeat failed", exception);
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static String streamUrlToHeartbeatUrl(String streamUrl) {
        URI uri = URI.create(streamUrl);
        String scheme = "wss".equalsIgnoreCase(uri.getScheme()) ? "https" : "http";
        int port = uri.getPort();
        StringBuilder url = new StringBuilder();
        url.append(scheme).append("://").append(uri.getHost());
        if (port >= 0) {
            url.append(":").append(port);
        }
        url.append("/v1/phone/heartbeat");
        return url.toString();
    }

    private static String safeSurface(String surface) {
        if (surface == null || surface.trim().isEmpty()) {
            return "unknown";
        }
        return surface.replaceAll("[^A-Za-z0-9._-]", "-");
    }
}
