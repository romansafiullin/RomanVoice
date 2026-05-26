"""Tests for light dictation text cleanup."""
from transcriber.local_backend import LocalWhisperBackend


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
