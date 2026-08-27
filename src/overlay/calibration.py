"""Where the stash grid sits on screen, and why that has to be told rather than found.

The overlay has to line its rectangles up with the stash cells the game is
drawing. Two ways to learn that: look at the screen, or ask.

Looking at the screen means screen-capturing the client and template-matching
the tab -- OpenCV, a new dependency, a new failure mode every time GGG changes a
border by two pixels, and a tool that reads pixels out of the game window, which
is exactly the class of thing this project stays away from. Asking means the
user drags a rectangle once, and it is then right forever.

So: manual calibration, persisted to config. The whole cost is one minute, once.

Two details that are not obvious:

- **`cell_px` is a float.** The stash panel scales with the window, and a 12x12
  tab at 1440p lands on cell sizes like 53.4px. Rounding it to 53 accumulates
  five pixels of drift by the right-hand column, which is enough to put a price
  on the wrong map. Every cell edge is therefore computed from the float and
  rounded only at the very end.
- **The screen resolution is stored with the calibration.** Change resolution,
  or move the game to the other monitor, and the old numbers are silently wrong
  -- still plausible, still drawn, just on the wrong pixels. The overlay checks
  and says so rather than letting you trust it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.overlay.profiles import DEFAULT_PROFILE, OverlayProfile, profile_for

#: Below this a "cell" is not a cell any more, and a resize keypress that walked
#: cell_px to zero would leave a window with no area and no way back.
MIN_CELL_PX = 4.0

#: Alpha bounds. Fully opaque hides the game, and below this the numbers stop
#: being readable over a bright map device.
MIN_OPACITY = 0.1
MAX_OPACITY = 1.0

#: Kept as a name because it is the stash overlay's chord and half the codebase
#: says "the default hotkey" meaning that one. Per-profile defaults live in
#: src/overlay/profiles.py; the inventory's is a different chord so that both
#: overlays can be toggled independently.
DEFAULT_HOTKEY = DEFAULT_PROFILE.hotkey
DEFAULT_ANALYZE_HOTKEY = DEFAULT_PROFILE.analyze_hotkey


@dataclass(frozen=True)
class Calibration:
    """The grid's position on screen, in virtual-desktop pixels.

    `origin` is the top-left of cell (0, 0) in *virtual* screen coordinates,
    which on a multi-monitor desktop are negative for anything left of or above
    the primary monitor. `monitor` records which screen the user calibrated on;
    it is informational -- the origin already carries the offset -- but it makes
    a mismatched calibration explainable rather than merely wrong.
    """

    origin: tuple[int, int] = (0, 0)
    cell_px: float = 53.4
    grid: tuple[int, int] = (12, 12)
    opacity: float = 0.55
    monitor: int = 0
    hotkey_toggle: str = DEFAULT_HOTKEY
    #: Chord that re-fetches and re-prices this surface, rather than merely
    #: showing it. Configurable for the same reason the toggle is: the chord may
    #: already belong to something else on this machine.
    hotkey_analyze: str = DEFAULT_ANALYZE_HOTKEY
    screen: tuple[int, int] | None = None
    #: Which surface this calibration is for -- see src/overlay/profiles.py.
    #: Carried on the object rather than passed alongside it because every
    #: `replace()` in the calibration keys would otherwise have to remember it,
    #: and a calibration that forgot which block it came from would save the
    #: inventory's origin over the stash's.
    profile: str = DEFAULT_PROFILE.key

    @property
    def calibrated(self) -> bool:
        """False until the user has actually saved a calibration.

        Keyed on `screen` because that is the field nobody can supply by
        accident: a default origin of (0, 0) is a legitimate calibration on a
        maximised window at the top-left, but a stored resolution only exists if
        the calibration screen wrote one.
        """
        return self.screen is not None

    # -- geometry -------------------------------------------------------
    def cell_rect(self, x: int, y: int, width: int = 1, height: int = 1) -> tuple[int, int, int, int]:
        """Screen rectangle (left, top, right, bottom) of a cell or item.

        Both edges are derived from the float `cell_px` and rounded
        independently, so neighbouring cells share an edge exactly and no
        rounding error accumulates across the row. `width`/`height` come from
        the item, not from an assumption: Originator Maps are 1x1 today, and the
        moment something 2x1 lands in the tab the arithmetic already works.
        """
        left = self.origin[0] + x * self.cell_px
        top = self.origin[1] + y * self.cell_px
        right = self.origin[0] + (x + width) * self.cell_px
        bottom = self.origin[1] + (y + height) * self.cell_px
        return (round(left), round(top), round(right), round(bottom))

    def grid_rect(self) -> tuple[int, int, int, int]:
        """The whole grid outline, in the same coordinates."""
        return self.cell_rect(0, 0, self.grid[0], self.grid[1])

    def size(self) -> tuple[int, int]:
        left, top, right, bottom = self.grid_rect()
        return (right - left, bottom - top)

    # -- the calibration keys -------------------------------------------
    def moved(self, dx: int, dy: int) -> Calibration:
        """Nudge the origin. One arrow press, one pixel."""
        return replace(self, origin=(self.origin[0] + dx, self.origin[1] + dy))

    def resized(self, d_width: int = 0, d_height: int = 0) -> Calibration:
        """Grow or shrink the whole outline by whole pixels.

        The user drags the outline's *edge*, which is the thing they can see, so
        the keypress is expressed in outline pixels and converted back into a
        cell size here. There is one `cell_px` for both axes -- stash cells are
        square -- so when both axes move in one call the two implied sizes are
        averaged rather than one silently winning.
        """
        cols, rows = self.grid
        implied: list[float] = []
        if d_width:
            implied.append((cols * self.cell_px + d_width) / cols)
        if d_height:
            implied.append((rows * self.cell_px + d_height) / rows)
        if not implied:
            return self
        cell = max(MIN_CELL_PX, sum(implied) / len(implied))
        return replace(self, cell_px=cell)

    def with_opacity(self, opacity: float) -> Calibration:
        return replace(self, opacity=min(MAX_OPACITY, max(MIN_OPACITY, float(opacity))))

    def with_grid(self, cols: int, rows: int) -> Calibration:
        return replace(self, grid=(max(1, int(cols)), max(1, int(rows))))

    def with_screen(self, screen: tuple[int, int] | None) -> Calibration:
        return replace(self, screen=None if screen is None else (int(screen[0]), int(screen[1])))

    # -- persistence ----------------------------------------------------
    def to_mapping(self) -> dict[str, Any]:
        """The `overlay:` block, in the shape config.example.yaml documents."""
        data: dict[str, Any] = {
            "origin": [int(self.origin[0]), int(self.origin[1])],
            "cell_px": round(float(self.cell_px), 3),
            "grid": [int(self.grid[0]), int(self.grid[1])],
            "opacity": round(float(self.opacity), 3),
            "monitor": int(self.monitor),
            "hotkey_toggle": self.hotkey_toggle,
            "hotkey_analyze": self.hotkey_analyze,
        }
        if self.screen is not None:
            data["screen"] = [int(self.screen[0]), int(self.screen[1])]
        return data

    @property
    def profile_spec(self) -> OverlayProfile:
        return profile_for(self.profile)

    @classmethod
    def from_mapping(
        cls, data: dict[str, Any] | None, profile: str | OverlayProfile = DEFAULT_PROFILE
    ) -> Calibration:
        """Read the block back, tolerating anything a hand-edited YAML can hold.

        Deliberately forgiving: a typo in config.yaml should leave the overlay
        drawing in a default place the user can see and fix, not raise on
        startup of a window that has no console attached to print into.
        """
        data = data or {}
        spec = profile_for(profile)
        default = cls()
        origin = _pair(data.get("origin"), default.origin)
        # The grid and the chord default per profile, not per class: an
        # inventory block with no `grid` means 12x5, and inheriting 12x12 there
        # would draw seven rows of tiles over the game below the panel.
        grid = _pair(data.get("grid"), spec.grid)
        screen = _pair(data.get("screen"), None) if data.get("screen") else None
        return cls(
            origin=(int(origin[0]), int(origin[1])),
            cell_px=max(MIN_CELL_PX, _number(data.get("cell_px"), default.cell_px)),
            grid=(max(1, int(grid[0])), max(1, int(grid[1]))),
            opacity=min(MAX_OPACITY, max(MIN_OPACITY, _number(data.get("opacity"), default.opacity))),
            monitor=int(_number(data.get("monitor"), 0)),
            hotkey_toggle=str(data.get("hotkey_toggle") or spec.hotkey),
            hotkey_analyze=str(data.get("hotkey_analyze") or spec.analyze_hotkey),
            screen=None if screen is None else (int(screen[0]), int(screen[1])),
            profile=spec.key,
        )

    @classmethod
    def from_config(cls, cfg, profile: str | OverlayProfile = DEFAULT_PROFILE) -> Calibration:
        spec = profile_for(profile)
        return cls.from_mapping(cfg.get(spec.config_key) or {}, spec)


def save_to_config(
    cfg,
    calibration: Calibration,
    path: Path | None = None,
    profile: str | OverlayProfile | None = None,
) -> Path:
    """Persist a calibration and write config.yaml.

    Writes the file rather than only the in-memory config: calibration happens
    in the overlay process, which exits straight afterwards, so anything left in
    memory is lost and the user would be asked to calibrate again on every
    launch.
    """
    spec = profile_for(profile or calibration.profile)
    cfg.set(spec.config_key, calibration.to_mapping())
    return cfg.save(path)


def resolution_warning(
    calibration: Calibration, current_screen: tuple[int, int] | None
) -> str | None:
    """The message to show when the saved calibration cannot be trusted.

    None means "go ahead". Everything else is worth showing, because a
    calibration that is wrong is not visibly wrong -- it still draws a tidy grid
    of prices, just over the wrong maps.
    """
    if not calibration.calibrated:
        surface = calibration.profile_spec.label.lower()
        return (
            "The overlay has never been calibrated, so it does not know where your "
            f"{surface} is. Run it with --calibrate first."
        )
    if current_screen is None:
        return None
    if tuple(calibration.screen or ()) != tuple(current_screen):
        saved = "x".join(str(v) for v in calibration.screen or ())
        now = "x".join(str(v) for v in current_screen)
        return (
            f"This calibration was saved at {saved} and the screen is now {now}. "
            "The grid will be in the wrong place -- recalibrate."
        )
    return None


def _pair(value: Any, default: tuple[int, int] | None) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return default
    return default


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
