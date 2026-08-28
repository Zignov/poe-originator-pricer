"""Streamlit entry point.

    streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.ui import prefs  # noqa: E402
from src.ui.common import page_setup, sidebar_context  # noqa: E402

#: Order matters twice over. It is the order of the nav, and the group headings
#: above Dashboard, Market Data and Model Analysis are drawn by CSS keyed on
#: position (see `.nav-group` rules in src/ui/common.py) -- "Price" what you
#: have, read the "Market", tend the "Tool". Reordering here without moving
#: those three nth-child selectors puts a heading in the wrong place.
PAGES = {
    # Price
    "Dashboard": "src.ui.dashboard",
    "Stash Pricer": "src.ui.stash_pricer",
    "Single Map": "src.ui.single_map",
    # Market
    "Market Data": "src.ui.market_data",
    "Bulk Buy": "src.ui.bulk_buy",
    # Tool
    "Model Analysis": "src.ui.model_analysis",
    "Settings": "src.ui.settings",
}


def main() -> None:
    page_setup("Originator Pricer")
    with st.sidebar:
        st.markdown(
            '<div class="brandmark"><div class="name">Originator</div>'
            '<div class="sub">Map Pricer</div></div>',
            unsafe_allow_html=True,
        )
        choice = st.radio(
            "Page",
            list(PAGES),
            label_visibility="collapsed",
            key=prefs.sticky("nav_page", "Dashboard", list(PAGES)),
        )
    sidebar_context()

    module = __import__(PAGES[choice], fromlist=["render"])
    module.render()

    # After the page, not before: `sticky` seeds a widget on the way in, and
    # this is the one place that has seen every widget on the way out. A page
    # that ends in st.rerun() never reaches here, which costs nothing -- the
    # rerun draws the same widgets again and the run after that saves them.
    prefs.flush()


if __name__ == "__main__":
    main()
