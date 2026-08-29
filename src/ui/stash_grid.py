"""The visual stash-tab representation.

Renders the tab as a grid with the price drawn over each map's real stash
position, so the user can look at the app and immediately see which physical map
a price belongs to.

The number on a tile is the **suggested listing price**, not the fair-value
estimate. Those differ by the selling strategy, and drawing fair value here made
the strategy selector look broken: every other number on the page moved when it
changed and the grid -- the thing people actually read -- did not. Fair value is
still one line down in the tooltip.
"""
from __future__ import annotations

from typing import Sequence

import streamlit as st

from src.market.comparables import percentile_bands, value_category
from src.overlay.state import free_affix_slots, tile_price
from src.pricing.display import PriceFormatter
from src.pricing.exalt import ExaltVerdict
from src.pricing.fair_value import PricedMap
from src.ui.common import VALUE_COLOURS
from src.ui.exalt_panel import affixes_for
from src.ui.palette import (
    CONFIDENCE_COLOURS,
    NEGLIGIBLE_CHAOS,
    VALUE_ORDER,
    confidence_fill,
)


def render_grid(
    priced: Sequence[PricedMap],
    fmt: PriceFormatter,
    width: int = 12,
    height: int = 12,
    cell_px: int = 46,
    verdicts: dict[int, ExaltVerdict] | None = None,
) -> None:
    """Draw the grid. Colour bands are percentiles of this tab, not fixed values."""
    values = [_tile_price(p) for p in priced if p.supported and p.estimate is not None]
    bands = percentile_bands(values)

    occupied: dict[tuple[int, int], PricedMap] = {}
    for entry in priced:
        if entry.item.x is None or entry.item.y is None:
            continue
        occupied[(entry.item.x, entry.item.y)] = entry

    max_x = max((x for x, _ in occupied), default=0) + 1
    max_y = max((y for _, y in occupied), default=0) + 1
    width = max(width, max_x)
    height = max(height, max_y)

    cells: list[str] = []
    badges = 0
    for y in range(height):
        for x in range(width):
            entry = occupied.get((x, y))
            if entry is None:
                cells.append('<div class="stash-cell"></div>')
                continue
            # Read once and handed to both the badge and the tooltip: resolving
            # a stash payload's modifier text against the catalogue is the
            # expensive part of drawing a tile, and a quad tab draws 576 of them.
            free = free_affix_slots(affixes_for(entry.item))
            tooltip = _tooltip(entry, fmt, (verdicts or {}).get(id(entry)), free)
            if not entry.supported:
                cells.append(
                    f'<div class="stash-cell unsupported" title="{tooltip}">-</div>'
                )
                continue
            price = _tile_price(entry)
            category = value_category(price, bands)
            colour = VALUE_COLOURS[category]
            ring = exalt_class((verdicts or {}).get(id(entry)))
            # The negligible band gets a class as well as a colour: the fill
            # alone recedes, but the white price on top of it does not, and the
            # point of the band is that the eye skips the whole tile.
            dim = " negligible" if category == "negligible" else ""
            badge = free_affix_badge(free)
            badges += bool(badge)
            cells.append(
                f'<div class="stash-cell filled{dim} {ring}" style="background:{colour}" '
                f'title="{tooltip}">{fmt.format(price)}'
                f"{confidence_meter(entry)}{badge}</div>"
            )

    st.markdown(
        '<div class="stash-grid-wrap">'
        f'<div class="stash-grid" style="grid-template-columns: repeat({width}, {cell_px}px)">'
        + "".join(cells)
        + "</div></div>",
        unsafe_allow_html=True,
    )
    _legend(bands, fmt, len(values))
    if verdicts:
        _exalt_legend()
    if badges:
        _free_affix_legend()
    _confidence_legend()


#: Ring drawn round a tile whose map is worth an Exalted Orb, and round one that
#: can take an orb but would not repay it. A map that cannot be exalted at all --
#: full, corrupted, or with unreadable affixes -- gets neither, because an
#: outline there would invite spending currency on something that cannot use it.
EXALT_YES_CLASS = "exalt-worth"
EXALT_NO_CLASS = "exalt-skip"


def exalt_class(verdict: ExaltVerdict | None) -> str:
    """Which ring, if any, belongs on this tile."""
    if verdict is None or not verdict.available:
        return ""
    return EXALT_YES_CLASS if (verdict.net_gain_chaos or 0.0) > 0 else EXALT_NO_CLASS


#: The badge's geometry, written as inline style rather than as a rule in the
#: page stylesheet. Everything it needs is four numbers and a colour, and the
#: alternative -- a `.free-affix` block over in src/ui/common.py -- would put the
#: two halves of one small feature in two files that are edited for unrelated
#: reasons. The class name is still on the element, because that is what the
#: grid's tests count and what a browser inspector reads.
#:
#: Top-right, inset 4px like the meter's 4px, which is what clears the 2px exalt
#: outline. The dots are the same white on every band, including the near-black
#: negligible one: the count is structure rather than a value judgement, so it
#: has to read the same on a 12c map as on a 900c one.
_BADGE_STYLE = (
    "position:absolute;top:4px;right:4px;display:flex;gap:2px;"
    "pointer-events:none"
)
_DOT_STYLE = "width:4px;height:4px;border-radius:50%;background:#fff"

#: Six dots at 4px plus five 2px gaps is 34px, inside a 46px cell with its
#: insets. Past that the badge would run under the price, so it stops -- see
#: DOTS_MAX in src/overlay/layout.py for why six is the right place to stop.
FREE_AFFIX_MAX_DOTS = 6


def free_affix_badge(free: int | None) -> str:
    """One white dot per open affix slot, or nothing at all.

    `None` and `0` both draw nothing, and they mean different things: `None` is
    "this payload carries no affix structure" and `0` is "this map is full".
    That they look the same is deliberate and safe in this direction -- neither
    invites an Exalted Orb -- and it is the *opposite* collapse, drawing zero
    dots for an unreadable map, that would be a lie. `free_affix_slots` in
    src/overlay/state.py is what keeps the two apart on the way in.
    """
    if not free or free <= 0:
        return ""
    dots = "".join(
        f'<span style="{_DOT_STYLE}"></span>'
        for _ in range(min(int(free), FREE_AFFIX_MAX_DOTS))
    )
    plural = "" if free == 1 else "s"
    return f'<span class="free-affix" style="{_BADGE_STYLE}" title="{free} free affix slot{plural}">{dots}</span>'


def confidence_meter(entry: PricedMap) -> str:
    """The little bar along the bottom of a tile: how much to trust the number.

    Empty string for a map with no estimate. The colour and the length come from
    ``src.ui.palette``, the same function the desktop overlay calls, so the tile
    that is a third full over the game is a third full here.
    """
    est = entry.estimate
    if not entry.supported or est is None:
        return ""
    filling = confidence_fill(est.confidence, est.confidence_score)
    if filling is None:
        return ""
    colour, fraction = filling
    return (
        '<span class="conf-meter">'
        f'<span style="width:{fraction * 100:.0f}%;background:{colour}"></span>'
        "</span>"
    )


def _confidence_legend() -> None:
    """Key for the meter.

    Its own row rather than a fourth entry on the value legend: those swatches
    are what a *price* looks like, and folding a confidence bar in among them
    invites reading the meter's colour as another value band.
    """
    swatches = "".join(
        f'<div><span class="conf-meter legend-meter">'
        f'<span style="width:{width};background:{CONFIDENCE_COLOURS[level]}"></span>'
        f"</span>{level.lower()} confidence</div>"
        for level, width in (("High", "100%"), ("Medium", "58%"), ("Low", "25%"))
    )
    st.markdown(f'<div class="legend">{swatches}</div>', unsafe_allow_html=True)
    st.caption(
        "The bar along the bottom of a tile is how much the market actually "
        "tells us about that map -- how many close comparables there are, how "
        "recent they are, and how wide the range is. A short bar means the "
        "number is a rough estimate, not a price."
    )


def _tile_price(entry: PricedMap) -> float:
    """What a tile shows: the suggested listing price for the chosen strategy.

    Falls back to fair value if no suggestion was produced, so a tile never goes
    blank because a strategy multiplier was missing.

    The rule itself lives in src/overlay/state.py, which is the Streamlit-free
    module the desktop overlay reads. Two copies of it would eventually drift,
    and the one thing the overlay must never do is show a different number from
    the tile it is mirroring.
    """
    return tile_price(entry)


#: Below this many priced maps the percentile bands are too close together to
#: label meaningfully -- every edge rounds to the same number.
MIN_FOR_BANDS = 8


def _exalt_legend() -> None:
    """Key for the two rings.

    Rendered separately from the value bands: those are suppressed on a tab with
    too few maps to make percentiles meaningful, and the rings are still drawn
    there, so folding this into that block left them unexplained.
    """
    st.markdown(
        '<div class="legend">'
        '<div><span class="swatch" style="background:transparent;'
        'outline:2px solid #39d0d8;outline-offset:-2px"></span>worth an exalt</div>'
        '<div><span class="swatch" style="background:transparent;'
        'outline:2px dashed rgba(220,220,225,.5);outline-offset:-2px"></span>'
        "not worth the orb</div>"
        '<div><span class="swatch" style="background:transparent;'
        'border:1px solid rgba(128,128,128,.35)"></span>cannot be exalted</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _free_affix_legend() -> None:
    """Key for the dots, drawn only on a tab that actually has some.

    Conditional, unlike the value bands: most stash payloads carry no affix
    structure at all -- see `_no_matches_reason` in the Stash Pricer page -- so
    on those tabs every tile is badgeless and a key explaining a mark nobody can
    see is one more thing to read past.
    """
    swatch = (
        '<span style="display:inline-flex;gap:2px;vertical-align:1px;'
        'margin-right:.35rem">'
        + f'<span style="{_DOT_STYLE}"></span>' * 2
        + "</span>"
    )
    st.markdown(
        f'<div class="legend"><div>{swatch}free affix slots</div></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "One dot per affix slot the map still has open, in the tile's top-right "
        "corner -- two dots, two slots. No dots means either that the map is "
        "full or that this payload carries no affix structure to read; the "
        "tooltip says which."
    )


def _legend(bands: dict[str, float], fmt: PriceFormatter, count: int) -> None:
    spread = (fmt.value(bands["exceptional"]) or 0.0) - (
        fmt.value(bands["modest"]) or 0.0
    )
    # One chaos apart is indistinguishable once rounded; in divine the same
    # applies at a hundredth.
    too_close = 1.0 if fmt.unit == "chaos" else 0.05

    if count < MIN_FOR_BANDS or spread < too_close:
        st.caption(
            f"Colour still ranks the {count} priced map"
            f"{'' if count == 1 else 's'} from cheapest to dearest -- and anything "
            f"under {fmt.format(NEGLIGIBLE_CHAOS)} is still greyed out -- but with "
            "so few (or such similar) values the percentile bands are not worth "
            "labelling. Hover a tile for the actual numbers."
        )
        return

    # Built from VALUE_ORDER so the legend cannot drift out of step with the
    # bands: each entry runs from the threshold that opens it to the one that
    # opens the next, with the two ends written as open-ended.
    edges = [NEGLIGIBLE_CHAOS] + [bands[name] for name in VALUE_ORDER[2:]]
    entries = [("negligible", f"&lt; {fmt.format(NEGLIGIBLE_CHAOS)}")]
    for index, key in enumerate(VALUE_ORDER[1:-1]):
        entries.append((key, fmt.range(edges[index], edges[index + 1])))
    entries.append(
        (VALUE_ORDER[-1], f"&gt; {fmt.format(bands[VALUE_ORDER[-1]])}")
    )

    html = ['<div class="legend">']
    for key, text in entries:
        html.append(
            f'<div><span class="swatch" style="background:{VALUE_COLOURS[key]}"></span>'
            f"{key} &nbsp;<span style='opacity:.6'>{text}</span></div>"
        )
    html.append(
        '<div><span class="swatch" style="background:rgba(128,128,128,.25)"></span>'
        "unsupported / empty</div>"
    )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)
    st.caption(
        f"Anything under {fmt.format(NEGLIGIBLE_CHAOS)} is greyed out on a fixed "
        "threshold; the bands above it are percentiles of this tab's remaining "
        "maps, not fixed league values. Hover a tile for details."
    )


def _tooltip(
    entry: PricedMap,
    fmt: PriceFormatter,
    verdict: ExaltVerdict | None = None,
    free: int | None = None,
) -> str:
    item = entry.item
    lines = [
        f"{item.position_label()}  {item.name or item.base_type}",
        f"Quantity: {item.quantity}",
        f"Rarity: {item.rarity}",
        f"Pack: {item.pack_size}",
        f"Currency: {item.more_currency}",
        f"Maps: {item.more_maps}",
        f"Scarabs: {item.more_scarabs}",
        f"Div Cards: {item.more_divination_cards}",
    ]
    if entry.supported and entry.estimate:
        est = entry.estimate
        lines += [
            "",
            f"Fair value: {fmt.format(est.fair_value_chaos)}",
            f"Suggested: {fmt.format(entry.suggested_chaos)}",
            f"Range: {fmt.range(est.interval_low_chaos, est.interval_high_chaos)}",
            f"Confidence: {est.confidence} ({est.confidence_score:.2f})",
        ]
    else:
        lines += ["", f"Unsupported: {item.unsupported_reason}"]

    # Spelled out in words because the dots cannot distinguish the two reasons a
    # tile has none of them, and this is the only place that can.
    lines.append(
        "Free affix slots: unknown (no affix data in this payload)"
        if free is None
        else f"Free affix slots: {free}"
    )

    if verdict is not None:
        lines.append("")
        if not verdict.available:
            lines.append(f"Exalt: {verdict.reason}")
        else:
            lines.append(
                f"Exalt: {verdict.affix_label}, "
                + (
                    f"worth it (+{fmt.format(verdict.net_gain_chaos)} net)"
                    if (verdict.net_gain_chaos or 0) > 0
                    else f"not worth it ({fmt.format(verdict.net_gain_chaos)} net)"
                )
            )
    return "&#10;".join(lines).replace('"', "'")
