"""Tests for light dictation text cleanup."""
import json

from transcriber.local_backend import LocalWhisperBackend
from transcriber.transcript_cleanup import DeterministicTranscriptCleaner


def test_light_cleanup_spacing_capitalization_and_final_period():
    text = LocalWhisperBackend._clean_transcript_text("  hello , world this is i speaking ")

    assert text == "Hello, world this is I speaking."


def test_light_cleanup_preserves_existing_terminal_punctuation():
    text = LocalWhisperBackend._clean_transcript_text("what is this ?")

    assert text == "What is this?"


def test_light_cleanup_capitalizes_after_sentence_end():
    text = LocalWhisperBackend._clean_transcript_text("first sentence. second sentence")

    assert text == "First sentence. Second sentence."


def test_light_cleanup_normalizes_time_meridiems_before_capitalization():
    assert LocalWhisperBackend._clean_transcript_text("4 a.m.") == "4 AM."
    assert LocalWhisperBackend._clean_transcript_text("4 a. m.") == "4 AM."
    assert LocalWhisperBackend._clean_transcript_text("meet me at 4 p.m.") == "Meet me at 4 PM."
    assert LocalWhisperBackend._clean_transcript_text("the call is at 10:30 pm") == "The call is at 10:30 PM."


def test_light_cleanup_normalizes_inline_time_and_ordinal_fragments():
    text = LocalWhisperBackend._clean_transcript_text(
        "i got us dental cleanings this Monday, the 1st. Yours at 10 a.m. , mine's at 1. "
        "it's one of the places on your list"
    )

    assert text == (
        "I got us dental cleanings this Monday, the 1st, yours at 10 AM, mine's at 1. "
        "It's one of the places on your list."
    )


def test_light_cleanup_keeps_terminal_time_meridiem_sentence_boundary():
    text = LocalWhisperBackend._clean_transcript_text(
        "the call is at 10 a.m. it starts with a review"
    )

    assert text == "The call is at 10 AM. It starts with a review."


def test_light_cleanup_normalizes_thousands_separator_spacing():
    text = LocalWhisperBackend._clean_transcript_text(
        "we have a balance of $8, 000 and should apply $2, 000"
    )

    assert text == "We have a balance of $8,000 and should apply $2,000."


def test_deterministic_cleanup_applies_user_glossary(tmp_path):
    glossary = tmp_path / "transcript_glossary.json"
    glossary.write_text(
        json.dumps(
            {
                "replacements": {
                    "a. a. m.": "AAM",
                    "cleo": "Cleo",
                }
            }
        ),
        encoding="utf-8",
    )
    cleaner = DeterministicTranscriptCleaner(glossary_path=str(glossary))

    text = cleaner.clean("talk to a.a.m. about cleo")

    assert text == "Talk to AAM about Cleo."


def test_light_cleanup_trims_repeated_tail_sentence_hallucination():
    text = (
        "i just need to look at something real quick. "
        + "one second, sorry. " * 22
    )

    assert LocalWhisperBackend._clean_transcript_text(text) == (
        "I just need to look at something real quick. One second, sorry."
    )
