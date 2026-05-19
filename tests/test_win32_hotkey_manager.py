"""Tests for Win32 hotkey parsing."""
from services.win32_hotkey_manager import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    parse_hotkey,
)


def test_parse_ctrl_alt_space():
    spec = parse_hotkey("ctrl+alt+space")

    assert spec.modifiers == (MOD_CONTROL | MOD_ALT)
    assert spec.vk == 0x20


def test_parse_numpad_multiply():
    spec = parse_hotkey("kp *")

    assert spec.modifiers == 0
    assert spec.vk == 0x6A


def test_parse_win_shift_s():
    spec = parse_hotkey("win+shift+s")

    assert spec.modifiers == (MOD_WIN | MOD_SHIFT)
    assert spec.vk == ord("S")


def test_parse_f_key():
    spec = parse_hotkey("ctrl+f10")

    assert spec.modifiers == MOD_CONTROL
    assert spec.vk == 0x79
