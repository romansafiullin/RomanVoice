package app.romanvoice.ime;

import android.content.Context;
import android.content.SharedPreferences;

final class RomanVoicePreferences {
    static final String KEY_STREAM_URL = "stream_url";
    static final String KEY_TOKEN = "token";
    static final String KEY_POLISH = "polish";

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
}
