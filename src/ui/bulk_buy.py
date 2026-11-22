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
    BULK_STATUS,
    BULK_STATUS_CHOICES,
    DEFAULT_MIN_LOT,
    DEFAULT_PRICE_MAX,
    BulkScan,
    summarise,
    trade_url,
)
from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.market.ingest import converter_for
from src.ui.common import (
    get_config,
    get_formatter,
    get_repo,
    hint,
    page_header,
)

#: Past this many seconds a scan is old enough that lots may already be gone.
STALE_SECONDS = 120


def render() -> None:
    cfg = get_config()
    repo = get_repo()
    fmt = get_formatter()

    page_header(
        "Market",
        "Bulk Buy",
        "Sellers listing several maps at once, <b>instant buyout only</b>, newest "
        "first rather than cheapest -- the cheap end is picked over within "
        "seconds, so the newest lots inside your price are the ones still there.",
    )
    hint(
        # `hint` emits raw HTML, so Markdown never runs here -- tags, not
        # asterisks. The lede on `page_header` is the same.
        "Only listings you can buy outright are shown — GGG's <b>Instant Buyout</b> "
        "status. Lots that need a whisper and a reply are left out: a cheap lot "
        "you cannot take right now is not a lot you can buy; widen "
        "<i>Who can you buy from</i> if the table comes back empty.<br>"
        "Nothing on this page is stored and none of it reaches the model: these "
        "are unrolled maps in flat-priced blocks, which is exactly the data "
        "training is protected from."
    )

    _live_search_callout(
        cfg,
        price_max=st.session_state.get("bulk_price_max", DEFAULT_PRICE_MAX),
        status=BULK_STATUS_CHOICES.get(
            st.session_state.get("bulk_status_label", ""), BULK_STATUS
        ),
    )

    controls = st.columns([0.9, 0.9, 1.3, 1.5, 1.1])
    price_max = controls[0].number_input(
        "Max chaos per map", 1.0, 500.0, float(DEFAULT_PRICE_MAX), step=1.0,
        key="bulk_price_max",
    )
    min_lot = controls[1].number_input("Minimum maps in a lot", 2, 60, DEFAULT_MIN_LOT)
    seller = controls[2].text_input("Re-check one seller", placeholder="Account#1234")
    status_label = controls[3].selectbox(
        "Who can you buy from",
        list(BULK_STATUS_CHOICES),
        key="bulk_status_label",
        help="Instant buyout is what you can take without whispering anyone. "
        "It is a much smaller set -- one live check found 5 instant-buyout "
        "listings against 98 for everyone -- so widen this if the table is "
        "empty and you are willing to whisper.",
    )
    status = BULK_STATUS_CHOICES[status_label]
    controls[4].markdown("&nbsp;", unsafe_allow_html=True)

    job = st.session_state.get("bulk_scan_job")
    if job is not None and job.running:
        # The scan owns the worker thread, not this one, so the button below is
        # reachable. Drawn in place of "Scan now" because starting a second
        # scan while one is in flight would spend the rate limit twice to
        # answer the same question.
        controls[4].button("Scan now", width="stretch", disabled=True)
        _live_scan_panel()
        return

    scan_now = controls[4].button("Scan now", type="primary", width="stretch")
    if job is not None:
        # The scan finished between reruns. Collect it and retire the handle.
        if job.error:
            st.session_state["bulk_scan_error"] = job.error
        elif job.scan is not None:
            st.session_state["bulk_scan"] = job.scan
        st.session_state.pop("bulk_scan_job", None)

    if scan_now:
        _start_scan(cfg, repo, price_max, int(min_lot), seller.strip() or None, status)
        # Straight back round, so the Cancel button is on screen before the
        # first request goes out rather than after the scan ends.
        st.rerun()

    if error := st.session_state.pop("bulk_scan_error", ""):
        st.error(error)

    scan: BulkScan | None = st.session_state.get("bulk_scan")
    if scan is None:
        st.info("Run a scan to see what is listed right now.")
        return

    _render_scan(scan, fmt)


def _live_search_callout(cfg, price_max: float, status: str = BULK_STATUS) -> None:
    """Point at GGG's own live search, which is the fast path.

    A scan is a snapshot, and lots go in under a minute -- one seller went from
    51 maps to 0 in about five while this page was being built. Live search fixes
    that properly by pushing new listings the moment they appear, but it runs on
    an endpoint GGG does not document, over an authenticated connection, and
    continuously. Doing that from here would be reverse-engineering an
    undocumented endpoint and running exactly the kind of automated monitoring
    the Terms of Use prohibit -- with the account risk landing on the user.
    GGG's own page has the button, so this hands the query over instead.
    """
    url = live_search_url(cfg, price_max, status)
    with st.container():
        st.markdown(
            f'<div class="warn"><b>Faster than scanning:</b> open '
            f'<a href="{url}" target="_blank">this search on the trade site</a> and '
            "press <b>Activate Live Search</b>. GGG pushes each new listing the "
            "instant it appears, and the buy and <b>Travel to Hideout</b> buttons "
            "are on the same row — no scanning, no round trip through this app. "
            "Live search is not built in here on purpose: it is an undocumented "
            "endpoint and a standing authenticated connection, and the account "
            "risk would be yours.</div>",
            unsafe_allow_html=True,
        )


def live_search_url(cfg, price_max: float, status: str = BULK_STATUS) -> str:
    """The scan's own query, ready for GGG's live search."""
    collector = OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=None,
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    query = collector.build_query(
        sort_by="indexed", sort="desc", collapse=False, price_max=float(price_max),
        # Whatever the page is set to, not the default: the link has to open
        # the same search whose results are on screen, or the count on GGG's
        # page disagrees with ours and neither can be trusted.
        status=status,
        exclude_other_influence=True,
    )
    return trade_url(cfg.get("league"), query)


#: How often the live scan panel redraws while a scan is in flight. Fast
#: enough that Cancel feels like a button rather than a form submission.
LIVE_REFRESH_SECONDS = 1.0


def _start_scan(
    cfg, repo, price_max: float, min_lot: int, seller: str | None,
    status: str = BULK_STATUS,
) -> None:
    """Begin a scan on a worker thread and keep the handle.

    Threaded rather than inline so the Cancel button below is reachable --
    Streamlit runs one script at a time per session, and a scan running on that
    thread means nothing can read a click until it is over. See
    src/market/run_job.py.
    """
    from src.market.cancel import CancelToken
    from src.market.run_job import BulkScanJob

    collector = OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=converter_for(cfg, repo),
        min_interval=cfg.get("http.min_request_interval_seconds", 2.0),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        # The token goes to the collector, not just the job, so a cancel lands
        # inside a rate-limit sleep instead of only between batches. Those
        # sleeps are where the minutes actually go.
        cancel=CancelToken(),
    )
    ok, why = collector.available()
    if not ok:
        st.session_state["bulk_scan_error"] = why
        return

    st.session_state.pop("bulk_scan_error", None)
    st.session_state["bulk_scan_job"] = BulkScanJob.start(
        collector, price_max=price_max, min_lot=min_lot, seller=seller, status=status
    )


@st.fragment(run_every=LIVE_REFRESH_SECONDS)
def _live_scan_panel() -> None:
    """Progress and a Cancel button, redrawn while the scan runs.

    A fragment rather than the whole page: this redraws two lines once a
    second, and the page around it has a results table on it.
    """
    job = st.session_state.get("bulk_scan_job")
    if job is None:
        return

    if not job.running:
        # Finished between frames. Ask for a full rerun so the page outside
        # this fragment can collect the result and draw it.
        st.rerun(scope="app")
        return

    left, right = st.columns([1, 3])
    if left.button("Cancel scan", width="stretch", disabled=job.cancelling):
        job.cancel()
        st.rerun(scope="fragment")

    if job.cancelling:
        # Honest about what a cancel can do: an in-flight request and a
        # rate-limit sleep both have to end first.
        right.caption("Cancelling -- finishing the current request...")
    else:
        right.caption(job.message or "Reading the newest listings...")


def _render_scan(scan: BulkScan, fmt) -> None:
    if scan.error:
        st.error(scan.error)
        return

    if scan.cancelled:
        # Shown above the results, not instead of them: the lots below are
        # real, they are just not everything that was listed. Saying only
        # "cancelled" would throw away requests the user has already paid for.
        st.markdown(
            '<div class="warn"><b>Scan cancelled.</b> Everything read before '
            "you stopped it is below — it is a partial view of the market, not "
            "the whole of it. Scan again for the rest.</div>",
            unsafe_allow_html=True,
        )
        if not scan.lots:
            st.info("Nothing had been read yet when the scan was cancelled.")
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

    if scan.query:
        st.markdown(
            f"[Open this whole search on the trade site]"
            f"({trade_url(scan.league, scan.query)}) — buying happens there, with "
            "GGG's own purchase and **Travel to Hideout** controls."
        )

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
        width="stretch",
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
        width="stretch",
        column_config={
            "Price": st.column_config.NumberColumn(
                f"Price ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Listed (min ago)": st.column_config.NumberColumn(
                "Listed (min ago)", format="%.0f"
            ),
        },
    )
    if scan.query:
        st.markdown(
            f"### [Buy this lot on the trade site]"
            f"({trade_url(scan.league, scan.query, seller=lot.seller)})"
        )
        st.caption(
            f"Opens {lot.seller}'s maps under {scan.price_max:g}c on GGG's own page, "
            "where the purchase and **Travel to Hideout** buttons live. Costs no "
            "request from here — the query travels in the link."
        )

    st.caption(
        f"Tabs: {', '.join(lot.stash_tabs()) or 'unknown'}. Put this seller's name in "
        "**Re-check one seller** above to refresh just this lot — that is a single "
        "query rather than a sweep, so it comes back in a second or two."
    )

    with st.expander("Paste into chat instead"):
        st.caption(
            "GGG's own message for each listing, to paste in game yourself. This app "
            "never sends anything to the client — it only puts the text in front of you."
        )
        messages = [x.whisper for x in lot.listings if x.whisper]
        st.code(
            "\n".join(messages)
            or "This scan returned no message text for that seller.",
            language="text",
        )
