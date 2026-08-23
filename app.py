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

from src.ui.common import page_setup, sidebar_context  # noqa: E402

PAGES = {
    "Dashboard": "src.ui.dashboard",
    "Stash Pricer": "src.ui.stash_pricer",
    "Single Map": "src.ui.single_map",
    "Market Data": "src.ui.market_data",
    "Bulk Buy": "src.ui.bulk_buy",
    "Model Analysis": "src.ui.model_analysis",
    "Settings": "src.ui.settings",
}


def main() -> None:
    page_setup("Originator Pricer")
    with st.sidebar:
        st.markdown("## Originator Pricer")
        choice = st.radio("Page", list(PAGES), label_visibility="collapsed")
    sidebar_context()

    module = __import__(PAGES[choice], fromlist=["render"])
    module.render()


if __name__ == "__main__":
    main()
