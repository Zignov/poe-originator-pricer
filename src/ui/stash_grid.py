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
from src.overlay.state import tile_price
from src.pricing.display import PriceFormatter
from src.pricing.exalt import ExaltVerdict
from src.pricing.fair_value import PricedMap
from src.ui.common import VALUE_COLOURS
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
    for y in range(height):
        for x in range(width):
            entry = occupied.get((x, y))
            if entry is None:
                cells.append('<div class="stash-cell"></div>')
                continue
            tooltip = _tooltip(entry, fmt, (verdicts or {}).get(id(entry)))
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
            cells.append(
                f'<div class="stash-cell filled{dim} {ring}" style="background:{colour}" '
                f'title="{tooltip}">{fmt.format(price)}'
                f"{confidence_meter(entry)}</div>"
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


def _tooltip(entry: PricedMap, fmt: PriceFormatter, verdict: ExaltVerdict | None = None) -> str:
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
