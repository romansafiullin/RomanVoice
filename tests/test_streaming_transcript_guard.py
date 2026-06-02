from services.streaming_transcript_guard import choose_streaming_transcript


def test_prefers_streaming_when_final_is_much_shorter_and_streaming_is_clean():
    streaming = "This is the clean streaming transcript. " * 30
    final = "This is the clean streaming transcript. " * 10

    decision = choose_streaming_transcript(
        final,
        streaming,
        duration_seconds=90.0,
    )

    assert decision.prefer_streaming is True
    assert decision.reason == "final_much_shorter"


def test_does_not_prefer_streaming_with_replacement_char_hallucination():
    streaming = (
        "This begins as normal dictated text. "
        "Then it breaks into garbage ��ang tonight is not free. "
    ) * 12
    final = "This begins as normal dictated text. " * 12

    decision = choose_streaming_transcript(
        final,
        streaming,
        duration_seconds=90.0,
    )

    assert decision.prefer_streaming is False
    assert decision.reason == "streaming_suspicious_replacement_char"


def test_does_not_prefer_streaming_with_non_latin_script_hallucination():
    streaming = (
        "This starts normally but then includes 혹 and опрос fragments. "
    ) * 18
    final = "This starts normally but then stays in English. " * 8

    decision = choose_streaming_transcript(
        final,
        streaming,
        duration_seconds=90.0,
    )

    assert decision.prefer_streaming is False
    assert decision.reason == "streaming_suspicious_non_latin_script"


def test_does_not_prefer_streaming_with_repeated_word_run():
    streaming = (
        "This starts normally before action action action action action action "
        "keeps repeating in the transcript. "
    ) * 10
    final = "This starts normally before continuing cleanly. " * 8

    decision = choose_streaming_transcript(
        final,
        streaming,
        duration_seconds=90.0,
    )

    assert decision.prefer_streaming is False
    assert decision.reason == "streaming_suspicious_repeated_word_run"
