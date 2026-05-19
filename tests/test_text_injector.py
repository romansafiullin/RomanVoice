import ctypes

from services.text_injector import INPUT, InjectionResult, TextInjector


def test_input_structure_matches_windows_sendinput_size():
    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28

    assert ctypes.sizeof(INPUT) == expected_size


def test_utf16_code_units_handles_ascii_and_surrogate_pairs():
    units = TextInjector._utf16_code_units("A😀")

    assert units == [0x0041, 0xD83D, 0xDE00]


def test_empty_text_is_noop():
    result = TextInjector().inject("")

    assert result.success is True
    assert result.method == "none"


def test_common_prefix_length_counts_shared_start():
    assert TextInjector._common_prefix_length("hello wurld", "hello world") == 7
    assert TextInjector._common_prefix_length("abc", "xyz") == 0
    assert TextInjector._common_prefix_length("same", "same") == 4


def test_update_live_text_types_only_new_suffix():
    injector = TextInjector()
    typed = []
    backspaces = []

    injector._send_backspaces = lambda count: backspaces.append(count) or InjectionResult(True, "backspace")
    injector._inject_unicode = lambda text, key_delay_ms=0: typed.append((text, key_delay_ms)) or InjectionResult(True, "unicode")

    result = injector.update_live_text("hello wor", "hello world", key_delay_ms=3)

    assert result.success is True
    assert backspaces == []
    assert typed == [("ld", 3)]


def test_update_live_text_replaces_corrected_suffix():
    injector = TextInjector()
    typed = []
    backspaces = []

    injector._send_backspaces = lambda count: backspaces.append(count) or InjectionResult(True, "backspace")
    injector._inject_unicode = lambda text, key_delay_ms=0: typed.append((text, key_delay_ms)) or InjectionResult(True, "unicode")

    result = injector.update_live_text("hello wurld", "hello world")

    assert result.success is True
    assert backspaces == [4]
    assert typed == [("orld", 0)]


def test_clipboard_fallback_uses_native_paste_before_restore(monkeypatch):
    injector = TextInjector()
    calls = []
    clipboard = {"text": "previous"}

    monkeypatch.setattr("services.text_injector.pyperclip.paste", lambda: clipboard["text"])
    monkeypatch.setattr(
        "services.text_injector.pyperclip.copy",
        lambda text: calls.append(("copy", text)) or clipboard.__setitem__("text", text),
    )
    monkeypatch.setattr(
        injector,
        "_send_ctrl_v",
        lambda: calls.append(("paste", clipboard["text"])) or InjectionResult(True, "clipboard_paste"),
    )
    monkeypatch.setattr("services.text_injector.time.sleep", lambda _seconds: calls.append(("sleep", _seconds)))

    result = injector._inject_clipboard("next text", restore_clipboard=True)

    assert result.success is True
    assert calls == [
        ("copy", "next text"),
        ("paste", "next text"),
        ("sleep", 0.35),
        ("copy", "previous"),
    ]
