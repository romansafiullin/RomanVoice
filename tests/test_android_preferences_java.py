import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFERENCES_SOURCE = (
    PROJECT_ROOT
    / "clients"
    / "android-ime"
    / "app"
    / "src"
    / "main"
    / "java"
    / "app"
    / "romanvoice"
    / "ime"
    / "RomanVoicePreferences.java"
)


def test_stream_url_policy_accepts_tailscale_and_requires_explicit_lan(tmp_path):
    android_content = tmp_path / "android" / "content"
    android_content.mkdir(parents=True)
    (android_content / "Context.java").write_text(
        textwrap.dedent(
            """
            package android.content;

            public abstract class Context {
                public static final int MODE_PRIVATE = 0;
                public abstract SharedPreferences getSharedPreferences(String name, int mode);
            }
            """
        ).strip(),
        encoding="utf-8",
    )
    (android_content / "SharedPreferences.java").write_text(
        textwrap.dedent(
            """
            package android.content;

            public interface SharedPreferences {
                String getString(String key, String fallback);
                boolean getBoolean(String key, boolean fallback);
                Editor edit();

                interface Editor {
                    Editor putString(String key, String value);
                    void apply();
                }
            }
            """
        ).strip(),
        encoding="utf-8",
    )

    harness = tmp_path / "RomanVoicePreferencesHarness.java"
    harness.write_text(
        textwrap.dedent(
            """
            package app.romanvoice.ime;

            public final class RomanVoicePreferencesHarness {
                public static void main(String[] args) {
                    assertApproved("ws://100.64.0.1:8799/v1/transcribe/stream", false);
                    assertApproved("ws://100.127.255.255:8799/v1/transcribe/stream", false);
                    assertApproved("wss://roman.pc-name.tail123.ts.net/v1/transcribe/stream", false);
                    assertRejected("ws://100.128.0.1:8799/v1/transcribe/stream", false);
                    assertRejected("ws://192.168.1.232:8799/v1/transcribe/stream", false);
                    assertApproved("ws://192.168.1.232:8799/v1/transcribe/stream", true);
                    assertRejected("ws://8.8.8.8:8799/v1/transcribe/stream", true);
                    assertRejected("http://100.92.44.49:8799/v1/transcribe/stream", false);
                    assertRejected("ws://100.92.44.49:8799/not-romanvoice", false);
                    assertRejected("ws://100.92.44.49:8799/v1/transcribe/stream?token=bad", false);
                    assertRejected("ws://user@100.92.44.49:8799/v1/transcribe/stream", false);
                    assertRejected("ws://100.x.x.x:8799/v1/transcribe/stream", false);
                }

                private static void assertApproved(String url, boolean allowLan) {
                    if (!RomanVoicePreferences.isApprovedStreamUrl(url, allowLan)) {
                        throw new AssertionError("expected approved: " + url);
                    }
                }

                private static void assertRejected(String url, boolean allowLan) {
                    if (RomanVoicePreferences.isApprovedStreamUrl(url, allowLan)) {
                        throw new AssertionError("expected rejected: " + url);
                    }
                }
            }
            """
        ).strip(),
        encoding="utf-8",
    )

    subprocess.run(
        [
            "javac",
            "-d",
            str(tmp_path),
            str(android_content / "Context.java"),
            str(android_content / "SharedPreferences.java"),
            str(PREFERENCES_SOURCE),
            str(harness),
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
    subprocess.run(
        [
            "java",
            "-cp",
            str(tmp_path),
            "app.romanvoice.ime.RomanVoicePreferencesHarness",
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
