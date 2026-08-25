"""The overlay's data contract: one JSON file, written by the app, read by the window.

Streamlit and tkinter cannot share a process. Streamlit re-executes the whole
script on every widget touch and runs it on a thread it owns; tkinter insists on
owning the main thread and then sitting in a blocking `mainloop`. Starting a
window from inside a Streamlit rerun therefore either hangs the page or produces
a window that dies on the next click. So the overlay is a **second process**,
and the two halves meet at a file.

A file was chosen over a socket or a pipe deliberately:

- either side can restart without a handshake -- the overlay can be running
  before the tab is priced, or started an hour after;
- it is inspectable when something looks wrong (it is just JSON on disk);
- it costs nothing while idle, which a polling socket does not.

The overlay watches the file's modification stamp and redraws when it moves.

The schema is **versioned**, and an overlay that reads a version it does not
understand refuses the file rather than drawing half-understood prices over the
game -- a wrong number on a tile is worse than no tile, because the whole point
of the thing is that you stop cross-referencing the browser.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import ROOT
from src.pricing.display import PriceFormatter
from src.pricing.exalt import ExaltVerdict
from src.pricing.fair_value import PricedMap

#: Bumped whenever the meaning of a field changes. A reader that sees a version
#: it was not written for stops; it does not guess.
OVERLAY_STATE_VERSION = 1

#: Where the app writes and the overlay reads unless told otherwise. Under
#: data/ because it is regenerated output, not configuration.
DEFAULT_STATE_PATH = ROOT / "data" / "overlay_state.json"

#: The three exalt states a tile can be in, matching the three rings the web
#: grid draws. "" is deliberate and load-bearing: a map that cannot take an orb
#: must look exactly like a plain tile, so that an outline is never an
#: invitation to spend currency on something that cannot use it.
EXALT_WORTH = "worth"
EXALT_SKIP = "skip"
EXALT_NONE = ""


class OverlayStateError(RuntimeError):
    """The state file is missing, unreadable, or of an unknown version."""


def tile_price(entry: PricedMap) -> float:
    """What a tile shows: the suggested listing price for the chosen strategy.

    The single definition, shared with the web grid (src/ui/stash_grid.py calls
    it). Fair value is the fallback so a tile never goes blank because a
    strategy multiplier was missing, and it is never the first choice: drawing
    fair value made the strategy selector look broken, because every other
    number moved with it and the tiles did not.
    """
    if entry.suggested_chaos is not None:
        return float(entry.suggested_chaos)
    return float(entry.estimate.fair_value_chaos) if entry.estimate else 0.0


def exalt_state(verdict: ExaltVerdict | None) -> str:
    """Which of the three exalt states a verdict puts a tile in."""
    if verdict is None or not verdict.available:
        return EXALT_NONE
    return EXALT_WORTH if verdict.worth_it else EXALT_SKIP


@dataclass(frozen=True)
class OverlayTile:
    """One occupied stash cell.

    Carries both the raw chaos price and the already-formatted label. The label
    is formatted by the app, in the app's display currency, so the overlay draws
    character-for-character what the web grid drew; the raw price is kept
    because the colour bands are percentiles of the tab and have to be computed
    over the whole set, which only the reader can do.
    """

    x: int
    y: int
    width: int = 1
    height: int = 1
    position: str = ""
    name: str = ""
    supported: bool = True
    price_chaos: float | None = None
    label: str = "-"
    fair_value_chaos: float | None = None
    confidence: str = ""
    exalt: str = EXALT_NONE
    unsupported_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "position": self.position,
            "name": self.name,
            "supported": self.supported,
            "price_chaos": self.price_chaos,
            "label": self.label,
            "fair_value_chaos": self.fair_value_chaos,
            "confidence": self.confidence,
            "exalt": self.exalt,
            "unsupported_reason": self.unsupported_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OverlayTile:
        try:
            return cls(
                x=int(data["x"]),
                y=int(data["y"]),
                width=int(data.get("width", 1) or 1),
                height=int(data.get("height", 1) or 1),
                position=str(data.get("position", "")),
                name=str(data.get("name", "")),
                supported=bool(data.get("supported", True)),
                price_chaos=_optional_float(data.get("price_chaos")),
                label=str(data.get("label", "-")),
                fair_value_chaos=_optional_float(data.get("fair_value_chaos")),
                confidence=str(data.get("confidence", "")),
                exalt=str(data.get("exalt", EXALT_NONE)),
                unsupported_reason=str(data.get("unsupported_reason", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OverlayStateError(f"malformed tile in overlay state: {exc}") from exc


@dataclass(frozen=True)
class OverlayState:
    """Everything the overlay needs to draw one priced tab."""

    tiles: tuple[OverlayTile, ...] = ()
    grid: tuple[int, int] = (12, 12)
    strategy: str = "market"
    league: str = ""
    tab_names: tuple[str, ...] = ()
    unit: str = "chaos"
    divine_to_chaos: float = 0.0
    generated_at: str = ""
    version: int = OVERLAY_STATE_VERSION

    @property
    def supported_prices(self) -> list[float]:
        return [t.price_chaos for t in self.tiles if t.supported and t.price_chaos is not None]

    def summary(self) -> str:
        """One line for the control strip, so the user can see the file landed."""
        tabs = ", ".join(self.tab_names) or "no tab name"
        when = self.generated_at[11:16] if len(self.generated_at) >= 16 else "?"
        return f"{len(self.tiles)} maps  |  {tabs}  |  {self.strategy}  |  {when}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "league": self.league,
            "tab_names": list(self.tab_names),
            "strategy": self.strategy,
            "currency": {"unit": self.unit, "divine_to_chaos": self.divine_to_chaos},
            "grid": list(self.grid),
            "tiles": [t.to_dict() for t in self.tiles],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OverlayState:
        if not isinstance(data, dict):
            raise OverlayStateError("overlay state must be a JSON object")
        version = data.get("version")
        if version != OVERLAY_STATE_VERSION:
            raise OverlayStateError(
                f"overlay state is version {version!r}, this build reads version "
                f"{OVERLAY_STATE_VERSION}. Re-export the tab from the Stash Pricer page."
            )
        grid = data.get("grid") or [12, 12]
        currency = data.get("currency") or {}
        return cls(
            tiles=tuple(OverlayTile.from_dict(t) for t in data.get("tiles", [])),
            grid=(int(grid[0]), int(grid[1])),
            strategy=str(data.get("strategy", "market")),
            league=str(data.get("league", "")),
            tab_names=tuple(str(n) for n in data.get("tab_names", [])),
            unit=str(currency.get("unit", "chaos")),
            divine_to_chaos=float(currency.get("divine_to_chaos", 0.0) or 0.0),
            generated_at=str(data.get("generated_at", "")),
            version=int(version),
        )


def state_from_priced(
    priced: Sequence[PricedMap],
    fmt: PriceFormatter,
    *,
    grid: tuple[int, int] = (12, 12),
    strategy: str = "market",
    league: str = "",
    tab_names: Iterable[str] = (),
    verdicts: dict[int, ExaltVerdict] | None = None,
) -> OverlayState:
    """Project a priced tab onto the overlay contract.

    `verdicts` is keyed by `id(entry)`, the same convention the web grid uses,
    so a caller that already computed them for the grid can hand over the same
    dict rather than paying for a second pass.

    Maps with no stash coordinates are dropped rather than stacked at the
    origin: the clipboard source numbers items in paste order, which is not a
    layout, and drawing it as one would put prices on the wrong maps -- the one
    failure mode this feature must not have.
    """
    verdicts = verdicts or {}
    tiles: list[OverlayTile] = []
    for entry in priced:
        item = entry.item
        if item.x is None or item.y is None:
            continue
        supported = entry.supported
        price = tile_price(entry) if supported else None
        tiles.append(
            OverlayTile(
                x=int(item.x),
                y=int(item.y),
                width=int(item.width or 1),
                height=int(item.height or 1),
                position=item.position_label(),
                name=item.name or item.base_type,
                supported=supported,
                price_chaos=price,
                label=fmt.format(price) if supported else "-",
                fair_value_chaos=(
                    float(entry.estimate.fair_value_chaos) if entry.estimate else None
                ),
                confidence=entry.estimate.confidence if entry.estimate else "",
                exalt=exalt_state(verdicts.get(id(entry))),
                unsupported_reason="" if supported else (item.unsupported_reason or "Unsupported"),
            )
        )

    # Grow the grid to whatever the tab actually contains, exactly as the web
    # grid does -- a quad tab is 24x24 and nobody should have to say so twice.
    width = max(grid[0], max((t.x + t.width for t in tiles), default=0))
    height = max(grid[1], max((t.y + t.height for t in tiles), default=0))

    return OverlayState(
        tiles=tuple(tiles),
        grid=(width, height),
        strategy=strategy,
        league=league,
        tab_names=tuple(tab_names),
        unit=fmt.unit,
        divine_to_chaos=fmt.divine_to_chaos,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def write_state(state: OverlayState, path: Path | str = DEFAULT_STATE_PATH) -> Path:
    """Write the file the overlay watches, atomically.

    Atomic because the reader is watching for the modification stamp to move and
    will open the file the instant it does. A plain `write_text` is two syscalls
    with a truncated file in between, and the overlay would occasionally read
    that empty file and blank the screen.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_state(path: Path | str = DEFAULT_STATE_PATH) -> OverlayState:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise OverlayStateError(
            f"no overlay state at {path}. Price a tab and press "
            '"Send this tab to the desktop overlay" on the Stash Pricer page.'
        ) from exc
    except OSError as exc:  # pragma: no cover - permissions, locked file
        raise OverlayStateError(f"could not read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OverlayStateError(f"{path} is not valid JSON: {exc}") from exc
    return OverlayState.from_dict(data)


@dataclass
class StateWatcher:
    """Polls one file's modification stamp and re-reads it when it moves.

    Polling rather than a filesystem notification API because the only thing
    watching is a tkinter `after` loop that has to wake up anyway, and because
    two lines of `stat` behave the same on a network drive, a Dropbox folder and
    a WSL mount, which `ReadDirectoryChangesW` does not.

    The mtime alone is not enough on Windows, where the timestamp granularity
    can be coarser than two exports in quick succession, so the size goes into
    the stamp as well.
    """

    path: Path
    _stamp: tuple[int, int] | None = field(default=None, init=False, repr=False)
    error: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def poll(self) -> OverlayState | None:
        """The new state if the file changed since the last call, else None."""
        try:
            info = self.path.stat()
        except OSError:
            # Absent is not an error worth shouting about on every tick: the
            # overlay is allowed to run before the first export.
            self._stamp = None
            return None
        stamp = (info.st_mtime_ns, info.st_size)
        if stamp == self._stamp:
            return None
        self._stamp = stamp
        try:
            state = read_state(self.path)
        except OverlayStateError as exc:
            self.error = str(exc)
            return None
        self.error = ""
        return state


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
