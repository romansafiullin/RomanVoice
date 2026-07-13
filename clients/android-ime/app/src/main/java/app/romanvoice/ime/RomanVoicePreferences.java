package app.romanvoice.ime;

import android.content.Context;
import android.content.SharedPreferences;

import java.net.URI;
import java.util.Locale;

final class RomanVoicePreferences {
    static final String KEY_STREAM_URL = "stream_url";
    static final String KEY_TOKEN = "token";
    static final String KEY_POLISH = "polish";
    static final String KEY_ALLOW_LAN_STREAM = "allow_lan_stream";

    private static final String PREFS_NAME = "romanvoice_ime";
    private static final String DEFAULT_STREAM_URL = "ws://100.x.x.x:8799/v1/transcribe/stream";
    private static final String DEFAULT_POLISH = "settings";

    private RomanVoicePreferences() {
    }

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    static String streamUrl(Context context) {
        return prefs(context).getString(KEY_STREAM_URL, DEFAULT_STREAM_URL);
    }

    static boolean isDefaultStreamUrl(String streamUrl) {
        return DEFAULT_STREAM_URL.equals((streamUrl == null ? "" : streamUrl.trim()));
    }

    static boolean isApprovedStreamUrl(Context context, String streamUrl) {
        return isApprovedStreamUrl(streamUrl, allowLanStream(context));
    }

    static boolean isApprovedStreamUrl(String streamUrl, boolean allowLanStream) {
        if (streamUrl == null || streamUrl.trim().isEmpty()) {
            return false;
        }

        try {
            URI uri = URI.create(streamUrl.trim());
            String scheme = uri.getScheme();
            String host = uri.getHost();
            String path = uri.getRawPath();
            if ((!"ws".equalsIgnoreCase(scheme) && !"wss".equalsIgnoreCase(scheme))
                    || host == null
                    || host.trim().isEmpty()
                    || uri.getRawUserInfo() != null
                    || uri.getRawQuery() != null
                    || uri.getRawFragment() != null
                    || (!"/v1/transcribe/stream".equals(path)
                    && !"/v1/transcribe/stream/".equals(path))
                    || uri.getPort() > 65535) {
                return false;
            }

            String normalizedHost = host.trim().toLowerCase(Locale.US);
            return isTailscaleHost(normalizedHost)
                    || (allowLanStream && isPrivateLanHost(normalizedHost));
        } catch (IllegalArgumentException exception) {
            return false;
        }
    }

    static boolean allowLanStream(Context context) {
        return prefs(context).getBoolean(KEY_ALLOW_LAN_STREAM, false);
    }

    static String token(Context context) {
        return prefs(context).getString(KEY_TOKEN, "");
    }

    static String polish(Context context) {
        return prefs(context).getString(KEY_POLISH, DEFAULT_POLISH);
    }

    static void save(Context context, String streamUrl, String token, String polish) {
        prefs(context)
                .edit()
                .putString(KEY_STREAM_URL, streamUrl == null ? "" : streamUrl.trim())
                .putString(KEY_TOKEN, token == null ? "" : token.trim())
                .putString(KEY_POLISH, polish == null ? DEFAULT_POLISH : polish.trim())
                .apply();
    }

    private static boolean isTailscaleHost(String host) {
        if (host.endsWith(".ts.net") && hasValidDnsLabels(host)) {
            return true;
        }
        int[] bytes = parseIpv4(host);
        return bytes != null && bytes[0] == 100 && bytes[1] >= 64 && bytes[1] <= 127;
    }

    private static boolean isPrivateLanHost(String host) {
        int[] bytes = parseIpv4(host);
        return bytes != null && (
                bytes[0] == 10
                        || (bytes[0] == 172 && bytes[1] >= 16 && bytes[1] <= 31)
                        || (bytes[0] == 192 && bytes[1] == 168)
        );
    }

    private static int[] parseIpv4(String host) {
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) {
            return null;
        }
        int[] bytes = new int[4];
        for (int index = 0; index < parts.length; index++) {
            if (parts[index].isEmpty() || !parts[index].matches("[0-9]{1,3}")) {
                return null;
            }
            int value;
            try {
                value = Integer.parseInt(parts[index]);
            } catch (NumberFormatException exception) {
                return null;
            }
            if (value > 255) {
                return null;
            }
            bytes[index] = value;
        }
        return bytes;
    }

    private static boolean hasValidDnsLabels(String host) {
        String[] labels = host.split("\\.", -1);
        for (String label : labels) {
            if (label.isEmpty() || label.length() > 63
                    || !Character.isLetterOrDigit(label.charAt(0))
                    || !Character.isLetterOrDigit(label.charAt(label.length() - 1))) {
                return false;
            }
            for (int index = 0; index < label.length(); index++) {
                char value = label.charAt(index);
                if (!Character.isLetterOrDigit(value) && value != '-') {
                    return false;
                }
            }
        }
        return true;
    }
}
