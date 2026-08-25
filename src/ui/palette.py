"""The value-band palette, kept where a process without Streamlit can read it.

These five colours used to live in ``src/ui/common.py``. They moved here when
the desktop overlay started drawing the same percentile bands with tkinter, in
its own process: importing the page helpers to get five hex strings would have
pulled Streamlit, the database and the pricing service into a process that never
renders a web page. ``src.ui.common`` re-exports the name, so every existing
import site is unchanged and there is still exactly one definition of what
"high" looks like.
"""
from __future__ import annotations

#: Keyed by the strings src.market.comparables.value_category returns.
VALUE_COLOURS = {
    "low": "#5b6470",
    "medium": "#2f7d5c",
    "high": "#a8791f",
    "very high": "#9b3d8a",
    "unknown": "#3a3f47",
}
