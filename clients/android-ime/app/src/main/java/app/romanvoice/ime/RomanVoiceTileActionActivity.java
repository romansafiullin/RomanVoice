package app.romanvoice.ime;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;

public class RomanVoiceTileActionActivity extends Activity {
    private static final long TOGGLE_AFTER_FINISH_MS = 250;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        finish();
        new Handler(Looper.getMainLooper()).postDelayed(
                RomanVoiceFloatingService::requestToggleFromTile,
                TOGGLE_AFTER_FINISH_MS
        );
    }
}
