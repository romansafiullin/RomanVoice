"""Windows global hotkey manager backed by RegisterHotKey.

This avoids low-level keyboard hooks and does not suppress unrelated shortcuts.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from config import config

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


@dataclass(frozen=True)
class HotkeySpec:
    """Parsed Win32 hotkey values."""

    modifiers: int
    vk: int


_MAIN_KEY_MAP = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "page up": 0x21,
    "pageup": 0x21,
    "page down": 0x22,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "kp *": 0x6A,
    "kp multiply": 0x6A,
    "kp +": 0x6B,
    "kp plus": 0x6B,
    "kp -": 0x6D,
    "kp minus": 0x6D,
    "kp .": 0x6E,
    "kp decimal": 0x6E,
    "kp /": 0x6F,
    "kp divide": 0x6F,
}


def parse_hotkey(hotkey: str) -> HotkeySpec:
    """Parse a saved hotkey string into Win32 modifier and virtual-key values."""
    if not hotkey or not hotkey.strip():
        raise ValueError("hotkey is empty")

    raw_parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not raw_parts:
        raise ValueError("hotkey is empty")

    modifiers = 0
    main_parts = []
    for part in raw_parts:
        if part in {"ctrl", "control"}:
            modifiers |= MOD_CONTROL
        elif part == "alt":
            modifiers |= MOD_ALT
        elif part == "shift":
            modifiers |= MOD_SHIFT
        elif part in {"win", "windows", "cmd", "command"}:
            modifiers |= MOD_WIN
        else:
            main_parts.append(part)

    if not main_parts:
        raise ValueError(f"missing main key in hotkey: {hotkey}")

    main_key = "+".join(main_parts)
    if main_key.startswith("kp "):
        suffix = main_key[3:].strip()
        if suffix.isdigit() and len(suffix) == 1:
            return HotkeySpec(modifiers, 0x60 + int(suffix))

    if main_key in _MAIN_KEY_MAP:
        return HotkeySpec(modifiers, _MAIN_KEY_MAP[main_key])

    if len(main_key) == 1 and main_key.isalpha():
        return HotkeySpec(modifiers, ord(main_key.upper()))

    if len(main_key) == 1 and main_key.isdigit():
        return HotkeySpec(modifiers, ord(main_key))

    if main_key.startswith("f") and main_key[1:].isdigit():
        number = int(main_key[1:])
        if 1 <= number <= 24:
            return HotkeySpec(modifiers, 0x70 + number - 1)

    raise ValueError(f"unsupported hotkey: {hotkey}")


class Win32HotkeyManager:
    """RegisterHotKey-backed manager for Windows global shortcuts."""

    _HOTKEY_IDS = {
        "record_toggle": 1,
        "cancel": 2,
        "enable_disable": 3,
    }

    def __init__(self, hotkeys: Optional[Dict[str, str]] = None):
        self.hotkeys = hotkeys or config.DEFAULT_HOTKEYS.copy()
        self.program_enabled = True
        self._last_trigger_time: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._registered_ids: list[int] = []

        self.on_record_toggle: Optional[Callable] = None
        self.on_cancel: Optional[Callable] = None
        self.on_enable_toggle: Optional[Callable] = None
        self.on_status_update: Optional[Callable] = None
        self.on_status_update_auto_hide: Optional[Callable] = None
        self.is_transcribing_fn: Optional[Callable[[], bool]] = None

        self._setup_keyboard_hook()

    def _setup_keyboard_hook(self) -> None:
        """Start the RegisterHotKey message loop."""
        self._stop_requested.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=2.0):
            logger.error("Win32 hotkey thread did not initialize")

    def _message_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = kernel32.GetCurrentThreadId()

        for name, hotkey_id in self._HOTKEY_IDS.items():
            hotkey = self.hotkeys.get(name)
            if not hotkey:
                continue
            try:
                spec = parse_hotkey(hotkey)
            except ValueError as exc:
                logger.warning("Skipping unsupported hotkey %s=%r: %s", name, hotkey, exc)
                continue

            if user32.RegisterHotKey(None, hotkey_id, spec.modifiers, spec.vk):
                self._registered_ids.append(hotkey_id)
                logger.info("Registered Win32 hotkey %s=%s", name, hotkey)
            else:
                error = ctypes.get_last_error()
                logger.error("Failed to register hotkey %s=%s (WinError %s)", name, hotkey, error)

        self._ready.set()

        msg = ctypes.wintypes.MSG()
        while not self._stop_requested.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0 or msg.message == WM_QUIT:
                break
            if result == -1:
                logger.error("Win32 hotkey message loop failed")
                break
            if msg.message == WM_HOTKEY:
                self._handle_hotkey_id(int(msg.wParam))

        for hotkey_id in list(self._registered_ids):
            user32.UnregisterHotKey(None, hotkey_id)
        self._registered_ids.clear()

    def _handle_hotkey_id(self, hotkey_id: int) -> None:
        if hotkey_id == self._HOTKEY_IDS["enable_disable"]:
            self._toggle_program_enabled()
            return

        if not self.program_enabled:
            return

        if hotkey_id == self._HOTKEY_IDS["record_toggle"]:
            if self._should_trigger_record_toggle() and self.on_record_toggle:
                threading.Thread(target=self.on_record_toggle, daemon=True).start()
        elif hotkey_id == self._HOTKEY_IDS["cancel"]:
            if self.on_cancel:
                threading.Thread(target=self.on_cancel, daemon=True).start()

    def _toggle_program_enabled(self) -> None:
        self.program_enabled = not self.program_enabled
        self._last_trigger_time = None

        status = "STT Enabled" if self.program_enabled else "STT Disabled"
        if self.on_status_update_auto_hide:
            self.on_status_update_auto_hide(status)
        elif self.on_status_update:
            self.on_status_update(status)

    def _should_trigger_record_toggle(self) -> bool:
        current_time = time.monotonic()
        if self._last_trigger_time is None:
            self._last_trigger_time = current_time
            return True

        if current_time - self._last_trigger_time > (config.HOTKEY_DEBOUNCE_MS / 1000.0):
            self._last_trigger_time = current_time
            return True
        return False

    def rehook(self) -> None:
        """Re-register all Win32 hotkeys."""
        logger.info("Re-registering Win32 hotkeys...")
        self.cleanup()
        self._setup_keyboard_hook()

    def update_hotkeys(self, new_hotkeys: Dict[str, str]) -> None:
        self.hotkeys.update(new_hotkeys)
        self.rehook()
        logger.info("Win32 hotkeys updated successfully")

    def cleanup(self) -> None:
        self._stop_requested.set()
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = None

    def set_callbacks(
        self,
        on_record_toggle: Callable = None,
        on_cancel: Callable = None,
        on_enable_toggle: Callable = None,
        on_status_update: Callable = None,
        on_status_update_auto_hide: Callable = None,
        is_transcribing_fn: Callable[[], bool] = None,
    ) -> None:
        self.on_record_toggle = on_record_toggle
        self.on_cancel = on_cancel
        self.on_enable_toggle = on_enable_toggle
        self.on_status_update = on_status_update
        self.on_status_update_auto_hide = on_status_update_auto_hide
        self.is_transcribing_fn = is_transcribing_fn
