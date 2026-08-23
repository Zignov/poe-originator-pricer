"""The visual stash-tab representation.

Renders the tab as a grid with the estimated price drawn over each map's real
stash position, so the user can look at the app and immediately see which
physical map a price belongs to.
"""
from __future__ import annotations

from typing import Sequence

import streamlit as st

from src.market.comparables import percentile_bands, value_category
from src.pricing.display import PriceFormatter
from src.pricing.fair_value import PricedMap
from src.ui.common import VALUE_COLOURS


def render_grid(
    priced: Sequence[PricedMap],
    fmt: PriceFormatter,
    width: int = 12,
    height: int = 12,
    cell_px: int = 46,
) -> None:
    """Draw the grid. Colour bands are percentiles of this tab, not fixed values."""
    values = [
        p.estimate.fair_value_chaos for p in priced if p.supported and p.estimate is not None
    ]
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
            tooltip = _tooltip(entry, fmt)
            if not entry.supported:
                cells.append(
                    f'<div class="stash-cell unsupported" title="{tooltip}">-</div>'
                )
                continue
            price = entry.estimate.fair_value_chaos
            colour = VALUE_COLOURS[value_category(price, bands)]
            cells.append(
                f'<div class="stash-cell filled" style="background:{colour}" '
                f'title="{tooltip}">{fmt.format(price)}</div>'
            )

    st.markdown(
        '<div class="stash-grid-wrap">'
        f'<div class="stash-grid" style="grid-template-columns: repeat({width}, {cell_px}px)">'
        + "".join(cells)
        + "</div></div>",
        unsafe_allow_html=True,
    )
    _legend(bands, fmt, len(values))


#: Below this many priced maps the percentile bands are too close together to
#: label meaningfully -- every edge rounds to the same number.
MIN_FOR_BANDS = 8


def _legend(bands: dict[str, float], fmt: PriceFormatter, count: int) -> None:
    spread = (fmt.value(bands["high"]) or 0.0) - (fmt.value(bands["low"]) or 0.0)
    # One chaos apart is indistinguishable once rounded; in divine the same
    # applies at a hundredth.
    too_close = 1.0 if fmt.unit == "chaos" else 0.05

    if count < MIN_FOR_BANDS or spread < too_close:
        st.caption(
            f"Colour still ranks the {count} priced map"
            f"{'' if count == 1 else 's'} from cheapest to dearest, but with so "
            "few (or such similar) values the percentile bands are not worth "
            "labelling. Hover a tile for the actual numbers."
        )
        return

    entries = [
        ("low", f"&lt; {fmt.format(bands['low'])}"),
        ("medium", fmt.range(bands["low"], bands["medium"])),
        ("high", fmt.range(bands["medium"], bands["high"])),
        ("very high", f"&gt; {fmt.format(bands['high'])}"),
    ]
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
        "Thresholds are percentiles of this tab, not fixed league values. "
        "Hover a tile for details."
    )


def _tooltip(entry: PricedMap, fmt: PriceFormatter) -> str:
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
            f"Confidence: {est.confidence}",
        ]
    else:
        lines += ["", f"Unsupported: {item.unsupported_reason}"]
    return "&#10;".join(lines).replace('"', "'")
