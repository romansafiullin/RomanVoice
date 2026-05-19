from services.polisher import LocalPolisher


def test_polisher_returns_raw_when_disabled():
    polisher = LocalPolisher()

    result = polisher.maybe_polish(
        "hello world",
        enabled=False,
        model="unused",
        word_threshold=1,
        timeout_ms=100,
        ollama_url="http://127.0.0.1:9",
    )

    assert result.text == "hello world"
    assert result.used_polish is False


def test_polisher_returns_raw_below_word_threshold():
    polisher = LocalPolisher()

    result = polisher.maybe_polish(
        "short text",
        enabled=True,
        model="unused",
        word_threshold=30,
        timeout_ms=100,
        ollama_url="http://127.0.0.1:9",
    )

    assert result.text == "short text"
    assert result.used_polish is False


def test_polisher_rejects_unusable_output():
    polisher = LocalPolisher()

    assert polisher._usable("hello world", "") is False
    assert polisher._usable("hello world", "As an AI, I cannot help.") is False
    assert polisher._usable("hello world", "Hello, world.") is True
