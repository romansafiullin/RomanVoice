import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
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
    / "RomanVoiceConnectionMessage.java"
)


def test_connection_failure_messages_distinguish_auth_network_and_stream(tmp_path):
    harness = tmp_path / "RomanVoiceConnectionMessageHarness.java"
    harness.write_text(
        textwrap.dedent(
            """
            package app.romanvoice.ime;

            public final class RomanVoiceConnectionMessageHarness {
                public static void main(String[] args) {
                    assertEquals(
                        RomanVoiceConnectionMessage.AUTH_FAILED,
                        RomanVoiceConnectionMessage.fromMessage(
                            "RomanVoice refused stream: HTTP/1.1 401 Unauthorized"
                        )
                    );
                    assertEquals(
                        RomanVoiceConnectionMessage.NETWORK_FAILED,
                        RomanVoiceConnectionMessage.fromMessage("Connection refused")
                    );
                    assertEquals(
                        RomanVoiceConnectionMessage.STREAM_FAILED,
                        RomanVoiceConnectionMessage.fromMessage(
                            "RomanVoice stream closed unexpectedly"
                        )
                    );
                }

                private static void assertEquals(String expected, String actual) {
                    if (!expected.equals(actual)) {
                        throw new AssertionError(
                            "expected=" + expected + " actual=" + actual
                        );
                    }
                }
            }
            """
        ).strip(),
        encoding="utf-8",
    )

    subprocess.run(
        ["javac", "-d", str(tmp_path), str(SOURCE), str(harness)],
        check=True,
        cwd=PROJECT_ROOT,
    )
    subprocess.run(
        [
            "java",
            "-cp",
            str(tmp_path),
            "app.romanvoice.ime.RomanVoiceConnectionMessageHarness",
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
