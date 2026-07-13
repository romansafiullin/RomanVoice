package app.romanvoice.ime;

import java.util.Locale;

final class RomanVoiceConnectionMessage {
    static final String AUTH_FAILED = "Authentication failed - reinstall RomanVoice";
    static final String NETWORK_FAILED = "Cannot reach your PC - open Tailscale and retry";
    static final String STREAM_FAILED = "RomanVoice stream stopped - try again";

    private RomanVoiceConnectionMessage() {
    }

    static String from(Throwable failure) {
        String message = failure == null ? "" : failure.getMessage();
        return fromMessage(message);
    }

    static String fromMessage(String message) {
        String normalized = message == null ? "" : message.toLowerCase(Locale.US);
        if (normalized.contains(" 401")
                || normalized.contains(" 403")
                || normalized.contains("unauthorized")
                || normalized.contains("forbidden")
                || normalized.contains("authentication")) {
            return AUTH_FAILED;
        }
        if (normalized.contains("timed out")
                || normalized.contains("timeout")
                || normalized.contains("failed to connect")
                || normalized.contains("connection refused")
                || normalized.contains("unreachable")
                || normalized.contains("no route")
                || normalized.contains("closed during handshake")
                || normalized.contains("unable to resolve host")) {
            return NETWORK_FAILED;
        }
        return STREAM_FAILED;
    }
}
