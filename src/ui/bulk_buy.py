"""Bulk Buy: who is selling a lot of maps cheaply, right now.

Deliberately its own page rather than a Market Data tab. Market Data collects the
dataset the model trains on, and everything here is the opposite of that -- it
wants the flat-priced tab dumps that collection works hard to keep out. Keeping
the two apart on screen keeps the distinction visible, and nothing found here is
ever written to the database.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.config import user_agent
from src.market.bulk import (
    DEFAULT_MIN_LOT,
    DEFAULT_PRICE_MAX,
    BulkScan,
    scan_bulk_lots,
    summarise,
)
from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.market.ingest import converter_for
from src.ui.common import get_config, get_formatter, get_repo

#: Past this many seconds a scan is old enough that lots may already be gone.
STALE_SECONDS = 120


def render() -> None:
    cfg = get_config()
    repo = get_repo()
    fmt = get_formatter()

    st.title("Bulk Buy")
    st.caption(
        "Sellers listing several maps at once, sorted by what was listed most "
        "recently rather than what is cheapest — the cheapest end of the market is "
        "picked over within seconds, so the newest listings inside a price you would "
        "buy at are the ones still available. Nothing here is stored, and none of it "
        "reaches the model: these are unrolled maps in flat-priced blocks, which is "
        "exactly the data training is protected from."
    )

    controls = st.columns([1, 1, 1, 1.3])
    price_max = controls[0].number_input(
        "Max chaos per map", 1.0, 500.0, float(DEFAULT_PRICE_MAX), step=1.0
    )
    min_lot = controls[1].number_input("Minimum maps in a lot", 2, 60, DEFAULT_MIN_LOT)
    seller = controls[2].text_input("Re-check one seller", placeholder="Account#1234")
    controls[3].markdown("&nbsp;", unsafe_allow_html=True)
    scan_now = controls[3].button("Scan now", type="primary", use_container_width=True)

    if scan_now:
        _run_scan(cfg, repo, price_max, int(min_lot), seller.strip() or None)

    scan: BulkScan | None = st.session_state.get("bulk_scan")
    if scan is None:
        st.info("Run a scan to see what is listed right now.")
        return

    _render_scan(scan, fmt)


def _run_scan(cfg, repo, price_max: float, min_lot: int, seller: str | None) -> None:
    collector = OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=converter_for(cfg, repo),
        min_interval=cfg.get("http.min_request_interval_seconds", 2.0),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    ok, why = collector.available()
    if not ok:
        st.error(why)
        return

    line = st.empty()
    with st.spinner("Reading the newest listings..."):
        scan = scan_bulk_lots(
            collector,
            price_max=price_max,
            min_lot=min_lot,
            seller=seller,
            progress=lambda m: line.caption(str(m)),
        )
    line.empty()
    st.session_state["bulk_scan"] = scan


def _render_scan(scan: BulkScan, fmt) -> None:
    if scan.error:
        st.error(scan.error)
        return

    age = scan.age_seconds()
    stamp = scan.scanned_at.astimezone().strftime("%H:%M:%S")
    headline = (
        f"{scan.listings_seen} listings from {scan.sellers_seen} sellers under "
        f"{scan.price_max:g}c  ·  {scan.total_matching} matching in total  ·  "
        f"scanned {stamp} ({age:.0f}s ago)"
    )
    if age > STALE_SECONDS:
        st.markdown(
            f'<div class="warn">{headline} — old enough that the cheaper lots have '
            "probably gone. Scan again.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.success(headline)

    if not scan.lots:
        st.info(
            "No seller had that many maps in the newest listings. Try a lower "
            "minimum lot size, or a higher price ceiling."
        )
        return

    frame = pd.DataFrame(summarise(scan, limit=25))
    digits = 0 if fmt.unit == "chaos" else 2
    for column in ("Each", "Cheapest", "Dearest", "Lot total"):
        frame[column] = frame[column].map(fmt.value)
    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Maps": st.column_config.NumberColumn("Maps", format="%d"),
            "Each": st.column_config.NumberColumn(
                f"Each ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Cheapest": st.column_config.NumberColumn(
                f"Cheapest ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Dearest": st.column_config.NumberColumn(
                f"Dearest ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Lot total": st.column_config.NumberColumn(
                f"Lot total ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Flat priced": st.column_config.CheckboxColumn(
                "Flat", help="One price across the whole lot — a tab dump."
            ),
            "Newest": st.column_config.NumberColumn(
                "Newest (min)", format="%.0f", help="Age of the freshest listing in the lot."
            ),
        },
    )
    st.caption(
        "**Flat** marks a lot listed at a single price, which usually means a tab "
        "dumped without being looked through. **Newest** is how long ago the "
        "freshest listing in that lot appeared."
    )

    st.markdown("**Lot detail**")
    labels = {
        f"{lot.seller} — {lot.count} maps at {fmt.format(lot.median_chaos)} each": lot
        for lot in scan.lots
    }
    chosen = st.selectbox("Seller", list(labels), label_visibility="collapsed")
    lot = labels[chosen]
    now = datetime.now(timezone.utc)
    detail = pd.DataFrame(
        [
            {
                "Price": fmt.value(x.price_chaos),
                "Currency": x.price_currency,
                "Tab": x.stash_name,
                "Position": f"{x.x},{x.y}" if x.x is not None else "",
                "Listed (min ago)": x.age_minutes(now),
            }
            for x in sorted(lot.listings, key=lambda x: x.price_chaos or 0)
        ]
    )
    st.dataframe(
        detail,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Price": st.column_config.NumberColumn(
                f"Price ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Listed (min ago)": st.column_config.NumberColumn(
                "Listed (min ago)", format="%.0f"
            ),
        },
    )
    st.caption(
        f"Tabs: {', '.join(lot.stash_tabs()) or 'unknown'}. Put this seller's name in "
        "**Re-check one seller** above to refresh just this lot — that is a single "
        "query rather than a sweep, so it comes back in a second or two."
    )
