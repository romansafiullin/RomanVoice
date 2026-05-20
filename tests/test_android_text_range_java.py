import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANDROID_IME_ROOT = PROJECT_ROOT / "clients" / "android-ime"
TEXT_RANGE_SOURCE = (
    ANDROID_IME_ROOT
    / "app"
    / "src"
    / "main"
    / "java"
    / "app"
    / "romanvoice"
    / "ime"
    / "RomanVoiceTextRange.java"
)


def test_text_range_helper_does_not_relocate_short_common_text(tmp_path):
    harness = tmp_path / "RomanVoiceTextRangeHarness.java"
    harness.write_text(
        textwrap.dedent(
            """
            package app.romanvoice.ime;

            public final class RomanVoiceTextRangeHarness {
                public static void main(String[] args) {
                    assertRange(
                        RomanVoiceTextRange.findLiveDictationRange("one ok two", 4, 6, "ok"),
                        4,
                        6
                    );
                    assertNull(
                        RomanVoiceTextRange.findLiveDictationRange("the original text", 20, 23, "the")
                    );
                    assertRange(
                        RomanVoiceTextRange.findLiveDictationRange(
                            "prefix dictated words suffix",
                            0,
                            5,
                            "dictated words"
                        ),
                        7,
                        21
                    );
                    assertNull(
                        RomanVoiceTextRange.findLiveDictationRange(
                            "dictated words and dictated words",
                            0,
                            5,
                            "dictated words"
                        )
                    );
                }

                private static void assertRange(int[] range, int start, int end) {
                    if (range == null || range[0] != start || range[1] != end) {
                        throw new AssertionError("range mismatch");
                    }
                }

                private static void assertNull(int[] range) {
                    if (range != null) {
                        throw new AssertionError("expected null");
                    }
                }
            }
            """
        ).strip(),
        encoding="utf-8",
    )

    subprocess.run(
        ["javac", "-d", str(tmp_path), str(TEXT_RANGE_SOURCE), str(harness)],
        check=True,
        cwd=PROJECT_ROOT,
    )
    subprocess.run(
        ["java", "-cp", str(tmp_path), "app.romanvoice.ime.RomanVoiceTextRangeHarness"],
        check=True,
        cwd=PROJECT_ROOT,
    )
