"""System-wide hotkeys, and why the overlay window cannot listen for keys itself.

The overlay is click-through: `WS_EX_TRANSPARENT` means the mouse passes to
whatever is underneath, and a window nobody can click is a window that never
takes focus. A `bind("<Key>")` on it would therefore never fire while the game
is in front, which is the only time it matters.

Windows' answer is `RegisterHotKey`, which is not a keylogger: it reserves one
specific chord with the OS and receives a `WM_HOTKEY` message when it is
pressed. Nothing else about the keyboard is visible to this process, no input is
generated, and nothing is sent to any other window.

There is one trap. `RegisterHotKey(NULL, ...)` posts `WM_HOTKEY` to the
*thread's* message queue, and Tk's own event loop pumps that queue with
`PeekMessage(NULL, ...)` and then throws away anything that has no window to
dispatch to -- so a hotkey registered on the Tk thread arrives and vanishes.
The fix is a dedicated thread that registers the hotkeys and runs its own
`GetMessage` loop; nothing else pumps there, so nothing else can swallow them.
The thread hands names to a queue, and the Tk side drains it from its `after`
loop.

On anything other than Windows this degrades to "unavailable", and the control
strip window remains the way to drive the overlay.
"""
from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Mapping

# Modifier bits, from WinUser.h.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
#: Without this, holding the chord repeats it at the keyboard's repeat rate,
#: which would flicker a show/hide toggle rather than fire it once.
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

_MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "cmd": MOD_WIN,
}

#: Virtual-key codes for the non-alphanumeric keys worth binding. Letters and
#: digits are their ASCII uppercase value, which is the whole rule for them.
_KEY_NAMES = {
    "space": 0x20,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "`": 0xC0,
}


class HotkeyError(ValueError):
    """The configured chord cannot be expressed as a Windows hotkey."""


def parse_hotkey(spec: str) -> tuple[int, int]:
    """"ctrl+alt+o" -> (modifier bits, virtual-key code).

    Kept as a pure function, separate from registration, because the thing that
    actually goes wrong here is a typo in config.yaml -- and that has to be
    reportable to the user before any window exists to report it in.
    """
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        raise HotkeyError("empty hotkey")

    modifiers = 0
    key: str | None = None
    for part in parts:
        if part in _MODIFIER_NAMES:
            modifiers |= _MODIFIER_NAMES[part]
            continue
        if key is not None:
            raise HotkeyError(f"{spec!r} names two keys ({key!r} and {part!r})")
        key = part

    if key is None:
        raise HotkeyError(f"{spec!r} is only modifiers; it needs a key as well")
    return (modifiers, _virtual_key(key, spec))


def _virtual_key(key: str, spec: str) -> int:
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return ord(key.upper())
    if key in _KEY_NAMES:
        return _KEY_NAMES[key]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    raise HotkeyError(f"unknown key {key!r} in {spec!r}")


def available() -> tuple[bool, str]:
    """Whether system-wide hotkeys can work at all here."""
    if sys.platform != "win32":
        return False, "system-wide hotkeys are Windows-only; use the control strip"
    return True, ""


class HotkeyListener:
    """Registers chords on its own thread and reports them by name.

    Failure is never fatal. A chord another application already owns -- and
    Ctrl+Alt+O is the sort of thing a screenshot tool takes -- fails
    registration with ERROR_HOTKEY_ALREADY_REGISTERED, and the right response is
    to say which one and carry on with the rest, not to refuse to start the
    overlay.
    """

    def __init__(self, bindings: Mapping[str, str]):
        self._bindings = dict(bindings)
        self._events: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop = threading.Event()
        self.errors: list[str] = []
        self.registered: list[str] = []

    def start(self) -> bool:
        ok, why = available()
        if not ok:
            self.errors.append(why)
            return False
        for name, spec in self._bindings.items():
            try:
                parse_hotkey(spec)
            except HotkeyError as exc:
                self.errors.append(f"{name}: {exc}")
        self._thread = threading.Thread(target=self._run, name="overlay-hotkeys", daemon=True)
        self._thread.start()
        return True

    def poll(self) -> list[str]:
        """Names of every chord pressed since the last call."""
        out: list[str] = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def stop(self) -> None:
        self._stop.set()
        if self._thread_id is not None:  # pragma: no cover - needs a live thread
            import ctypes

            # WM_QUIT (0x0012) into the listener's own queue: the only way to
            # break a blocking GetMessage from outside the thread.
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)

    # -- the listener thread --------------------------------------------
    def _run(self) -> None:  # pragma: no cover - needs a Windows message loop
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        ids: dict[int, str] = {}
        for index, (name, spec) in enumerate(self._bindings.items(), start=1):
            try:
                modifiers, vk = parse_hotkey(spec)
            except HotkeyError:
                continue  # already reported by start()
            if user32.RegisterHotKey(None, index, modifiers | MOD_NOREPEAT, vk):
                ids[index] = name
                self.registered.append(f"{name}: {spec}")
            else:
                self.errors.append(
                    f"{name}: {spec} is already taken by another application"
                )

        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    name = ids.get(int(msg.wParam))
                    if name:
                        self._events.put(name)
        finally:
            for index in ids:
                user32.UnregisterHotKey(None, index)
