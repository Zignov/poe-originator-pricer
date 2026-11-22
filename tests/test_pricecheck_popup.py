"""The window flags that decide whether a price check interrupts the game.

Not about drawing -- that needs a display and was checked by hand. These pin
the two properties that are invisible when they work and catastrophic when they
do not: the popup must not take focus, and it must not be click-through.
"""
from __future__ import annotations

import sys
from pathlib import Path

from conftest import code_only
from src.overlay.window import (
    WS_EX_NOACTIVATE,
    WS_EX_TRANSPARENT,
    set_no_activate,
)

POPUP = Path("src/pricecheck/popup.py")


def test_the_no_activate_bit_is_the_documented_one():
    """0x08000000, from WinUser.h. A wrong constant would set some other flag
    and the popup would still steal focus -- silently, and only on a machine
    with a game running."""
    assert WS_EX_NOACTIVATE == 0x08000000


def test_topmost_and_no_activate_are_different_bits():
    """The reason this flag is needed at all.

    `-topmost` is z-order; it does not stop a window becoming the foreground
    window. Conflating them is how an overlay ends up minimising the game it is
    drawn over.
    """
    assert WS_EX_NOACTIVATE != WS_EX_TRANSPARENT


def test_setting_it_off_windows_is_a_no_op(monkeypatch):
    """The checker imports cleanly on any platform; only the flag is Windows."""
    monkeypatch.setattr(sys, "platform", "linux")

    class _Window:
        def update_idletasks(self):
            raise AssertionError("it should have returned before touching the window")

    set_no_activate(_Window())  # must not raise


def test_the_popup_re_asserts_the_flag_on_every_show():
    """Setting it once in __init__ would not survive.

    The ex-style has to be written while the window is mapped, and the popup is
    withdrawn between checks -- so a flag set at construction is discarded when
    Tk maps the window again. The failure is a popup that takes focus
    intermittently, which is the worst version of this bug to debug.
    """
    source = POPUP.read_text(encoding="utf-8")
    show = source[source.index("def show"):source.index("def hide")]
    assert "set_no_activate" in show, "the flag is not re-applied when showing"


def test_the_popup_is_never_made_click_through():
    """The overlay's tiles are click-through because they sit over stash cells
    the user still has to click. This sits beside the pointer and is dismissed
    by clicking it, so the same flag would make it impossible to close."""
    code = code_only(POPUP)
    assert "set_click_through" not in code
    assert "WS_EX_TRANSPARENT" not in code


def test_the_popup_never_forces_focus():
    """`focus_force` and `grab_set` both take input away from the game.

    A source check because the consequence -- a fullscreen-windowed client
    minimising on every price check -- cannot be reproduced without a game.
    Read past the comments, which mention `focus_force` in order to say that it
    is deliberately not called.
    """
    code = code_only(POPUP)
    for api in ("focus_force", "focus_set", "grab_set", "takefocus"):
        assert api not in code, f"the popup calls {api}"


def test_a_problem_card_stays_up_longer_than_a_price():
    """A price is a glance; a problem needs reading and an action."""
    from src.pricecheck.popup import HIDE_AFTER_MS, PROBLEM_HIDE_AFTER_MS

    assert PROBLEM_HIDE_AFTER_MS > HIDE_AFTER_MS


def test_the_popup_width_is_fixed():
    """A popup that changed width per item would move the price under the
    pointer between checks."""
    from src.pricecheck.popup import POPUP_WIDTH

    assert isinstance(POPUP_WIDTH, int) and POPUP_WIDTH > 0
    source = POPUP.read_text(encoding="utf-8")
    assert "POPUP_WIDTH}x{height}" in source, "the width is not the fixed constant"
