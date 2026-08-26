"""Priced tab plus calibration in, rectangles out.

All of the overlay's judgement lives here, and none of its pixels: this module
answers "what should be drawn, where, in what colour" as plain data, so the
whole visual contract -- the colour bands, the fallback tile, the rings, the
window's position on a negative-coordinate second monitor -- can be tested
without a display. src/overlay/window.py then does nothing but hand these
rectangles to a tkinter Canvas.

The colours and the banding are not re-derived here. They come from
`percentile_bands` / `value_category` and the shared palette, so the tile that
is purple in the browser is the same purple over the game. That mattered more
than it sounds: the point of the overlay is that you *stop* looking at the
browser, which only works if the two agree exactly.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.market.comparables import percentile_bands, value_category
from src.overlay.calibration import Calibration
from src.overlay.state import EXALT_SKIP, EXALT_WORTH, OverlayState, OverlayTile
from src.ui.palette import CONFIDENCE_TRACK, VALUE_COLOURS, confidence_fill

#: The ring colours, matching the web grid's. Cyan appears nowhere in
#: VALUE_COLOURS, so "worth an exalt" stays readable on top of any band. The
#: "not worth it" ring is a translucent grey in CSS; tkinter has no per-item
#: alpha, so it is the flattened equivalent against the tiles it sits on.
RING_WORTH = "#39d0d8"
RING_SKIP = "#b6b6bd"

#: Every tile gets a hairline outline. Without it, two neighbouring maps in the
#: same value band merge into one block and the alignment with the stash cells
#: -- the entire reason to trust the thing -- becomes impossible to eyeball.
TILE_OUTLINE = "#0e0f12"

TEXT_COLOUR = "#ffffff"

#: An unsupported map still gets a tile, in the "unknown" grey, showing "-".
#: Drawing nothing would be worse: the map is in the tab, and a gap reads as
#: "the overlay has not loaded" rather than "this one cannot be priced".
MUTED_FILL = VALUE_COLOURS["unknown"]
MUTED_LABEL = "-"

#: The confidence meter's geometry, as fractions of the tile it sits in.
#:
#: A strip along the bottom edge rather than a corner dot, because the thing it
#: has to communicate at a glance across a whole tab is *how much* -- and length
#: is the only encoding that survives being read out of the corner of the eye
#: while the actual attention is on the game. The inset clears the exalt ring,
#: which is drawn 2px in at 2px wide, so the two never touch.
METER_INSET = 4
METER_HEIGHT_RATIO = 0.11
METER_MIN_HEIGHT = 2

#: Under this many pixels a cell cannot carry both a price and a meter, and the
#: price wins: a two-pixel smear along the bottom of a tiny tile is noise that
#: makes the number above it harder to read, not a second reading.
METER_MIN_CELL = 22


@dataclass(frozen=True)
class MeterDraw:
    """The two rectangles of one confidence meter: the track, and the fill in it.

    Both are carried, rather than the fraction alone, so that the tkinter layer
    stays a renderer -- rounding a fraction to whole pixels is a decision, and
    all of those live in this module.
    """

    left: int
    top: int
    right: int
    bottom: int
    fill_right: int
    fill: str
    track: str = CONFIDENCE_TRACK

    def relative_to(self, origin: tuple[int, int]) -> MeterDraw:
        return MeterDraw(
            left=self.left - origin[0],
            top=self.top - origin[1],
            right=self.right - origin[0],
            bottom=self.bottom - origin[1],
            fill_right=self.fill_right - origin[0],
            fill=self.fill,
            track=self.track,
        )


@dataclass(frozen=True)
class TileDraw:
    """One rectangle and its label, in screen pixels."""

    left: int
    top: int
    right: int
    bottom: int
    fill: str
    text: str
    ring: str = ""
    meter: MeterDraw | None = None
    tile: OverlayTile | None = None

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def relative_to(self, origin: tuple[int, int]) -> TileDraw:
        """The same rectangle in window coordinates.

        The window is placed at the grid's origin, and a Canvas draws from its
        own top-left, so every rectangle is shifted once on the way in rather
        than every coordinate being written twice.
        """
        return TileDraw(
            left=self.left - origin[0],
            top=self.top - origin[1],
            right=self.right - origin[0],
            bottom=self.bottom - origin[1],
            fill=self.fill,
            text=self.text,
            ring=self.ring,
            meter=self.meter.relative_to(origin) if self.meter else None,
            tile=self.tile,
        )


def ring_colour(exalt: str) -> str:
    """"" for a map that cannot take an orb -- no ring, indistinguishable."""
    if exalt == EXALT_WORTH:
        return RING_WORTH
    if exalt == EXALT_SKIP:
        return RING_SKIP
    return ""


def meter_draw(
    tile: OverlayTile, left: int, top: int, right: int, bottom: int
) -> MeterDraw | None:
    """The confidence meter for one tile, or None if it should not have one.

    None for an unsupported map (there is no estimate to be confident about),
    for a state file with no confidence at all, and for a cell too small to
    carry the bar without eating the price.
    """
    if not tile.supported:
        return None
    filling = confidence_fill(tile.confidence, tile.confidence_score)
    if filling is None:
        return None
    if (right - left) < METER_MIN_CELL or (bottom - top) < METER_MIN_CELL:
        return None

    colour, fraction = filling
    height = max(METER_MIN_HEIGHT, round((bottom - top) * METER_HEIGHT_RATIO))
    bar_left = left + METER_INSET
    bar_right = right - METER_INSET
    bar_bottom = bottom - METER_INSET
    # At least one pixel of fill for any meter that exists at all -- a rounded
    # zero would make a Low tile look like a tile that was never scored.
    fill_right = bar_left + max(1, round((bar_right - bar_left) * fraction))
    return MeterDraw(
        left=bar_left,
        top=bar_bottom - height,
        right=bar_right,
        bottom=bar_bottom,
        fill_right=min(fill_right, bar_right),
        fill=colour,
    )


def build_draw_list(state: OverlayState, calibration: Calibration) -> list[TileDraw]:
    """The rectangles for one priced tab, in screen coordinates.

    Empty cells produce nothing at all. That is the difference between an
    overlay and a screenshot: the game has to show through everywhere there is
    no price, or the tab becomes unreadable and the user turns the thing off.
    """
    bands = percentile_bands(state.supported_prices)
    draws: list[TileDraw] = []
    for tile in state.tiles:
        left, top, right, bottom = calibration.cell_rect(
            tile.x, tile.y, tile.width, tile.height
        )
        if tile.supported and tile.price_chaos is not None:
            fill = VALUE_COLOURS[value_category(tile.price_chaos, bands)]
            text = tile.label
        else:
            fill = MUTED_FILL
            text = MUTED_LABEL
        draws.append(
            TileDraw(
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                fill=fill,
                text=text,
                ring=ring_colour(tile.exalt),
                meter=meter_draw(tile, left, top, right, bottom),
                tile=tile,
            )
        )
    return draws


def window_geometry(calibration: Calibration) -> tuple[int, int, int, int]:
    """(width, height, x, y) for the overlay window, covering the whole grid.

    Sized to the grid rather than to the screen so that the transparent window
    is no larger than it has to be: a full-screen layered window is a full
    screen of per-pixel compositing on every redraw, and this one redraws on a
    timer.
    """
    left, top, right, bottom = calibration.grid_rect()
    return (max(1, right - left), max(1, bottom - top), left, top)


def geometry_string(width: int, height: int, x: int, y: int) -> str:
    """Tk's geometry string, with negative offsets written the way Tk means them.

    In Tk, `+x` counts from the left edge and `-x` counts from the *right*, so a
    monitor to the left of the primary one -- whose coordinates are genuinely
    negative -- has to be written `+-1920`, not `-1920`. Getting this wrong puts
    the overlay against the far right of the desktop, which looks like a bug in
    the calibration rather than in the string.
    """
    return f"{int(width)}x{int(height)}{_offset(x)}{_offset(y)}"


def font_size_for(cell_px: float) -> int:
    """Point size that keeps a four-character price inside a cell.

    Derived from the cell rather than fixed: the same tab is 40px a cell at
    1080p and 70px on a 4K monitor, and a fixed size is either unreadable or
    spills over the neighbours.
    """
    return max(6, min(16, int(cell_px * 0.26)))


def _offset(value: int) -> str:
    # Always a leading "+", which for a negative value yields "+-1920": the
    # literal-coordinate form, rather than Tk's from-the-right-edge form.
    return f"+{int(value)}"
