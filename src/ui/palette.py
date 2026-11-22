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


# ===========================================================================
# Re-colouring, and the page chrome around it
#
# Everything above is a shipped default. Everything below is how the Settings
# page changes one, and how that change survives a restart.
#
# The band and confidence dictionaries are mutated **in place** rather than
# rebound, and that is the whole mechanism: `src/ui/stash_grid.py` and
# `src/overlay/layout.py` both do `from src.ui.palette import VALUE_COLOURS`
# and then subscript it at draw time, so they share this one object. Rebinding
# the name here would leave both of them holding the old dictionary; mutating
# it re-colours the web grid and the overlay together, which is the property
# this module exists to guarantee.
# ===========================================================================

#: The shipped values, kept so "Reset to defaults" has something to reset to
#: after the live dictionaries have been written over.
DEFAULT_VALUE_COLOURS = dict(VALUE_COLOURS)
DEFAULT_CONFIDENCE_COLOURS = dict(CONFIDENCE_COLOURS)
DEFAULT_TEXT_ON_VALUE = dict(TEXT_ON_VALUE)

#: The keys a colour picker may set, in the order they should be offered.
#: `unknown` is last because it is not a band -- it is what an unpriceable map
#: gets -- and putting it in the middle of the ramp reads as a seventh band.
VALUE_COLOUR_KEYS: tuple[str, ...] = (*VALUE_ORDER, "unknown")
CONFIDENCE_COLOUR_KEYS: tuple[str, ...] = ("High", "Medium", "Low")


def is_hex_colour(value: object) -> bool:
    """True for ``#rgb`` and ``#rrggbb``, which is all tkinter and CSS agree on.

    Validated rather than trusted because the same string is handed to a Tcl
    canvas, and an unparseable colour there is a `TclError` in the middle of a
    redraw -- an overlay that vanishes over a game, with no console to say why.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) not in (4, 7) or not text.startswith("#"):
        return False
    return all(char in "0123456789abcdefABCDEF" for char in text[1:])


def apply_palette_overrides(overrides: dict | None) -> None:
    """Write user colours into the live dictionaries, ignoring anything invalid.

    Silently skipping a bad entry is deliberate: this runs at import time
    against a file the user may have hand-edited, and one mistyped hex string
    should cost that one band its colour rather than stop the app from starting.
    """
    overrides = overrides or {}

    for key, colour in (overrides.get("value_colours") or {}).items():
        if key in VALUE_COLOURS and is_hex_colour(colour):
            VALUE_COLOURS[key] = str(colour).strip()

    for key, colour in (overrides.get("confidence_colours") or {}).items():
        if key in CONFIDENCE_COLOURS and is_hex_colour(colour):
            CONFIDENCE_COLOURS[key] = str(colour).strip()

    negligible = (overrides.get("text_on_value") or {}).get("negligible")
    if is_hex_colour(negligible):
        TEXT_ON_VALUE["negligible"] = str(negligible).strip()


def reset_palette() -> None:
    """Put every band, meter and label colour back to the shipped value."""
    VALUE_COLOURS.clear()
    VALUE_COLOURS.update(DEFAULT_VALUE_COLOURS)
    CONFIDENCE_COLOURS.clear()
    CONFIDENCE_COLOURS.update(DEFAULT_CONFIDENCE_COLOURS)
    TEXT_ON_VALUE.clear()
    TEXT_ON_VALUE.update(DEFAULT_TEXT_ON_VALUE)


def current_palette() -> dict[str, dict[str, str]]:
    """What is on screen right now, in the shape `apply_palette_overrides` takes."""
    return {
        "value_colours": dict(VALUE_COLOURS),
        "confidence_colours": dict(CONFIDENCE_COLOURS),
        "text_on_value": dict(TEXT_ON_VALUE),
    }


def palette_overrides_only() -> dict[str, dict[str, str]]:
    """Just the entries that differ from the shipped colours.

    What gets written to config.yaml. Storing the whole palette instead would
    freeze a release's colours into every user's config file, so a later change
    to a band would reach nobody who had ever opened this page.
    """
    out: dict[str, dict[str, str]] = {}
    changed = {k: v for k, v in VALUE_COLOURS.items() if DEFAULT_VALUE_COLOURS.get(k) != v}
    if changed:
        out["value_colours"] = changed
    changed = {
        k: v for k, v in CONFIDENCE_COLOURS.items() if DEFAULT_CONFIDENCE_COLOURS.get(k) != v
    }
    if changed:
        out["confidence_colours"] = changed
    changed = {k: v for k, v in TEXT_ON_VALUE.items() if DEFAULT_TEXT_ON_VALUE.get(k) != v}
    if changed:
        out["text_on_value"] = changed
    return out


# ---------------------------------------------------------------------------
# The web page's structural colours
#
# These are *not* data colours -- nothing here belongs to a band, a confidence
# level or an exalt verdict, and that separation is the design rule the whole
# app follows. They are the ground, the panels, the hairlines and the ink, and
# they are the only part of the page a theme is allowed to move.
# ---------------------------------------------------------------------------

#: Token name -> value, matching the `:root` block in src/ui/common.py one for
#: one. `dark` is that block; `light` is the same structure inverted, with the
#: value bands left exactly as they are -- a band is a measurement and does not
#: get lighter because the page did.
WEB_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "ground": "#0d1014",
        "panel": "#151a21",
        "panel-lift": "#1b2129",
        "rule": "#262d38",
        "rule-soft": "#1e242d",
        "ink": "#e7eaf0",
        "ink-dim": "#8d97a8",
        "ink-faint": "#656e7d",
    },
    "light": {
        "ground": "#f4f2ed",
        "panel": "#ffffff",
        "panel-lift": "#ebe7de",
        "rule": "#d5cfc2",
        "rule-soft": "#e4dfd4",
        "ink": "#181b20",
        "ink-dim": "#4f5865",
        "ink-faint": "#727b89",
    },
}

#: What "follow system" resolves to when something has to pick one anyway --
#: the preview on the Settings page, and the tokens written into the stylesheet.
#: Streamlit itself is told nothing, which is what makes it follow the browser.
SYSTEM_THEME_FALLBACK = "dark"

THEME_KEYS: tuple[str, ...] = ("dark", "light", "system")
THEME_LABELS = {
    "dark": "Dark",
    "light": "Light",
    "system": "Follow system",
}

#: Body-text scale, as a multiplier on the browser's root size. The floor and
#: ceiling are the range in which the stash grid still lays out: below 0.8 the
#: price on a tile clips, and above 1.3 the twelve-column grid wraps.
MIN_FONT_SCALE = 0.8
MAX_FONT_SCALE = 1.3

#: Vertical rhythm. `comfortable` is the spacing the pages were designed at;
#: `compact` is what a 1080p screen needs to get a full stash tab and its
#: readout on one screen without scrolling.
DENSITIES: dict[str, dict[str, str]] = {
    "comfortable": {"top": "2.6rem", "gap": "1rem", "block": ".75rem"},
    "compact": {"top": "1.1rem", "gap": ".42rem", "block": ".38rem"},
}
DENSITY_KEYS: tuple[str, ...] = tuple(DENSITIES)
DENSITY_LABELS = {"comfortable": "Comfortable", "compact": "Compact"}

#: The families offered for the page. Each ends in a generic fallback, so a
#: machine without the first name still gets the right *kind* of face rather
#: than whatever the browser defaults to.
WEB_FONTS: tuple[str, ...] = (
    "Inter, sans-serif",
    "system-ui, -apple-system, 'Segoe UI', sans-serif",
    "'IBM Plex Mono', ui-monospace, Consolas, monospace",
    "Georgia, 'Times New Roman', serif",
)

#: What the pages were designed at, as the tuple `appearance_css` compares
#: against to decide it has nothing to say. Must stay in step with the
#: `appearance.web` block in src/config.py -- one is the shipped setting and
#: the other is the shipped rendering of it.
DEFAULT_WEB_APPEARANCE = ("dark", "#b3894e", "Inter, sans-serif", 1.0, "comfortable")


def resolve_theme(theme: str) -> str:
    """The token set to draw with. "system" has to become one of the two."""
    if theme in WEB_THEMES:
        return theme
    return SYSTEM_THEME_FALLBACK


def clamp_font_scale(value: object) -> float:
    try:
        scale = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    return min(MAX_FONT_SCALE, max(MIN_FONT_SCALE, scale))


def _rgba(colour: str, alpha: float) -> str:
    """`#rrggbb` -> `rgba(r,g,b,a)`, for the one token that is translucent.

    `--brass-dim` is the accent at a third strength, used behind the selected
    segment of a radio and under the page rule. Deriving it rather than asking
    for a second colour is what keeps a custom accent looking deliberate.
    """
    text = colour.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    try:
        red, green, blue = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return f"rgba(179,137,78,{alpha})"
    return f"rgba({red},{green},{blue},{alpha})"


def appearance_css(
    theme: str = "dark",
    accent: str = "#b3894e",
    font: str = "Inter, sans-serif",
    font_scale: float = 1.0,
    density: str = "comfortable",
) -> str:
    """A `<style>` block that re-points the page's design tokens.

    It overrides the `:root` block in src/ui/common.py rather than replacing
    the stylesheet: every rule in that file is written against these tokens, so
    moving the token moves the whole page and nothing has to be restated here.
    Emitted *after* that stylesheet, which is what lets a same-specificity
    `:root` win.

    Returns "" for the shipped settings, so a default install puts exactly the
    markup on the page it always did and there is nothing to go wrong in it.
    """
    accent = accent if is_hex_colour(accent) else "#b3894e"
    scale = clamp_font_scale(font_scale)
    density = density if density in DENSITIES else "comfortable"
    if (theme, accent, font, scale, density) == DEFAULT_WEB_APPEARANCE:
        return ""

    tokens = dict(WEB_THEMES[resolve_theme(theme)])
    spacing = DENSITIES[density]

    declarations = "\n".join(f"  --{name}: {value};" for name, value in tokens.items())
    return f"""
<style>
/* Appearance overrides, written by the Settings page. See src/ui/palette.py. */
:root {{
{declarations}
  --brass: {accent};
  --brass-dim: {_rgba(accent, 0.32)};
  --font-ui: {font};
}}
html {{ font-size: {scale * 100:.1f}%; }}
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
  background: var(--ground); color: var(--ink);
}}
/* The sidebar takes its ground from Streamlit's secondaryBackgroundColor, which
   is only read at startup -- so the theme paints it here as well, and a theme
   change does not leave a panel from the old one down the left of the page. */
[data-testid="stSidebar"] {{ background: var(--panel); }}
[data-testid="stMainBlockContainer"] {{ padding-top: {spacing["top"]}; }}
[data-testid="stMain"] [data-testid="stVerticalBlock"] {{ gap: {spacing["gap"]}; }}
[data-testid="stMain"] [data-testid="stElementContainer"] {{ margin-bottom: 0; }}
[data-testid="stMain"] .panel, [data-testid="stMain"] .stat-rail {{
  margin-bottom: {spacing["block"]};
}}
</style>
"""


# ---------------------------------------------------------------------------
# Streamlit's own theme file
#
# The second half of a web theme, and the half `appearance_css` cannot do.
# Streamlit draws a good deal of the page for itself -- the dataframe canvas,
# the sidebar ground, chart defaults, every focus ring -- and none of it is
# reachable from a stylesheet the app injects. Those take their colours from
# .streamlit/config.toml, which Streamlit reads once at startup.
#
# It is built here rather than in src/ui/settings.py so that the mapping from a
# theme's tokens to Streamlit's five colour keys sits next to the tokens
# themselves, and can be checked without importing Streamlit to do it.
# ---------------------------------------------------------------------------

#: Rewritten whole rather than patched. A TOML editor is a dependency, a regex
#: over a hand-edited file is a bug waiting to happen, and this file is entirely
#: ours -- three sections, all documented in it. The header is kept because it
#: is the only place the "colour is data" rule is written for somebody who opens
#: the file directly rather than the Settings page.
STREAMLIT_CONFIG_TEMPLATE = """\
# Streamlit's own theme, so its built-in widgets start from the same ground the
# CSS in src/ui/common.py builds on. Anything Streamlit renders that CSS cannot
# reach -- the dataframe canvas, chart defaults, the widget focus colours -- gets
# its colours from here, and a mismatch between the two shows up as a pale panel
# in the middle of a dark page.
#
# The palette rule the whole design follows: colour is data. Every hue on screen
# belongs to a value band, a confidence level or an exalt verdict. Structure --
# rules, labels, borders, buttons, the active nav marker -- is slate and brass.
# `primaryColor` is therefore brass rather than a signal colour, so a button can
# never be mistaken for a price.
#
# Generated from `appearance.web` in config.yaml by the Settings page. Editing
# it by hand works -- nothing reads it back -- but the Appearance tab will
# overwrite it the next time it is used. Streamlit reads this file once, at
# startup, so a change here needs a restart.
[theme]
{theme}

[server]
headless = true

[browser]
gatherUsageStats = false
"""


def streamlit_theme_toml(
    theme: str = "dark",
    accent: str = "#b3894e",
    font: str = "Inter, sans-serif",
) -> str:
    """The whole of .streamlit/config.toml for one appearance.

    "Follow system" writes no `base` and no colours at all, and that is not an
    omission -- it is the mechanism. With nothing set, Streamlit follows the
    browser's own light/dark preference, and naming a base is exactly what would
    stop it. The accent and the face are still written, because neither of them
    is a light-or-dark decision.
    """
    accent = accent if is_hex_colour(accent) else "#b3894e"
    lines: list[str] = []
    if theme in WEB_THEMES:
        tokens = WEB_THEMES[theme]
        lines += [
            f'base = "{theme}"',
            f'backgroundColor = "{tokens["ground"]}"',
            f'secondaryBackgroundColor = "{tokens["panel"]}"',
            f'textColor = "{tokens["ink"]}"',
        ]
    else:
        lines += [
            "# Follow system: no base and no ground colour, so Streamlit takes",
            "# the browser's own light/dark preference.",
        ]
    lines.append(f'primaryColor = "{accent}"')
    lines.append(f'font = "{font}"')
    return STREAMLIT_CONFIG_TEMPLATE.format(theme="\n".join(lines))


# ---------------------------------------------------------------------------
# Persisted overrides, applied once, at import
#
# This is the only import-time side effect in the module, and it is here rather
# than at a call site because there is no call site both front ends share: the
# web app enters through Streamlit and the overlay enters through a tkinter
# process, and a colour that only one of them picked up would break the promise
# the file opens with. Reading the config file directly -- no dotenv, no
# defaults merge -- keeps that cheap and side-effect-free.
# ---------------------------------------------------------------------------
def _apply_persisted_palette() -> None:
    try:
        from src.config import read_raw_config

        stored = ((read_raw_config().get("appearance") or {}).get("palette")) or {}
        apply_palette_overrides(stored)
    except Exception:  # noqa: BLE001 - a colour must never stop the app starting
        pass


_apply_persisted_palette()
