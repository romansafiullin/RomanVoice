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
    / "RomanVoiceKeepAlivePolicy.java"
)


def test_keepalive_waits_for_outstanding_pong_and_times_out(tmp_path):
    harness = tmp_path / "RomanVoiceKeepAlivePolicyHarness.java"
    harness.write_text(
        textwrap.dedent(
            """
            package app.romanvoice.ime;

            public final class RomanVoiceKeepAlivePolicyHarness {
                public static void main(String[] args) {
                    assertAction(
                        RomanVoiceKeepAlivePolicy.Action.SEND,
                        RomanVoiceKeepAlivePolicy.nextAction(5000, 0, 12000)
                    );
                    assertAction(
                        RomanVoiceKeepAlivePolicy.Action.WAIT,
                        RomanVoiceKeepAlivePolicy.nextAction(10000, 5000, 12000)
                    );
                    assertAction(
                        RomanVoiceKeepAlivePolicy.Action.WAIT,
                        RomanVoiceKeepAlivePolicy.nextAction(17000, 5000, 12000)
                    );
                    assertAction(
                        RomanVoiceKeepAlivePolicy.Action.TIMEOUT,
                        RomanVoiceKeepAlivePolicy.nextAction(17001, 5000, 12000)
                    );
                }

                private static void assertAction(
                        RomanVoiceKeepAlivePolicy.Action expected,
                        RomanVoiceKeepAlivePolicy.Action actual
                ) {
                    if (expected != actual) {
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
            "app.romanvoice.ime.RomanVoiceKeepAlivePolicyHarness",
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
