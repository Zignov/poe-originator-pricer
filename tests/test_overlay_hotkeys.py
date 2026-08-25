"""Parsing the configured chord.

Only the parsing is tested. Registration needs a Windows message loop and a
keyboard, and a test that presses Ctrl+Alt+O on the machine running the suite
would be a worse idea than an untested branch. What *is* testable is the thing
that actually goes wrong: a typo in config.yaml, which has to be reported before
a window exists to report it in.
"""
from __future__ import annotations

import pytest

from src.overlay.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    HotkeyError,
    parse_hotkey,
)


def test_the_default_chord_parses():
    assert parse_hotkey("ctrl+alt+o") == (MOD_CONTROL | MOD_ALT, ord("O"))


def test_modifiers_may_be_spelled_either_way():
    assert parse_hotkey("control+shift+p") == parse_hotkey("ctrl+shift+p")
    assert parse_hotkey("win+o")[0] == MOD_WIN


def test_case_and_whitespace_do_not_matter():
    assert parse_hotkey(" Ctrl + Alt + O ") == parse_hotkey("ctrl+alt+o")


def test_a_digit_is_a_key_like_any_other():
    assert parse_hotkey("alt+1") == (MOD_ALT, ord("1"))


def test_a_function_key_maps_onto_its_virtual_key_code():
    assert parse_hotkey("f9") == (0, 0x78)


def test_a_named_key_maps_onto_its_virtual_key_code():
    assert parse_hotkey("shift+space") == (MOD_SHIFT, 0x20)


def test_modifiers_alone_are_refused():
    with pytest.raises(HotkeyError, match="needs a key"):
        parse_hotkey("ctrl+alt")


def test_two_keys_are_refused_rather_than_one_being_dropped():
    with pytest.raises(HotkeyError, match="two keys"):
        parse_hotkey("ctrl+o+p")


def test_an_unknown_key_names_itself_in_the_error():
    with pytest.raises(HotkeyError, match="banana"):
        parse_hotkey("ctrl+banana")


def test_an_empty_setting_is_refused():
    with pytest.raises(HotkeyError):
        parse_hotkey("")


def test_a_hotkey_out_of_the_function_key_range_is_not_silently_accepted():
    with pytest.raises(HotkeyError):
        parse_hotkey("f99")
