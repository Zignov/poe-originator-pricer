"""The guard on the one thing that could get this account banned.

`src/pricecheck/clipboard.py` sets out the argument in full: a price checker
can either read what the game puts on the clipboard, or it can synthesise a
Ctrl+C into the game window and read what it caused. The first is a program
reading; the second is a program acting for the player, and it is what every
automation rule is written about.

That decision is worth exactly as much as whatever enforces it. A docstring
does not, and the specific way this feature would rot is obvious and cheap: a
future change wires a single `keybd_event` in to save the user a keypress,
every test still passes, and the tool quietly becomes the kind that has
something to explain.

So these are source checks rather than behavioural ones, deliberately. The
failure mode is an API *appearing*, and there is no input one can give a
program to prove it never calls `SendInput`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import code_only

PACKAGE = Path("src/pricecheck")

#: Every Windows and third-party route to generating input, plus the two
#: library names that wrap them. `PostMessage`/`SendMessage` are included
#: because a posted WM_KEYDOWN is a synthesised keypress whatever it is called,
#: and `SetForegroundWindow` because a checker that steals focus from the game
#: is a different -- and also unacceptable -- way to act on the client.
FORBIDDEN = (
    "SendInput",
    "keybd_event",
    "mouse_event",
    "PostMessage",
    "SendMessage",
    "SetForegroundWindow",
    "SetCursorPos",
    "pyautogui",
    "pydirectinput",
    "SendKeys",
    "WM_KEYDOWN",
    "WM_KEYUP",
    "WM_CHAR",
)

#: Ways of reaching into the game's process rather than its clipboard. A price
#: checker has no business with any of them, and `ReadProcessMemory` in
#: particular is the one that is unambiguously not allowed.
FORBIDDEN_PROCESS = (
    "ReadProcessMemory",
    "WriteProcessMemory",
    "OpenProcess",
    "CreateRemoteThread",
    "SetWindowsHookEx",
)


def _sources() -> list[Path]:
    files = sorted(PACKAGE.glob("*.py"))
    assert files, f"no sources found under {PACKAGE}; has the package moved?"
    return files


def _code(path: Path) -> str:
    """The file's code with docstrings and comments stripped out.

    Shared with the other source checks via `tests/conftest.py`: the module
    docstring names every one of these APIs in order to say it does not call
    them, so a naive grep over the file would fail on the very sentence that
    makes the promise.
    """
    return code_only(path)


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_input_is_ever_synthesised(path: Path):
    """The line from the module docstring, enforced.

    If this fails, the question is not how to make it pass. It is whether the
    change sends input to the game -- and if it does, it does not go in.
    """
    code = _code(path)
    offenders = [api for api in FORBIDDEN if api in code]
    assert not offenders, (
        f"{path} synthesises input: {offenders}. See the docstring in "
        "src/pricecheck/clipboard.py -- this is the one change that is not allowed."
    )


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_the_game_process_is_never_touched(path: Path):
    code = _code(path)
    offenders = [api for api in FORBIDDEN_PROCESS if api in code]
    assert not offenders, f"{path} reaches into another process: {offenders}"


def test_the_clipboard_is_only_ever_read():
    """Never written, either.

    Not a ban-risk, but a correctness one: the game's own Ctrl+C put the item
    there, and a tool that overwrites the clipboard destroys whatever the user
    had copied for their own reasons -- and would make the sequence-number
    watcher fire on its own writes.
    """
    source = (PACKAGE / "clipboard.py").read_text(encoding="utf-8")
    for api in ("SetClipboardData", "EmptyClipboard"):
        assert api not in source, f"clipboard.py writes the clipboard via {api}"


def test_nothing_in_the_package_reaches_the_trade_api():
    """A price check must cost the rate limit nothing.

    The whole value of pricing from the local model is that a hotkey press
    makes no request to GGG at all -- it goes to 127.0.0.1 and is answered from
    data already collected. A direct call to pathofexile.com from this package
    would turn a key the user presses hundreds of times an evening into
    hundreds of requests, which is how an IP gets rate-limited and how a tool
    gets noticed for the wrong reason.
    """
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for host in ("pathofexile.com", "poe.ninja", "poe.watch"):
            # In a string or a comment is fine -- in a URL being requested is
            # not, and this package's only request target is the loopback
            # address assembled in client.py.
            assert host not in _code(path), (
                f"{path} contains a request to {host}"
            )


def test_the_client_can_only_talk_to_loopback():
    """`127.0.0.1` is not a default here, it is the only option.

    A `host` parameter would be a setting someone eventually points at a LAN
    address, at which point a stash reader is answering the network. The
    service refuses to bind anything else; the client refuses to ask anything
    else.
    """
    import inspect

    from src.pricecheck.client import PriceCheckClient

    source = inspect.getsource(PriceCheckClient)
    assert "127.0.0.1" in source
    assert "host" not in inspect.signature(PriceCheckClient).parameters


def test_the_compliance_note_stays_with_the_code():
    """The reasoning has to survive, because the rule is unenforceable without it.

    These tests can only say "do not add SendInput". Why that is the rule --
    that GGG's line is between reading and acting -- lives in the docstring,
    and a future reader who deletes it is left with a prohibition that looks
    arbitrary and will be argued away.
    """
    source = (PACKAGE / "clipboard.py").read_text(encoding="utf-8")
    assert "SendInput" in source, "the docstring no longer names what it refuses to do"
    assert "Ctrl+C" in source
    assert "tests/test_pricecheck_compliance.py" in source, (
        "the docstring should point at the test that enforces it"
    )
