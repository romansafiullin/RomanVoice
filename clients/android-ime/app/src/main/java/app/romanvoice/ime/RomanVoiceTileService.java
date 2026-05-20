package app.romanvoice.ime;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.widget.Toast;

public class RomanVoiceTileService extends TileService {
    static void requestStateUpdate(Context context) {
        if (context == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            return;
        }
        TileService.requestListeningState(
                context,
                new ComponentName(context, RomanVoiceTileService.class)
        );
    }

    @Override
    public void onStartListening() {
        super.onStartListening();
        updateTile();
    }

    @Override
    public void onClick() {
        super.onClick();
        if (isLocked()) {
            updateTile();
            Toast.makeText(this, "Unlock before dictating", Toast.LENGTH_SHORT).show();
            return;
        }

        if (RomanVoiceFloatingService.isAvailableForTile()) {
            launchToggleActivity();
            return;
        }

        updateTileUnavailable();
        Intent intent = new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivityAndCollapseCompat(intent);
    }

    private void updateTile() {
        Tile tile = getQsTile();
        if (tile == null) {
            return;
        }

        tile.setLabel("RomanVoice");
        if (isLocked()) {
            tile.setSubtitle("Unlock first");
            tile.setState(Tile.STATE_UNAVAILABLE);
            tile.updateTile();
            return;
        }

        TileState state = RomanVoiceFloatingService.getTileStateForTile();
        switch (state) {
            case LISTENING:
                tile.setSubtitle("Listening");
                tile.setState(Tile.STATE_ACTIVE);
                break;
            case CONNECTING:
                tile.setSubtitle("Connecting");
                tile.setState(Tile.STATE_ACTIVE);
                break;
            case READY:
                tile.setSubtitle("Ready");
                tile.setState(Tile.STATE_INACTIVE);
                break;
            case UNAVAILABLE:
            default:
                tile.setSubtitle("Enable Floating Mic");
                tile.setState(Tile.STATE_UNAVAILABLE);
                break;
        }
        tile.updateTile();
    }

    private void updateTileUnavailable() {
        updateTile();
        Toast.makeText(this, "Enable RomanVoice Floating Mic first", Toast.LENGTH_SHORT).show();
    }

    private void startActivityAndCollapseCompat(Intent intent) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startActivityAndCollapse(
                    android.app.PendingIntent.getActivity(
                            this,
                            0,
                            intent,
                            android.app.PendingIntent.FLAG_IMMUTABLE
                    )
            );
        } else {
            startActivityAndCollapse(intent);
        }
    }

    private void launchToggleActivity() {
        Intent intent = new Intent(this, RomanVoiceTileActionActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivityAndCollapseCompat(intent);
    }
}
