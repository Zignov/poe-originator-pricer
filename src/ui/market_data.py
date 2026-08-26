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
    listing_rows_to_csv,
)
from src.market.currency_source import ExchangeRateError, fetch_rate_quote
from src.market.ingest import collect_from_trade_api, converter_for, refresh_outliers, store
from src.schema import FEATURE_NAMES
from src.ui.common import (
    CHART_SERIES,
    flash,
    get_config,
    get_converter,
    get_exalt_cost,
    get_formatter,
    get_repo,
    invalidate_model_cache,
    page_header,
    price_histogram,
    show_flash,
    stat_rail,
    typical_focus,
)


def render() -> None:
    cfg = get_config()
    repo = get_repo()
    converter = get_converter(cfg, repo)
    fmt = get_formatter(cfg, converter)

    page_header(
        "Market",
        "Market Data",
        "The listings every price is fitted to. Collect them, import them, or "
        "look at what is already stored.",
    )
    show_flash()
    counts = repo.count_listings(cfg.get("league"))
    stat_rail(
        [
            ("Stored", f"{counts['total']:,}", cfg.get("league")),
            ("Flagged", f"{counts['outliers']:,}", "outliers"),
            ("Last 24h", f"{counts['last_24h']:,}", "captured"),
        ]
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

    buttons = st.columns(2)
    collect_only = buttons[0].button("Collect from trade API")
    # The pairing exists because collecting on its own changes no price. The
    # model is a separate artefact and nothing retrains it on a timer, so
    # "collect" without "retrain" leaves the app showing the old numbers over
    # newer data -- which is exactly the state that is impossible to notice.
    collect_and_train = buttons[1].button(
        "Collect and retrain", type="primary",
        help="Collect, re-run outlier detection, then retrain on the result.",
    )

    if collect_only or collect_and_train:
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
            return

        total = repo.count_listings(cfg.get("league"))["total"]
        bar.progress(1.0, text=f"{total} maps indexed")
        detail.empty()
        st.success(
            f"Inserted {stats['inserted']}, refreshed {stats['updated']}, "
            f"flagged {stats.get('flagged', 0)} outliers. "
            f"{total} maps indexed in total (was {already})."
        )
        if collect_and_train:
            _retrain(cfg)

    st.markdown("---")
    _orb_prices(cfg, repo)


def _retrain(cfg) -> None:
    """Train on what is in the database now, streaming the log to the page.

    Deliberately in-line rather than a background job: training takes seconds,
    and a button that returns instantly while the model quietly changes
    underneath the other pages is worse than one that makes you wait.
    """
    from src.ml.train import NotEnoughData, train

    log_area = st.empty()
    lines: list[str] = []

    def log(message: str) -> None:
        lines.append(str(message))
        log_area.code("\n".join(lines), language="text")

    with st.spinner("Retraining on the collected data..."):
        try:
            train(cfg, log=log)
        except NotEnoughData as exc:
            st.error(f"Collected, but not retrained: {exc}")
            return
    invalidate_model_cache()
    # Same reason as the Retrain button in Settings: everything above this point
    # was rendered from the outgoing model, so the message has to survive a
    # rerun rather than be printed into a page that is already out of date.
    flash(
        "success",
        "Retrained on the newly collected data. Every page now prices with the "
        "new model.",
    )
    st.rerun()


def _chaos(value: float) -> str:
    """A chaos price at a precision that suits its size.

    One decimal is right for a 211-chaos Divine Orb and wrong for a 1.25-chaos
    Exalted Orb, where it renders as "1.2c" and quietly loses four percent of
    the number the exalt verdict divides by.
    """
    value = float(value or 0.0)
    return f"{value:.2f}c" if value < 10 else f"{value:.1f}c"


def _orb_prices(cfg, repo) -> None:
    """Both orb prices, side by side and stated the same way.

    Divine used to be shown as a rate and Exalted as a bare number, which made
    them look like different kinds of thing when they are the same measurement
    of the same market. Both now read "1 orb = N chaos", both show when they were
    set, and both report the volume behind the number.
    """
    from src.config import user_agent

    st.markdown("**Orb prices in chaos**")
    st.caption(
        f"Read from [poe.ninja](https://poe.ninja/poe1/economy/{cfg.get('league')}/currency) "
        f"for **{cfg.get('league')}**, which aggregates the in-game Currency "
        "Exchange over a window rather than sampling a few dozen live listings. "
        "Change the league on the Settings page."
    )

    divine_col, exalt_col = st.columns(2)

    with divine_col:
        stored = repo.get_divine_rate(cfg.get("league"))
        st.metric(
            "1 Divine Orb",
            _chaos(stored[0] if stored else cfg.get("currency.divine_to_chaos")),
        )
        st.caption(
            f"Set {stored[1]:%Y-%m-%d %H:%M} UTC"
            if stored
            else "From config — never refreshed."
        )
        if st.button("Refresh Divine", use_container_width=True):
            try:
                quote = fetch_rate_quote(cfg.get("league"), user_agent(cfg), "divine")
            except ExchangeRateError as exc:
                st.error(str(exc))
            else:
                repo.set_divine_rate(
                    cfg.get("league"), quote.chaos, source="poe_ninja_exchange"
                )
                repo.recompute_price_chaos(quote.chaos)
                flash("success", f"{quote.summary()}. All stored prices recomputed.")
                st.rerun()
        st.caption("Every stored price is recomputed through this rate.")

    with exalt_col:
        st.metric("1 Exalted Orb", _chaos(get_exalt_cost()))
        stamp = repo.get_setting_time("exalted_to_chaos")
        st.caption(
            f"Set {stamp:%Y-%m-%d %H:%M} UTC" if stamp else "From config — never refreshed."
        )
        if st.button("Refresh Exalted", use_container_width=True):
            try:
                quote = fetch_rate_quote(cfg.get("league"), user_agent(cfg), "exalted")
            except ExchangeRateError as exc:
                st.error(str(exc))
            else:
                # Stored, not session-scoped: the divine rate beside it persists,
                # and a price that silently reverted to the config default on
                # restart is exactly what made this button look broken.
                repo.set_setting("exalted_to_chaos", quote.chaos)
                flash("success", f"{quote.summary()}. Saved.")
                st.rerun()
        st.caption(
            "The whole right-hand side of the exalt-worthy verdict on the Stash Pricer."
        )


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
            st.bar_chart(
                price_histogram(priced["price"], focus=typical_focus(cfg, fmt)),
                height=240,
                color=CHART_SERIES,
            )
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

    # Two exports, because they are genuinely two different things and one
    # button trying to be both is what broke the round-trip: the view frame
    # holds display-unit prices beside the listing's original currency, which
    # cannot be re-imported without inflating every divine-priced row.
    st.markdown("**Export**")
    dataset_col, view_col = st.columns(2)
    with dataset_col:
        st.download_button(
            "Export dataset (re-importable)",
            listing_rows_to_csv(rows),
            "originator_dataset.csv",
            "text/csv",
            use_container_width=True,
        )
        st.caption("Importer's own schema. Drop it back into the Import tab as-is.")
    with view_col:
        st.download_button(
            "Export view (for reading)",
            frame.to_csv(index=False),
            "originator_view.csv",
            "text/csv",
            use_container_width=True,
        )
        st.caption(f"What is on screen: prices in {fmt.unit}, plus outlier reasons.")
