"""Market Data page: collect, import, inspect and export the listing dataset."""
from __future__ import annotations

import json
from collections import Counter

import pandas as pd
import streamlit as st

from src.market.collectors.manual_import import (
    JsonExportCollector,
    ManualCsvCollector,
    csv_template,
)
from src.market.currency_source import ExchangeRateError, fetch_divine_to_chaos
from src.market.ingest import collect_from_trade_api, converter_for, refresh_outliers, store
from src.schema import FEATURE_NAMES
from src.ui.common import (
    get_config,
    get_converter,
    get_formatter,
    get_repo,
    price_histogram,
    typical_focus,
)


def render() -> None:
    cfg = get_config()
    repo = get_repo()
    converter = get_converter(cfg, repo)
    fmt = get_formatter(cfg, converter)

    st.title("Market Data")
    counts = repo.count_listings(cfg.get("league"))
    st.caption(
        f"{counts['total']} listings stored for **{cfg.get('league')}**, "
        f"{counts['outliers']} flagged as outliers, {counts['last_24h']} captured in the last 24h."
    )

    collect_tab, import_tab, inspect_tab = st.tabs(
        ["Collect", "Import", "Inspect and export"]
    )

    with collect_tab:
        _collect(cfg, repo)
    with import_tab:
        _import(cfg, repo)
    with inspect_tab:
        _inspect(cfg, repo, fmt)


def _collect(cfg, repo) -> None:
    st.markdown(
        "Collects listings from the **official pathofexile.com trade API**, obeying the "
        "rate-limit policy GGG returns in its own response headers. Nothing is scraped "
        "and no access control is bypassed. See `docs/data-access.md`."
    )
    limit = st.number_input("How many listings to collect", 50, 5000, 500, step=50)
    already = repo.count_listings(cfg.get("league"))["total"]
    if st.button("Collect from trade API", type="primary"):
        bar = st.progress(0.0, text="Starting...")
        detail = st.empty()

        def on_progress(update) -> None:
            # Collectors emit a ProgressUpdate; it stringifies to the old plain
            # message, so guard on the attribute rather than the type and the
            # page still works against a collector that only sends text.
            fraction = getattr(update, "fraction", 0.0)
            collected = getattr(update, "collected", 0)
            target = getattr(update, "target", 0)
            slice_index = getattr(update, "slice_index", 0)
            slice_count = getattr(update, "slice_count", 0)
            skipped = getattr(update, "skipped_as_bulk", 0)
            headline = (
                f"{already + collected} maps indexed  "
                f"({collected} of {target} this run"
                + (f", query {min(slice_index + 1, slice_count)}/{slice_count}" if slice_count else "")
                + (f", {skipped} skipped as bulk" if skipped else "")
                + ")"
            )
            bar.progress(fraction, text=headline)
            detail.caption(str(update))

        with st.spinner("Collecting -- this pauses when GGG's rate limit says to."):
            stats = collect_from_trade_api(
                cfg, repo, limit=int(limit), progress=on_progress
            )
        if stats.get("error"):
            bar.empty()
            st.error(stats["error"])
        else:
            total = repo.count_listings(cfg.get("league"))["total"]
            bar.progress(1.0, text=f"{total} maps indexed")
            detail.empty()
            st.success(
                f"Inserted {stats['inserted']}, refreshed {stats['updated']}, "
                f"flagged {stats.get('flagged', 0)} outliers. "
                f"{total} maps indexed in total (was {already})."
            )

    st.markdown("---")
    st.markdown("**Divine / chaos rate**")
    current = repo.get_divine_rate(cfg.get("league"))
    st.caption(
        f"Currently {current[0]:.1f} (set {current[1]:%Y-%m-%d %H:%M} UTC)"
        if current
        else f"Currently {cfg.get('currency.divine_to_chaos')} from config."
    )
    if st.button("Refresh rate from the official exchange endpoint"):
        from src.config import user_agent

        try:
            rate = fetch_divine_to_chaos(cfg.get("league"), user_agent(cfg))
        except ExchangeRateError as exc:
            st.error(str(exc))
        else:
            repo.set_divine_rate(cfg.get("league"), rate, source="official_exchange_api")
            repo.recompute_price_chaos(rate)
            st.success(f"Divine = {rate:.1f} chaos. All stored prices recomputed.")
            st.rerun()


def _import(cfg, repo) -> None:
    st.markdown(
        "If automated collection is ever unavailable or inappropriate, the app still "
        "works entirely on manual imports."
    )
    kind = st.radio("Format", ["Saved trade JSON", "CSV"], horizontal=True)

    if kind == "CSV":
        st.download_button(
            "Download CSV template", csv_template(), "originator_listings_template.csv", "text/csv"
        )
        upload = st.file_uploader("Listings CSV", type=["csv"])
        if upload is not None and st.button("Import CSV", type="primary"):
            collector = ManualCsvCollector(
                converter_for(cfg, repo), cfg.get("league"), cfg.get("scope.map_tier")
            )
            try:
                listings = list(collector.collect(text=upload.getvalue().decode("utf-8")))
            except Exception as exc:
                st.error(f"Import failed: {exc}")
                return
            _report(store(cfg, repo, listings))
    else:
        st.caption(
            "Run your own search on the trade site, save the JSON response your browser "
            "already received, and drop it here. Nothing is fetched on your behalf."
        )
        upload = st.file_uploader("Trade JSON export", type=["json"])
        if upload is not None and st.button("Import JSON", type="primary"):
            collector = JsonExportCollector(
                converter_for(cfg, repo),
                cfg.get("league"),
                cfg.get("scope.map_tier"),
                cfg.get("scope.require_uncorrupted"),
            )
            try:
                listings = list(collector.collect(payload=json.load(upload)))
            except Exception as exc:
                st.error(f"Import failed: {exc}")
                return
            _report(store(cfg, repo, listings))


def _report(stats: dict) -> None:
    st.success(
        f"Inserted {stats.get('inserted', 0)}, refreshed {stats.get('updated', 0)}, "
        f"flagged {stats.get('flagged', 0)} outliers."
    )


def _inspect(cfg, repo, fmt) -> None:
    show_outliers = st.checkbox("Include flagged outliers", value=False)
    rows = repo.listing_rows(league=cfg.get("league"), include_outliers=show_outliers)
    if not rows:
        st.info("No listings yet.")
        return

    frame = pd.DataFrame(
        [
            {
                **{name: row[name] for name in FEATURE_NAMES},
                "price": fmt.value(row["price_chaos"]) or 0.0,
                "currency": row["price_currency"],
                "type": row["price_type"],
                "indexed": row["indexed_at"],
                "outlier": bool(row["is_outlier"]),
                "reason": row["outlier_reason"],
                "source": row["source"],
            }
            for row in rows
        ]
    )

    left, right = st.columns(2)
    with left:
        st.markdown(f"**Price distribution ({fmt.unit})**")
        priced = frame[frame["price"] > 0]
        if not priced.empty:
            st.bar_chart(price_histogram(priced["price"], focus=typical_focus(cfg, fmt)), height=240)
            st.caption(f"Buckets are upper edges, in {fmt.unit}.")
    with right:
        st.markdown("**Most common reward combinations**")
        combos = Counter(
            tuple(
                name
                for name in ("more_currency", "more_maps", "more_scarabs", "more_divination_cards")
                if row[name] > 0
            )
            for _, row in frame.iterrows()
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {"combination": " + ".join(k) or "(none)", "listings": v}
                    for k, v in combos.most_common(12)
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("**Flagged outliers**")
    flagged = repo.listing_rows(league=cfg.get("league"), include_outliers=True)
    flagged_frame = pd.DataFrame(
        [
            {
                "price": fmt.value(row["price_chaos"]) or 0.0,
                "currency": row["price_currency"],
                "reason": row["outlier_reason"],
            }
            for row in flagged
            if row["is_outlier"]
        ]
    )
    if flagged_frame.empty:
        st.caption("Nothing flagged.")
    else:
        st.dataframe(
            flagged_frame["reason"].value_counts().rename("listings"),
            use_container_width=True,
        )
        if st.button("Re-run outlier detection"):
            result = refresh_outliers(cfg, repo)
            st.success(
                f"Flagged {result['flagged']} of {result['evaluated']} listings."
            )
            st.rerun()

    st.markdown("**Latest captures**")
    st.dataframe(frame.tail(25), hide_index=True, use_container_width=True)
    st.download_button(
        "Export CSV", frame.to_csv(index=False), "originator_listings.csv", "text/csv"
    )
