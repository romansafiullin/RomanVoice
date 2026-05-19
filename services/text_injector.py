"""Windows text injection helpers.

The default path uses SendInput with KEYEVENTF_UNICODE so normal dictation does
not touch the clipboard. Clipboard paste remains a fallback for long text and
targets that do not accept synthesized Unicode key events.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass

import keyboard
import pyperclip

logger = logging.getLogger(__name__)


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_BACK = 0x08
VK_CONTROL = 0x11
VK_V = 0x56
WORD = ctypes.c_uint16
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", DWORD),
        ("wParamL", WORD),
        ("wParamH", WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("union", INPUT_UNION)]


@dataclass
class InjectionResult:
    success: bool
    method: str
    error: str | None = None


class TextInjector:
    """Inject text into the currently focused Windows control."""

    def inject(
        self,
        text: str,
        *,
        mode: str = "unicode",
        key_delay_ms: int = 0,
        long_text_threshold: int = 5000,
    ) -> InjectionResult:
        if not text:
            return InjectionResult(success=True, method="none")

        if mode == "clipboard" or len(text) > long_text_threshold:
            return self._inject_clipboard(text, restore_clipboard=True)

        unicode_result = self._inject_unicode(text, key_delay_ms=key_delay_ms)
        if unicode_result.success:
            return unicode_result

        logger.warning("Unicode injection failed; trying clipboard fallback: %s", unicode_result.error)
        fallback = self._inject_clipboard(text, restore_clipboard=True)
        if fallback.success:
            fallback.method = "clipboard_fallback"
        return fallback

    def _inject_unicode(self, text: str, *, key_delay_ms: int = 0) -> InjectionResult:
        try:
            send_input = ctypes.WinDLL("user32", use_last_error=True).SendInput
            send_input.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
            send_input.restype = ctypes.c_uint
        except Exception as exc:
            return InjectionResult(False, "unicode", f"SendInput unavailable: {exc}")

        delay_seconds = max(0, min(key_delay_ms, 5)) / 1000.0
        code_units = self._utf16_code_units(text)

        for unit in code_units:
            inputs = (INPUT * 2)(
                self._keyboard_input(unit, KEYEVENTF_UNICODE),
                self._keyboard_input(unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            )
            sent = send_input(2, inputs, ctypes.sizeof(INPUT))
            if sent != 2:
                error = ctypes.get_last_error()
                return InjectionResult(False, "unicode", f"SendInput sent {sent}/2 events; error={error}")
            if delay_seconds:
                time.sleep(delay_seconds)

        return InjectionResult(True, "unicode")

    def update_live_text(
        self,
        previous_text: str,
        next_text: str,
        *,
        key_delay_ms: int = 0,
    ) -> InjectionResult:
        """Reconcile previously typed streaming text with a newer transcript."""
        previous_text = previous_text or ""
        next_text = next_text or ""
        if previous_text == next_text:
            return InjectionResult(True, "live_none")

        prefix_len = self._common_prefix_length(previous_text, next_text)
        backspace_count = len(previous_text) - prefix_len
        suffix = next_text[prefix_len:]

        if backspace_count:
            result = self._send_backspaces(backspace_count)
            if not result.success:
                return result

        if suffix:
            result = self._inject_unicode(suffix, key_delay_ms=key_delay_ms)
            if not result.success:
                return result

        return InjectionResult(True, "live_unicode")

    def _send_backspaces(self, count: int) -> InjectionResult:
        try:
            send_input = ctypes.WinDLL("user32", use_last_error=True).SendInput
            send_input.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
            send_input.restype = ctypes.c_uint
        except Exception as exc:
            return InjectionResult(False, "live_backspace", f"SendInput unavailable: {exc}")

        for _ in range(max(0, count)):
            inputs = (INPUT * 2)(
                self._keyboard_input(VK_BACK, 0, use_vk=True),
                self._keyboard_input(VK_BACK, KEYEVENTF_KEYUP, use_vk=True),
            )
            sent = send_input(2, inputs, ctypes.sizeof(INPUT))
            if sent != 2:
                error = ctypes.get_last_error()
                return InjectionResult(False, "live_backspace", f"SendInput sent {sent}/2 events; error={error}")
        return InjectionResult(True, "live_backspace")

    def _inject_clipboard(self, text: str, *, restore_clipboard: bool) -> InjectionResult:
        previous_text = None
        had_previous = False

        try:
            if restore_clipboard:
                try:
                    previous_text = pyperclip.paste()
                    had_previous = True
                except Exception as exc:
                    logger.debug("Could not read existing clipboard: %s", exc)

            pyperclip.copy(text)
            paste_result = self._send_ctrl_v()
            if not paste_result.success:
                logger.debug("Native Ctrl+V failed; falling back to keyboard module: %s", paste_result.error)
                keyboard.send("ctrl+v")

            if restore_clipboard and had_previous:
                # Give the focused app a moment to consume the paste before restoring.
                time.sleep(0.35)
                pyperclip.copy(previous_text)

            return InjectionResult(True, "clipboard")
        except Exception as exc:
            return InjectionResult(False, "clipboard", str(exc))

    def _send_ctrl_v(self) -> InjectionResult:
        try:
            send_input = ctypes.WinDLL("user32", use_last_error=True).SendInput
            send_input.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
            send_input.restype = ctypes.c_uint
        except Exception as exc:
            return InjectionResult(False, "clipboard_paste", f"SendInput unavailable: {exc}")

        inputs = (INPUT * 4)(
            self._keyboard_input(VK_CONTROL, 0, use_vk=True),
            self._keyboard_input(VK_V, 0, use_vk=True),
            self._keyboard_input(VK_V, KEYEVENTF_KEYUP, use_vk=True),
            self._keyboard_input(VK_CONTROL, KEYEVENTF_KEYUP, use_vk=True),
        )
        sent = send_input(4, inputs, ctypes.sizeof(INPUT))
        if sent != 4:
            error = ctypes.get_last_error()
            return InjectionResult(False, "clipboard_paste", f"SendInput sent {sent}/4 events; error={error}")
        return InjectionResult(True, "clipboard_paste")

    @staticmethod
    def _keyboard_input(code_unit: int, flags: int, *, use_vk: bool = False) -> INPUT:
        return INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=code_unit if use_vk else 0,
                    wScan=0 if use_vk else code_unit,
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )

    @staticmethod
    def _utf16_code_units(text: str) -> list[int]:
        encoded = text.encode("utf-16-le")
        return [
            int.from_bytes(encoded[index:index + 2], "little")
            for index in range(0, len(encoded), 2)
        ]

    @staticmethod
    def _common_prefix_length(left: str, right: str) -> int:
        limit = min(len(left), len(right))
        index = 0
        while index < limit and left[index] == right[index]:
            index += 1
        return index


text_injector = TextInjector()
