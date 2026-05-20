package app.romanvoice.ime;

import android.Manifest;
import android.app.Activity;
import android.app.StatusBarManager;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

public class SettingsActivity extends Activity {
    private static final int RECORD_AUDIO_REQUEST = 42;

    private EditText streamUrlField;
    private EditText tokenField;
    private Spinner polishSpinner;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle("RomanVoice Settings");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(20), dp(20), dp(20));
        root.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        streamUrlField = new EditText(this);
        streamUrlField.setSingleLine(true);
        streamUrlField.setHint("ws://PC_TAILSCALE_IP:8799/v1/transcribe/stream");
        streamUrlField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        streamUrlField.setText(RomanVoicePreferences.streamUrl(this));
        root.addView(label("Streaming URL"));
        root.addView(streamUrlField, matchWidth());

        tokenField = new EditText(this);
        tokenField.setSingleLine(true);
        tokenField.setHint("Bearer token");
        tokenField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tokenField.setText(RomanVoicePreferences.token(this));
        root.addView(label("Token"));
        root.addView(tokenField, matchWidth());

        polishSpinner = new Spinner(this);
        String[] modes = new String[]{"settings", "off", "on"};
        polishSpinner.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, modes));
        String currentPolish = RomanVoicePreferences.polish(this);
        for (int index = 0; index < modes.length; index++) {
            if (modes[index].equals(currentPolish)) {
                polishSpinner.setSelection(index);
                break;
            }
        }
        root.addView(label("Polish"));
        root.addView(polishSpinner, matchWidth());

        Button permissionButton = new Button(this);
        permissionButton.setText(hasRecordPermission() ? "Microphone permission granted" : "Grant microphone permission");
        permissionButton.setOnClickListener(view -> requestRecordPermission());
        root.addView(permissionButton, matchWidth());

        Button accessibilityButton = new Button(this);
        accessibilityButton.setText("Open floating mic accessibility setting");
        accessibilityButton.setOnClickListener(view -> openAccessibilitySettings());
        root.addView(accessibilityButton, matchWidth());

        Button tileButton = new Button(this);
        tileButton.setText("Add RomanVoice Quick Settings tile");
        tileButton.setOnClickListener(view -> requestQuickSettingsTile());
        root.addView(tileButton, matchWidth());

        Button saveButton = new Button(this);
        saveButton.setText("Save");
        saveButton.setOnClickListener(view -> {
            RomanVoicePreferences.save(
                    this,
                    streamUrlField.getText().toString(),
                    tokenField.getText().toString(),
                    polishSpinner.getSelectedItem().toString()
            );
            finish();
        });
        root.addView(saveButton, matchWidth());

        setContentView(root);
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setGravity(Gravity.START);
        view.setPadding(0, dp(16), 0, dp(4));
        return view;
    }

    private LinearLayout.LayoutParams matchWidth() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private boolean hasRecordPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void requestRecordPermission() {
        if (!hasRecordPermission()) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, RECORD_AUDIO_REQUEST);
        }
    }

    private void openAccessibilitySettings() {
        startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
    }

    private void requestQuickSettingsTile() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            Toast.makeText(this, "Add RomanVoice from the Quick Settings editor", Toast.LENGTH_LONG).show();
            return;
        }

        StatusBarManager statusBarManager = getSystemService(StatusBarManager.class);
        if (statusBarManager == null) {
            Toast.makeText(this, "Quick Settings tile prompt is unavailable", Toast.LENGTH_LONG).show();
            return;
        }

        statusBarManager.requestAddTileService(
                new ComponentName(this, RomanVoiceTileService.class),
                getString(R.string.tile_service_name),
                Icon.createWithResource(this, R.drawable.ic_romanvoice_tile),
                getMainExecutor(),
                result -> Toast.makeText(this, tilePromptResult(result), Toast.LENGTH_SHORT).show()
        );
    }

    private String tilePromptResult(int result) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return "";
        }
        if (result == StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ADDED) {
            return "RomanVoice tile added";
        }
        if (result == StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ALREADY_ADDED) {
            return "RomanVoice tile is already added";
        }
        return "RomanVoice tile was not added";
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
