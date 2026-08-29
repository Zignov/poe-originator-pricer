"""The value-band palette, kept where a process without Streamlit can read it.

These colours used to live in ``src/ui/common.py``. They moved here when
the desktop overlay started drawing the same percentile bands with tkinter, in
its own process: importing the page helpers to get five hex strings would have
pulled Streamlit, the database and the pricing service into a process that never
renders a web page. ``src.ui.common`` re-exports the name, so every existing
import site is unchanged and there is still exactly one definition of what
"high" looks like.
"""
from __future__ import annotations

#: The chaos value under which a map is not worth reading. Absolute, not a
#: percentile: a tab of nothing but 5c maps still has a dearest map, and
#: colouring it as one made the overlay shout about maps nobody would ever
#: bother listing. Everything below this is drawn in the near-black
#: ``negligible`` fill with dim text -- present, alignable, but effectively
#: invisible next to anything that actually carries value.
#:
#: 30c is the same floor the model is trained against -- listings under it are
#: held out of the fit because the map gets rerolled rather than sold -- so a
#: tile below it is one the pricer has the least to say about *and* the one the
#: user cares least about. Both reasons point the same way.
NEGLIGIBLE_CHAOS = 30.0

#: The bands in order, cheapest first. The percentile thresholds and the legend
#: are both derived from this list, so adding a band is a one-line change here
#: rather than four in step with each other.
VALUE_ORDER = (
    "negligible",
    "low",
    "modest",
    "medium",
    "high",
    "very high",
    "exceptional",
)

#: Keyed by the strings src.market.comparables.value_category returns.
#:
#: Six live bands rather than the original four, because the whole job the
#: colour does is "which of these maps am I looking at" across a full tab at a
#: glance, and four buckets put a 60c map and a 400c map in the same one. The
#: ramp walks slate -> teal -> green -> amber -> rust -> magenta: six separated
#: hues rather than one hue getting brighter, so neighbouring bands are told
#: apart by colour and not by remembering a brightness order. Green, amber and
#: magenta are the original values, so a tab that was read yesterday still
#: reads the same way today.
VALUE_COLOURS = {
    "negligible": "#22252a",
    "low": "#46566b",
    "modest": "#2b6e7a",
    "medium": "#2f7d5c",
    "high": "#a8791f",
    "very high": "#b4432f",
    "exceptional": "#9b3d8a",
    "unknown": "#3a3f47",
}

#: The label colour per band. Everything is white on a fill chosen to carry it,
#: except the negligible band: its whole point is to recede, and a white price
#: on a near-black tile is the brightest thing on the screen, which is exactly
#: backwards.
TEXT_ON_VALUE = {"negligible": "#5b626d"}
DEFAULT_TEXT = "#ffffff"


def value_text_colour(category: str) -> str:
    """The price colour for a tile in ``category``."""
    return TEXT_ON_VALUE.get(category, DEFAULT_TEXT)


#: The confidence meter, kept here for the same reason: the tkinter overlay and
#: the web grid both draw it, and a tile that is amber in the browser has to be
#: amber over the game.
#:
#: Keyed by the level strings ``src.ml.confidence.score_confidence`` returns,
#: plus ``""`` for a tile with no estimate behind it. Deliberately light colours:
#: the fill sits on the dark track below, over any of the value bands, and
#: has to stay legible on all of them. Traffic-light hues, because the meter
#: answers "how much should I trust this number", which is the one place on a
#: tile where that reading is the right one.
CONFIDENCE_COLOURS = {
    "High": "#5fd39b",
    "Medium": "#e6b143",
    "Low": "#e8705f",
    "": "#6c737f",
}

#: The bar the fill sits in. Opaque and near-black rather than a translucent
#: white, so a half-full meter looks the same over the purple "very high" band as
#: over the grey "low" one. The length is the message, and a track that took its
#: colour from the tile would make one score look like two different ones.
CONFIDENCE_TRACK = "#15181d"

#: What to show when a reader has the label but not the score -- an overlay state
#: file written by an older build, say. Each is the middle of its band, so the
#: meter degrades to a three-position indicator rather than to nothing.
CONFIDENCE_LEVEL_FRACTION = {"High": 0.85, "Medium": 0.58, "Low": 0.25}

#: Below this the fill is a sliver that reads as an empty track. A "Low" tile
#: still has to look like a meter with *something* in it, or it is
#: indistinguishable from one whose confidence was never computed.
MIN_CONFIDENCE_FILL = 0.06


def confidence_fill(level: str, score: float | None = None) -> tuple[str, float] | None:
    """``(colour, fraction)`` for one confidence meter, or ``None`` for no meter.

    ``None`` -- rather than a zero-length bar -- is the answer for a map that was
    never priced. An empty meter would say the model looked and found nothing to
    go on, which is a different claim from "there is no estimate here at all",
    and the tile already makes the second one with its hatching.
    """
    if not level:
        return None
    colour = CONFIDENCE_COLOURS.get(level, CONFIDENCE_COLOURS[""])
    if score is None:
        fraction = CONFIDENCE_LEVEL_FRACTION.get(level, 0.5)
    else:
        fraction = float(score)
    return colour, min(max(fraction, MIN_CONFIDENCE_FILL), 1.0)
