"""Widget selections that survive a restart.

A Streamlit widget's value lives in a session, and a session dies with the
browser tab. That is the right default for a form and the wrong one for a tool
you open every evening to read the same stash tab: the source picker, the tab
list, the character, the pricing mode and the whole filter row all came up
blank on every launch, so the first half-dozen clicks of every session were
spent putting the app back where it was left.

So the values are also written to one small JSON file. Not the `app_settings`
table next door -- its `value` column is REAL, and these are tab names, mode
strings and lists. A file also matches the overlay's precedent (see
src/overlay/state.py), is inspectable when something looks wrong, and survives
the database being rebuilt, which for a preference is the behaviour you want.

Two rules shape the whole module:

**A remembered value is only used if it is still offered.** Tabs get renamed,
leagues end, option lists get edited between releases. A remembered choice that
no longer exists must fall back to the default rather than wedge the widget or
raise, which is what `remembered` below is for and what most of its tests are
about.

**Nothing here is important.** These are conveniences. A corrupt or
unreadable file reads as "nothing remembered" rather than stopping the app
from starting -- a traceback on launch would be a far worse bug than the one
this module fixes.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import streamlit as st

from src.config import ROOT

#: Where the preferences live. Patched in tests; read through the module rather
#: than imported by value so that patching works.
PATH = ROOT / "data" / "ui_prefs.json"

#: What JSON can hold, and therefore what is allowed through `flush`. A widget
#: whose value is some richer object -- the overlay's profile radio returns an
#: `OverlayProfile` -- is silently not remembered rather than crashing the save.
_PRIMITIVES = (str, int, float, bool)

#: Session keys this module owns. Prefixed so they cannot collide with a widget.
_DISK = "_prefs_disk"
_TRACKED = "_prefs_tracked"


class PrefStore:
    """The file. Load, save, forget -- nothing else."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, values: dict[str, Any]) -> None:
        """Write, atomically, and only when something actually changed.

        The guard is not an optimisation so much as a consequence of how
        Streamlit works: the whole script re-executes on every keystroke, so a
        save that wrote unconditionally would rewrite this file several times a
        second while a number is being typed, for values that are identical
        each time.
        """
        if self.load() == values:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
        )
        try:
            with handle:
                json.dump(values, handle, indent=2, sort_keys=True)
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


def _jsonable(value: Any) -> Any:
    """`value` as something JSON can hold, or `None` if it cannot hold it.

    `None` doubles as the "nothing remembered" marker in `remembered`, which is
    exactly the right reading for a value that could not be stored.
    """
    if isinstance(value, _PRIMITIVES):
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        return items if all(isinstance(v, _PRIMITIVES) for v in items) else None
    return None


def remembered(
    stored: Any,
    default: Any,
    options: Sequence[Any] | None = None,
    bounds: tuple[float, float] | None = None,
) -> Any:
    """What a widget should start with, given what was remembered for it.

    Returns the *option object* rather than the stored value when the two only
    match through JSON -- a selectbox over tuples reads its remembered choice
    back as a list, and Streamlit compares session values to options with `==`,
    so handing back the list would fail the very check this exists to pass.
    """
    if stored is None:
        return default

    # A multiselect. Rebuilt from the offered options rather than filtered from
    # the stored list, so the order on screen is the order of the options and
    # not the order they happened to be picked in last time.
    if isinstance(default, list):
        if not isinstance(stored, list):
            return default
        if options is None:
            return stored
        kept = [o for o in options if _jsonable(o) in stored or o in stored]
        # Everything vanished -- the ordinary way being a changed league. An
        # empty multiselect blocks the Analyze button, so the default is more
        # use than the remainder.
        return kept or default

    # bool before the number branch: in Python a bool *is* an int, and a
    # checkbox that accepted 1 or a number input that accepted True would both
    # make Streamlit raise on the next run.
    if isinstance(default, bool):
        return stored if isinstance(stored, bool) else default

    if isinstance(default, (int, float)):
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return default
        # number_input infers int or float from its default and refuses the
        # other. JSON has one number type, so 50.0 comes back as 50 and has to
        # be widened -- but 2.5 can never narrow into an int widget.
        if isinstance(default, int) and not isinstance(stored, int):
            return default
        value = type(default)(stored)
        if bounds is not None and not (bounds[0] <= value <= bounds[1]):
            return default
        return value

    if options is not None:
        return next(
            (o for o in options if o == stored or _jsonable(o) == stored), default
        )
    return stored if isinstance(stored, type(default)) else default


# -- the Streamlit-facing half ----------------------------------------------


def _store() -> PrefStore:
    return PrefStore(PATH)


def _saved() -> dict[str, Any]:
    """The file, read once per session rather than once per rerun."""
    if _DISK not in st.session_state:
        st.session_state[_DISK] = _store().load()
    return st.session_state[_DISK]


def sticky(
    key: str,
    default: Any,
    options: Sequence[Any] | None = None,
    *,
    bounds: tuple[float, float] | None = None,
) -> str:
    """Seed a widget's value from the file, and hand back its key.

    Written to be a one-line change at the call site::

        mode = st.radio(..., key=prefs.sticky("pricing_mode", "model", list(MODES)))

    The widget's own `index=` / `value=` / `default=` argument goes away when
    this is added, and that is not optional: Streamlit warns when a widget is
    given both a literal default and a session-state value for its key.
    """
    if key not in st.session_state:
        st.session_state[key] = remembered(
            _saved().get(key), default, options, bounds
        )
    st.session_state.setdefault(_TRACKED, set()).add(key)
    return key


def flush() -> None:
    """Write every sticky value on the page. Called once, after the page renders.

    One call site in app.py rather than an `on_change` per widget, because two
    of these widgets already carry an `on_change` doing something else and
    chaining onto them would be a lot of plumbing to buy nothing.

    A key that is tracked but absent from the session keeps whatever was last
    saved for it. That case is routine rather than exceptional: Streamlit drops
    the state of any widget it did not draw this run, so every key on the page
    you just navigated away from is missing here.
    """
    tracked = st.session_state.get(_TRACKED)
    if not tracked:
        return
    values = dict(_saved())
    for key in tracked:
        if key not in st.session_state:
            continue
        value = _jsonable(st.session_state[key])
        if value is not None:
            values[key] = value
    _store().save(values)
    st.session_state[_DISK] = values


def forget() -> None:
    """Drop every remembered selection and reset the widgets holding them.

    The file comes back on the next save, holding the defaults -- which is what
    "forgotten" means here anyway, and cheaper than teaching `flush` about a
    tombstone.
    """
    _store().forget()
    st.session_state[_DISK] = {}
    for key in st.session_state.pop(_TRACKED, set()):
        st.session_state.pop(key, None)
