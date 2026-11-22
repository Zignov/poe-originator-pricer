"""Fullscreen-exclusive detection: the overlay's worst failure, made visible.

A layered window cannot draw over a game holding the display in
fullscreen-exclusive mode. The overlay still starts, still reads the file,
still places its tiles -- and none of it is on screen. There is nothing to
look at and therefore nothing to debug, which makes it indistinguishable from
a crash.

The README has warned about this in three places for as long as the overlay
has existed. Warning in the documentation is not the same as saying so at the
moment it happens, which is what these pin.
"""
from __future__ import annotations

import sys

import pytest

from src.overlay.window import (
    FULLSCREEN_WARNING,
    QUNS_RUNNING_D3D_FULL_SCREEN,
    fullscreen_exclusive_active,
)


class _Shell:
    """Stands in for shell32, filling the out-parameter the way Windows does."""

    def __init__(self, state: int, result: int = 0):
        self.state = state
        self.result = result
        self.calls = 0

    def SHQueryUserNotificationState(self, pointer):
        self.calls += 1
        pointer._obj.value = self.state
        return self.result


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def _with_shell(monkeypatch, shell):
    import ctypes

    class _Dll:
        shell32 = shell

    monkeypatch.setattr(ctypes, "windll", _Dll, raising=False)


def test_a_game_in_exclusive_fullscreen_is_detected(on_windows, monkeypatch):
    _with_shell(monkeypatch, _Shell(QUNS_RUNNING_D3D_FULL_SCREEN))
    assert fullscreen_exclusive_active() is True


@pytest.mark.parametrize("state", [1, 2, 4, 5, 6, 7])
def test_every_other_notification_state_is_not_fullscreen(on_windows, monkeypatch, state):
    """Busy, presenting, quiet hours and the rest are all fine for an overlay."""
    _with_shell(monkeypatch, _Shell(state))
    assert fullscreen_exclusive_active() is False


def test_a_failed_call_says_it_does_not_know_rather_than_fine(on_windows, monkeypatch):
    """None and False are different claims, and conflating them is the bug.

    Returning False on failure would mean a silent overlay on a machine where
    the check simply did not work -- the exact failure this exists to remove.
    """
    _with_shell(monkeypatch, _Shell(QUNS_RUNNING_D3D_FULL_SCREEN, result=-2147467259))
    assert fullscreen_exclusive_active() is None


def test_a_missing_api_is_not_an_exception(on_windows, monkeypatch):
    class _Missing:
        def __getattr__(self, name):
            raise AttributeError(name)

    _with_shell(monkeypatch, _Missing())
    assert fullscreen_exclusive_active() is None


def test_off_windows_the_question_is_unanswerable(monkeypatch):
    """The overlay is Windows-only; the check must not claim otherwise elsewhere."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert fullscreen_exclusive_active() is None


def test_the_warning_names_the_fix():
    """A warning that does not say what to do is only half of one."""
    assert "windowed fullscreen" in FULLSCREEN_WARNING.lower()
    assert "overlay" in FULLSCREEN_WARNING.lower()


def test_both_surfaces_use_the_same_wording():
    """The overlay's own strip and the web page must not drift apart.

    A source check rather than a behavioural one: the failure mode is a second
    copy of the sentence appearing, and a copy is correct right up until one of
    them is edited.
    """
    from pathlib import Path

    page = Path("src/ui/stash_pricer.py").read_text(encoding="utf-8")
    window = Path("src/overlay/window.py").read_text(encoding="utf-8")
    assert "FULLSCREEN_WARNING" in page, "the page should reuse the constant"
    # Defined once, in the overlay module, and referenced from the page.
    assert window.count("FULLSCREEN_WARNING = (") == 1
    assert "switch the game to windowed fullscreen" not in page, (
        "the page has its own copy of the sentence instead of the constant"
    )
