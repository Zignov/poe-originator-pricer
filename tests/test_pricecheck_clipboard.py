"""The clipboard gate and the change watcher.

The gate is the privacy boundary -- text that is not a copied item must not
travel past it -- and the watcher is what decides when to read at all. Both are
small, and both are the kind of small that is wrong in a way nobody notices:
a gate that lets a forum post through, or a watcher that fires on startup and
prices something the user copied an hour ago.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.pricecheck import clipboard
from src.pricecheck.clipboard import (
    MAX_ITEM_TEXT_CHARS,
    ClipboardWatcher,
    looks_like_item_text,
)

ITEM = Path("data/sample_map.txt")


# -- the gate ---------------------------------------------------------------
def test_a_real_copied_map_is_recognised():
    if not ITEM.exists():
        pytest.skip("no sample item text in this checkout")
    assert looks_like_item_text(ITEM.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "text",
    [
        "Item Class: Maps\nRarity: Rare\nTerror Solace",
        # The pre-3.x header, still what older pastes and most fixtures use.
        "Rarity: Normal\nChaos Orb",
        "  \n Item Class: Stackable Currency\nRarity: Currency",
    ],
)
def test_both_item_headers_are_accepted(text):
    assert looks_like_item_text(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "hunter2",
        "https://example.com/some/url",
        "SELECT * FROM listings;",
        # The word appears, but not as the header -- a copied forum post about
        # rarity is not an item, and treating it as one is the gate failing at
        # exactly the thing it is for.
        "Someone wrote:\nItem Class: Maps is the line you want",
        "the Rarity: Rare roll is what matters here",
    ],
)
def test_everything_else_is_refused(text):
    assert not looks_like_item_text(text)


def test_a_non_string_clipboard_is_refused():
    """`read_text` can hand back whatever ctypes found. It must not crash here."""
    assert not looks_like_item_text(42)
    assert not looks_like_item_text(b"Item Class: Maps")


def test_an_enormous_clipboard_is_not_even_matched():
    """The size check comes before the regex, and is the point of having it.

    A watcher fires on every copy, so someone copying a 5 MB document should
    not pay for a pattern match against it. The header is still there in this
    case, which is what makes it a real test: it is refused on size alone.
    """
    huge = "Item Class: Maps\n" + ("x" * (MAX_ITEM_TEXT_CHARS + 1))
    assert not looks_like_item_text(huge)
    assert looks_like_item_text(huge[: MAX_ITEM_TEXT_CHARS - 1])


def test_read_item_text_drops_what_the_gate_refuses(monkeypatch):
    """The composition that matters: a non-item clipboard yields None.

    This is the function the rest of the package calls, and it is the only
    place the gate is actually load-bearing -- `read_text` itself will happily
    return a password.
    """
    monkeypatch.setattr(clipboard, "read_text", lambda: "my bank password")
    assert clipboard.read_item_text() is None

    monkeypatch.setattr(clipboard, "read_text", lambda: "Rarity: Rare\nTerror Solace")
    assert clipboard.read_item_text() == "Rarity: Rare\nTerror Solace"


# -- availability -----------------------------------------------------------
def test_off_windows_the_clipboard_is_unavailable_with_a_reason(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    ok, why = clipboard.available()
    assert not ok
    assert "Windows" in why


def test_off_windows_reading_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert clipboard.read_text() is None
    assert clipboard.sequence_number() is None


# -- the watcher ------------------------------------------------------------
class _Counter:
    """Stands in for GetClipboardSequenceNumber.

    Yields each value in turn and then repeats the last one forever, which is
    how the real counter behaves: it holds still until something is copied.
    """

    def __init__(self, *values):
        self.values = list(values) or [None]
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _watch(monkeypatch, counter, text="Rarity: Rare\nTerror Solace"):
    monkeypatch.setattr(clipboard, "sequence_number", counter)
    monkeypatch.setattr(clipboard, "read_item_text", lambda: text)


def test_the_first_poll_primes_and_does_not_fire(monkeypatch):
    """Starting up must not price whatever was copied an hour ago.

    A popup on launch, for an item the user is no longer holding, is a price
    check nobody asked for -- and the one most likely to be mistaken for the
    tool pricing the wrong thing.
    """
    _watch(monkeypatch, _Counter(5))
    watcher = ClipboardWatcher(seen=None)
    assert watcher.poll() is None


def test_a_copy_fires_exactly_once(monkeypatch):
    _watch(monkeypatch, _Counter(6))
    watcher = ClipboardWatcher(seen=5)
    assert watcher.poll() == "Rarity: Rare\nTerror Solace"
    # The counter has not moved again, so nothing more happened.
    assert watcher.poll() is None
    assert watcher.poll() is None


def test_an_unchanged_clipboard_is_never_read(monkeypatch):
    """The polite half of the design: no change, no clipboard open at all."""
    reads = []
    monkeypatch.setattr(clipboard, "sequence_number", lambda: 7)
    monkeypatch.setattr(
        clipboard, "read_item_text", lambda: reads.append(1) or "Rarity: Rare"
    )
    watcher = ClipboardWatcher(seen=7)
    for _ in range(10):
        assert watcher.poll() is None
    assert reads == [], "the clipboard was opened although nothing changed"


def test_a_copy_that_is_not_an_item_yields_nothing(monkeypatch):
    """The counter moved, the gate refused. Both are correct and both happen."""
    _watch(monkeypatch, _Counter(2), text=None)
    watcher = ClipboardWatcher(seen=1)
    assert watcher.poll() is None


def test_a_machine_with_no_readable_counter_never_fires(monkeypatch):
    """Rather than firing on every poll, which would be the other way to fail."""
    monkeypatch.setattr(clipboard, "sequence_number", lambda: None)
    monkeypatch.setattr(clipboard, "read_item_text", lambda: "Rarity: Rare")
    watcher = ClipboardWatcher(seen=None)
    assert not watcher.primed
    for _ in range(5):
        assert watcher.poll() is None


def test_a_counter_that_starts_working_late_primes_then(monkeypatch):
    """Priming is deferred, not skipped.

    `ClipboardWatcher()` constructed before the first successful counter read
    must still prime on its first real one rather than treating that value as a
    change and firing.
    """
    counter = _Counter(None, 11, 11)
    monkeypatch.setattr(clipboard, "sequence_number", counter)
    monkeypatch.setattr(clipboard, "read_item_text", lambda: "Rarity: Rare")
    watcher = ClipboardWatcher()
    assert not watcher.primed
    assert watcher.poll() is None  # primes on 11
    assert watcher.primed
    assert watcher.poll() is None  # still 11


def test_forget_suppresses_the_item_the_hotkey_just_priced(monkeypatch):
    """Otherwise every hotkey press shows the same popup twice.

    The hotkey reads the clipboard directly; without `forget` the watcher then
    notices the same unchanged-but-unseen counter and fires for the item that
    was just shown.
    """
    monkeypatch.setattr(clipboard, "sequence_number", lambda: 42)
    monkeypatch.setattr(clipboard, "read_item_text", lambda: "Rarity: Rare")
    watcher = ClipboardWatcher(seen=1)
    watcher.forget()
    assert watcher.poll() is None


def test_forget_on_a_machine_with_no_counter_does_not_crash(monkeypatch):
    monkeypatch.setattr(clipboard, "sequence_number", lambda: None)
    watcher = ClipboardWatcher(seen=None)
    watcher.forget()  # must be a no-op, not an exception
    assert not watcher.primed


# -- the Win32 call ---------------------------------------------------------
def test_the_sequence_number_survives_a_missing_api(monkeypatch):
    """Returns None, so the caller degrades to hotkey-only rather than failing."""
    monkeypatch.setattr(sys, "platform", "win32")

    class _Dll:
        class shell32:  # unused here, present for shape
            pass

        class user32:
            @staticmethod
            def GetClipboardSequenceNumber():
                raise AttributeError("GetClipboardSequenceNumber")

    import ctypes

    monkeypatch.setattr(ctypes, "windll", _Dll, raising=False)
    assert clipboard.sequence_number() is None


def test_handle_returning_calls_declare_their_pointer_types():
    """A 64-bit handle truncated to int is the classic way to get this wrong.

    A source check, because the failure needs a real clipboard to reproduce and
    manifests as an address that sometimes locks and sometimes does not.
    """
    source = Path("src/pricecheck/clipboard.py").read_text(encoding="utf-8")
    assert "GetClipboardData.restype" in source
    assert "GlobalLock.restype" in source


def test_the_clipboard_is_always_closed():
    """`CloseClipboard` has to be in a finally, not on the success path.

    Leaving the clipboard open holds a global lock: every other program's copy
    and paste stops working until this process exits, and the user would have
    no way to connect that to a price checker.
    """
    source = Path("src/pricecheck/clipboard.py").read_text(encoding="utf-8")
    body = source[source.index("def read_text"):]
    close = body.index("CloseClipboard")
    finally_at = body.index("finally:")
    assert finally_at < close, "CloseClipboard is not in a finally block"
