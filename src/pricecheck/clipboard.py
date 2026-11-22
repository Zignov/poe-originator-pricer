"""Reading the clipboard, and the line this package will not cross.

Every price-check tool for this game works the same way from the user's side:
hover an item, press a key, see a price. There are two ways to implement that,
and the difference between them is the difference between a tool that is
allowed and one that is not.

**The way this does not work.** The common implementation binds a single
hotkey, and when it fires it *synthesises a Ctrl+C into the game window* --
``SendInput``, ``keybd_event``, or a ``WM_KEYDOWN`` posted at the client -- then
reads what landed on the clipboard. One keypress instead of two. It is also,
plainly, a program sending input to the game client on the user's behalf, which
is the thing every automation rule in every ARPG is written about. GGG's
position has been consistent for years: a program may read what the game gives
it, and may not act for the player. A tool that injects keystrokes has to argue
about which side of that line a single synthetic Ctrl+C falls on. This one does
not have to argue.

**The way this works.** The user presses Ctrl+C themselves. That is Path of
Exile's own copy-item binding, a feature the game ships for exactly this
purpose, and pressing it is the player playing. This package then reads the
clipboard -- a shared operating-system facility that the game deliberately
wrote to -- and prices what it finds. Two keypresses rather than one. That is
the entire cost, and it buys a tool with nothing to explain.

So: there is no ``SendInput``, no ``keybd_event``, no ``PostMessage``, no
``SendKeys`` and no ``pyautogui`` anywhere in this package, there never will be,
and ``tests/test_pricecheck_compliance.py`` fails the build if one appears.

Two smaller decisions, both about being a good citizen of the desktop:

- **Change detection does not open the clipboard.**
  ``GetClipboardSequenceNumber`` is a counter Windows bumps on every write, and
  reading it needs no lock. Polling *that* a few times a second costs nothing
  and cannot make another program's copy or paste fail; polling the clipboard
  itself, by opening it, would hold a global lock on a shared resource several
  times a second for as long as the app is running.
- **Nothing that is not item text gets past this module.**
  ``looks_like_item_text`` is a gate, not a convenience: a clipboard watcher
  sees passwords, addresses and private messages, and the correct amount of
  those to carry into the rest of the program is none. Text that does not have
  Path of Exile's own item header on it is dropped here, before any other
  module, the network, the log or the disk can see it. Nothing in this package
  ever writes the clipboard's contents to a file or to a terminal.
"""
from __future__ import annotations

import re
import sys
import time

#: The standard clipboard format for Unicode text, from WinUser.h. The game
#: writes item text as plain text, so this is the only format worth asking for.
CF_UNICODETEXT = 13

#: ``OpenClipboard`` fails outright if another process currently holds the
#: lock, which happens routinely and briefly -- the game itself has it for an
#: instant while writing. Retrying a handful of times over ~100 ms turns the
#: normal case of "someone else is mid-copy" into a success instead of a
#: dropped check.
OPEN_ATTEMPTS = 6
OPEN_RETRY_SECONDS = 0.02

#: Path of Exile begins every copied item with one of these. ``Item Class:`` is
#: what current clients emit; ``Rarity:`` was the first line for years before
#: that and is still what older pastes and most of this project's fixtures look
#: like, so both are accepted.
#:
#: This pair is the privacy gate described in the module docstring. It is
#: deliberately a header match anchored to the start of the text rather than a
#: search for map-ish words anywhere in it: "Rarity" appearing in the middle of
#: a copied forum post should not make that post something this program reads.
_ITEM_HEADER = re.compile(r"\A\s*(Item Class:|Rarity:)\s*\S")

#: An upper bound on what is plausibly one copied item, applied before the
#: regex. A copied item is a few hundred characters; a stash export is tens of
#: thousands and a copied document can be megabytes. Refusing to even pattern
#: match a 200 KB clipboard keeps a watcher that fires on every copy from
#: spending real time on things that were never items.
MAX_ITEM_TEXT_CHARS = 20_000


def available() -> tuple[bool, str]:
    """Whether the clipboard can be read here at all."""
    if sys.platform != "win32":
        return False, "clipboard price checks are Windows-only"
    return True, ""


def looks_like_item_text(text: str | None) -> bool:
    """Does this look like something Path of Exile put on the clipboard?

    The gate from the module docstring. Returns False for anything else,
    including the empty clipboard, and says nothing about *what* the other
    thing was.
    """
    if not text or not isinstance(text, str):
        return False
    if len(text) > MAX_ITEM_TEXT_CHARS:
        return False
    return _ITEM_HEADER.search(text) is not None


def sequence_number() -> int | None:
    """Windows' clipboard change counter, or None if it cannot be read.

    Bumped on every write by any process. Comparing it to the last value seen
    is how the watcher notices a copy without opening the clipboard; it is
    monotonic within a session but says nothing about *what* changed, which is
    the point.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except (OSError, AttributeError, ValueError):
        return None


def read_text() -> str | None:
    """The clipboard's text, or None if there is none to be had.

    Opens the clipboard, copies the string out, and closes it in the same
    breath -- the lock is global, and holding it blocks every other program's
    copy and paste.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - ctypes ships with CPython
        return None

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Both of these return pointers. Without an explicit restype ctypes assumes
    # `int`, which silently truncates a 64-bit handle to 32 bits and hands back
    # an address that either fails to lock or -- far worse -- is a different
    # valid one. This is the single easiest way to get this function wrong.
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]

    if not _open_clipboard(user32):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    except (OSError, ValueError):
        return None
    finally:
        user32.CloseClipboard()


def read_item_text() -> str | None:
    """The clipboard's text if it is an item, and None for anything else.

    The only reader the rest of this package calls. Everything that is not item
    text stops here -- see the module docstring.
    """
    text = read_text()
    return text if looks_like_item_text(text) else None


def _open_clipboard(user32) -> bool:
    """``OpenClipboard``, retried briefly while another process holds the lock."""
    for attempt in range(OPEN_ATTEMPTS):
        if user32.OpenClipboard(None):
            return True
        if attempt + 1 < OPEN_ATTEMPTS:
            time.sleep(OPEN_RETRY_SECONDS)
    return False


class ClipboardWatcher:
    """Notices that the clipboard changed, and reports item text when it does.

    Deliberately not a thread. The sequence-number check is a single cheap
    syscall, so the Tk side can drive it from its own ``after`` loop -- the same
    arrangement the overlay uses for its state file -- and that keeps every
    clipboard read on one thread, where the ordering is obvious.

    The first poll after construction primes the counter rather than firing.
    Starting up and immediately pricing whatever happened to be on the
    clipboard from an hour ago would be a price check the user did not ask for,
    attached to an item they are no longer holding.
    """

    def __init__(self, seen: int | None = None):
        self._seen = seen if seen is not None else sequence_number()
        #: True once the counter is known, so a machine where it cannot be read
        #: is told apart from one where nothing has been copied yet.
        self.primed = self._seen is not None

    def poll(self) -> str | None:
        """Item text, if the clipboard changed into some since the last call."""
        current = sequence_number()
        if current is None:
            return None
        if not self.primed:
            self._seen = current
            self.primed = True
            return None
        if current == self._seen:
            return None
        self._seen = current
        return read_item_text()

    def forget(self) -> None:
        """Treat whatever is on the clipboard now as already seen.

        Called after an explicit hotkey check, so the watcher does not
        immediately show the same item a second time.
        """
        current = sequence_number()
        if current is not None:
            self._seen = current
            self.primed = True
