"""How the overlay looks, as data -- separate from where it sits.

There are two independent things a user means by "the overlay's appearance",
and keeping them apart is the whole reason this module is not part of
`src/overlay/calibration.py`:

- **Where the grid is**, which is a fact about the screen and the game window.
  That is the calibration: origin, cell pitch, grid size, and the resolution
  they were confirmed at. It is per surface -- a stash tab and an inventory
  cannot share one -- and it is measured, not chosen.
- **What the grid looks like**, which is a preference: the label face, the ring
  colours, whether the confidence meter is drawn at all. Nothing here can put a
  price on the wrong map, and nothing here differs between the two surfaces, so
  it is one block shared by both.

Opacity is the one field that sits on the calibration rather than here, and
deliberately: it is edited with `+`/`-` on the calibration screen while looking
at the game through it, it is saved per surface by that screen, and moving it
into this block would mean two places writing the same setting.

Every field is clamped on the way in. The overlay is a window over a game with
no console attached, so a bad value in config.yaml has to degrade to a default
rather than raise inside a redraw.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from src.ui.palette import VALUE_COLOURS, is_hex_colour

#: The config block this reads. One block, both profiles -- see the module docstring.
CONFIG_KEY = "appearance.overlay"

#: Faces Tk can be relied on to resolve on Windows. Tk falls back silently to a
#: default face for a name it does not know, so an unknown font is not an error
#: -- it is a label that quietly stops looking like the one that was picked.
FONT_FAMILIES: tuple[str, ...] = (
    "Segoe UI",
    "Tahoma",
    "Verdana",
    "Arial",
    "Consolas",
    "Courier New",
)

#: Multiplier on the size `font_size_for` derives from the cell. Bounded either
#: side: under a half the price is unreadable at a glance, which is the only
#: thing the overlay is for, and over double a four-character price no longer
#: fits the cell it is labelling.
MIN_FONT_SCALE = 0.5
MAX_FONT_SCALE = 2.0

#: A hairline, a visible border, or a heavy one. Zero is allowed and means no
#: outline at all -- worth having, because on a dark tab the tile edges are
#: sometimes clearer without one.
MIN_OUTLINE_WIDTH = 0
MAX_OUTLINE_WIDTH = 4

#: The control strip has five buttons and a profile label in it. Below this they
#: clip -- a "quit" nobody can find -- and above it the strip is wider than the
#: grid it is anchored to.
MIN_STRIP_WIDTH = 320
MAX_STRIP_WIDTH = 900

#: Where the control strip goes relative to the grid. It never overlaps the
#: grid in either case; this only chooses which side it is pushed off to.
STRIP_ANCHORS: tuple[str, ...] = ("above", "below")


@dataclass(frozen=True)
class OverlayAppearance:
    """Every colour and switch the tkinter renderer reads, with its default.

    The defaults are exactly the constants these fields replaced, so an install
    with no `appearance:` block in its config draws what it always drew.
    """

    font_family: str = "Segoe UI"
    font_scale: float = 1.0
    bold_labels: bool = True

    #: The exalt ring. Cyan appears nowhere in the value bands, which is what
    #: keeps "worth an exalt" legible on top of any of them; a custom colour
    #: that collides with a band is the user's to notice.
    ring_worth: str = "#39d0d8"
    ring_skip: str = "#b6b6bd"

    #: The hairline around every tile. Without it two neighbouring maps in the
    #: same band merge into one block and the alignment with the stash cells --
    #: the entire reason to trust the overlay -- cannot be eyeballed.
    outline: str = "#0e0f12"
    outline_width: int = 1

    #: A flat colour painted across the whole grid, under the tiles. Empty by
    #: default, and that is the important default: the game showing through
    #: every cell with no price is the difference between an overlay and a
    #: screenshot. Set it and the grid becomes a dimmed panel instead.
    tint: str = ""

    #: The unsupported tile. "" takes the palette's `unknown` grey, so a user
    #: who re-colours that band in the web app gets it here too.
    muted_fill: str = ""
    muted_text: str = "#9aa1ab"

    #: The bar the confidence fill sits in. Opaque and near-black rather than a
    #: translucent white, so a half-full meter reads the same over every band.
    meter_track: str = "#15181d"
    dots_fill: str = "#ffffff"

    #: Both readings can be turned off. They are the two marks that compete with
    #: the price for room on a small cell, and on a 40px tab some people would
    #: rather have the number alone.
    show_meter: bool = True
    show_dots: bool = True

    strip_anchor: str = "above"
    strip_width: int = 500

    # -- derived ---------------------------------------------------------
    @property
    def muted_tile_fill(self) -> str:
        """The unsupported tile's fill, resolved against the live palette.

        Read through here rather than frozen into the field so that a band
        re-coloured on the Settings page reaches the overlay too: `VALUE_COLOURS`
        is the one dictionary both front ends share.
        """
        return self.muted_fill or VALUE_COLOURS["unknown"]

    @property
    def font_weight(self) -> str:
        return "bold" if self.bold_labels else "normal"

    def label_font(self, size: int) -> tuple[str, int, str]:
        """The Tk font tuple for a tile label at `size` points."""
        return (self.font_family, int(size), self.font_weight)

    # -- persistence -----------------------------------------------------
    def to_mapping(self) -> dict[str, Any]:
        """The `appearance.overlay:` block, in the shape config.example.yaml documents."""
        return {
            "font_family": self.font_family,
            "font_scale": round(float(self.font_scale), 3),
            "bold_labels": bool(self.bold_labels),
            "ring_worth": self.ring_worth,
            "ring_skip": self.ring_skip,
            "outline": self.outline,
            "outline_width": int(self.outline_width),
            "tint": self.tint,
            "muted_fill": self.muted_fill,
            "muted_text": self.muted_text,
            "meter_track": self.meter_track,
            "dots_fill": self.dots_fill,
            "show_meter": bool(self.show_meter),
            "show_dots": bool(self.show_dots),
            "strip_anchor": self.strip_anchor,
            "strip_width": int(self.strip_width),
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> OverlayAppearance:
        """Read the block back, tolerating anything a hand-edited YAML can hold.

        Forgiving on exactly the same reasoning as `Calibration.from_mapping`:
        this runs in a process with no console, so a typo has to leave the
        overlay drawing in a default the user can see and fix.
        """
        data = data or {}
        default = cls()
        return cls(
            font_family=_text(data.get("font_family"), default.font_family),
            font_scale=_scale(data.get("font_scale"), default.font_scale),
            bold_labels=_flag(data.get("bold_labels"), default.bold_labels),
            ring_worth=_colour(data.get("ring_worth"), default.ring_worth),
            ring_skip=_colour(data.get("ring_skip"), default.ring_skip),
            outline=_colour(data.get("outline"), default.outline),
            outline_width=_width(data.get("outline_width"), default.outline_width),
            # The three that accept "" mean something by it, so they are read
            # through a variant that lets the empty string past the hex check.
            tint=_optional_colour(data.get("tint"), default.tint),
            muted_fill=_optional_colour(data.get("muted_fill"), default.muted_fill),
            muted_text=_colour(data.get("muted_text"), default.muted_text),
            meter_track=_colour(data.get("meter_track"), default.meter_track),
            dots_fill=_colour(data.get("dots_fill"), default.dots_fill),
            show_meter=_flag(data.get("show_meter"), default.show_meter),
            show_dots=_flag(data.get("show_dots"), default.show_dots),
            strip_anchor=(
                str(data.get("strip_anchor"))
                if data.get("strip_anchor") in STRIP_ANCHORS
                else default.strip_anchor
            ),
            strip_width=_strip_width(data.get("strip_width"), default.strip_width),
        )

    @classmethod
    def from_config(cls, cfg) -> OverlayAppearance:
        return cls.from_mapping(cfg.get(CONFIG_KEY) or {})

    def with_values(self, **fields: Any) -> OverlayAppearance:
        """A copy with `fields` replaced and every one of them re-clamped.

        Round-tripped through `from_mapping` rather than handed to `replace`
        directly, so a value that arrives from a widget gets the same bounds a
        value that arrives from the file does. There is exactly one place that
        decides what a legal opacity or font scale is, and it is not the caller.
        """
        return OverlayAppearance.from_mapping({**self.to_mapping(), **fields})


DEFAULT_APPEARANCE = OverlayAppearance()


def save_to_config(cfg, appearance: OverlayAppearance) -> None:
    """Put an appearance into the config object. Writing the file is the caller's.

    Unlike `src/overlay/calibration.save_to_config`, this does not save: it is
    called from the Streamlit page, which already has a save step of its own,
    and a second write of the same file from inside this call would race it.
    """
    cfg.set(CONFIG_KEY, appearance.to_mapping())


# ---------------------------------------------------------------------------
def _text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _colour(value: Any, default: str) -> str:
    return str(value).strip() if is_hex_colour(value) else default


def _optional_colour(value: Any, default: str) -> str:
    """Like `_colour`, but "" is a legal answer meaning "draw nothing"."""
    if value is None:
        return default
    if str(value).strip() == "":
        return ""
    return _colour(value, default)


def _flag(value: Any, default: bool) -> bool:
    return bool(value) if isinstance(value, bool) else default


def _scale(value: Any, default: float) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return default
    return min(MAX_FONT_SCALE, max(MIN_FONT_SCALE, scale))


def _width(value: Any, default: int) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return default
    return min(MAX_OUTLINE_WIDTH, max(MIN_OUTLINE_WIDTH, width))


def _strip_width(value: Any, default: int) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return default
    return min(MAX_STRIP_WIDTH, max(MIN_STRIP_WIDTH, width))
