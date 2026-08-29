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
from src.market.collect_job import CollectJob
from src.market.currency_source import ExchangeRateError, fetch_rate_quote
from src.market.ingest import converter_for, refresh_outliers, store
from src.schema import FEATURE_NAMES
from src.ui.common import (
    CHART_SERIES,
    data_version,
    flash,
    get_config,
    get_converter,
    get_exalt_cost,
    get_formatter,
    get_repo,
    invalidate_data,
    invalidate_model_cache,
    listing_counts,
    listing_records,
    page_header,
    price_histogram,
    show_flash,
    stat_rail,
    typical_focus,
)

#: The four reward flags whose combination the Inspect tab counts.
REWARD_COLUMNS = ("more_currency", "more_maps", "more_scarabs", "more_divination_cards")


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
    counts = listing_counts(cfg.get("league"), data_version())
    stat_rail(
        [
            ("Stored", f"{counts['total']:,}", cfg.get("league")),
            ("Flagged", f"{counts['outliers']:,}", "outliers"),
            ("Last 24h", f"{counts['last_24h']:,}", "captured"),
        ]
    )

    collect_tab, passive_tab, import_tab, inspect_tab = st.tabs(
        ["Collect", "Passive", "Import", "Inspect and export"]
    )

    with collect_tab:
        _collect(cfg, repo)
    with passive_tab:
        _passive(cfg)
    with import_tab:
        _import(cfg, repo)
    with inspect_tab:
        _inspect(cfg, repo, fmt)


#: How often the collection panel redraws itself while a run is in flight.
#: Lifted from the live-run panel on the Stash Pricer, and for the same reason:
#: fast enough that Cancel feels like a button rather than a form submission,
#: slow enough not to redraw a page for every frame of a progress bar.
COLLECT_REFRESH_SECONDS = 1.5


def collect_headline(update, already: int) -> tuple[float, str]:
    """The progress bar's fraction and its one-line summary.

    Module-level because two things draw it now -- the inline path this page
    used to have, and the fragment that polls a worker thread -- and a headline
    that says "4,102 maps indexed" in one place and something subtly different
    in the other is a needless way to make the same run look like two runs.

    Reads its fields with getattr: collectors emit a `ProgressUpdate`, which
    stringifies to the old plain message, so a collector that only sends text
    still renders here rather than raising.
    """
    fraction = float(getattr(update, "fraction", 0.0) or 0.0)
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
    return min(max(fraction, 0.0), 1.0), headline


def should_retrain_after(stats: dict | None, requested: bool) -> bool:
    """Whether the retrain half of "Collect and retrain" should still run.

    It should not, if the collection was cancelled. Cancelling means "stop
    calling the trade API", and answering that by starting a training run the
    user is now waiting on is the opposite of stopping. The stated workflow is
    cancel, look at what landed, then press "Retrain on stored data" when you
    want it -- so the decision lives here, in one testable place, rather than
    inside a fragment that only exists while a browser is open.

    A run that errored out is excluded for a plainer reason: there is nothing
    new to train on.
    """
    stats = stats or {}
    return bool(requested) and not stats.get("cancelled") and not stats.get("error")


def _collect(cfg, repo) -> None:
    st.markdown(
        "Collects listings from the **official pathofexile.com trade API**, obeying the "
        "rate-limit policy GGG returns in its own response headers. Nothing is scraped "
        "and no access control is bypassed. See `docs/data-access.md`."
    )

    job = st.session_state.get("collect_job")
    if job is not None and job.running:
        # A run is in flight on a worker thread. Everything below -- the size
        # box, the three buttons -- would start a second one, so the panel takes
        # the tab for as long as it lasts.
        _collect_panel(cfg)
        st.markdown("---")
        _orb_prices(cfg, repo)
        return

    limit = st.number_input("How many listings to collect", 50, 5000, 500, step=50)
    already = listing_counts(cfg.get("league"), data_version())["total"]

    # The run ended -- finished, cancelled or thrown. Retire the handle, say
    # what came of it, and (unless it was cancelled) do the retrain half of the
    # button that started it.
    if job is not None:
        _report_finished_collection(cfg, repo, job, already)

    buttons = st.columns(3)
    collect_only = buttons[0].button("Collect from trade API")
    # The pairing exists because collecting on its own changes no price. The
    # model is a separate artefact and nothing retrains it on a timer, so
    # "collect" without "retrain" leaves the app showing the old numbers over
    # newer data -- which is exactly the state that is impossible to notice.
    collect_and_train = buttons[1].button(
        "Collect and retrain", type="primary",
        help="Collect, re-run outlier detection, then retrain on the result.",
    )
    # The third button exists because the second one cannot be reached after a
    # cancel. A collection that is stopped part way has still written every
    # listing it read -- ingest flushes to SQLite every fifty rows -- but the
    # retrain half of "Collect and retrain" is deliberately skipped, because a
    # cancel means "stop calling the API", not "start a training run I did not
    # ask for". That leaves exactly the state this app spends most of its prose
    # warning about: newer data underneath older prices. This button is the way
    # out of it, and it costs no requests at all.
    train_only = buttons[2].button(
        "Retrain on stored data",
        help="Trains on everything already in the database -- including whatever "
        "a cancelled collection left behind -- without calling the trade API.",
    )

    if train_only:
        _retrain(cfg)

    if collect_only or collect_and_train:
        st.session_state["collect_job"] = CollectJob.start(cfg, limit=int(limit))
        st.session_state["collect_train_after"] = bool(collect_and_train)
        st.session_state["collect_baseline"] = already
        # Straight back into the branch above, so the Cancel button is on screen
        # before the first request goes out rather than after the run ends. The
        # run itself is on a worker thread; this thread's only remaining job is
        # to draw.
        st.rerun()

    st.markdown("---")
    _orb_prices(cfg, repo)


@st.fragment(run_every=COLLECT_REFRESH_SECONDS)
def _collect_panel(cfg) -> None:
    """Progress, a Cancel button, and a way to look at what has landed.

    A fragment rather than the page, for the reason the Stash Pricer's live-run
    panel is one: this redraws a progress bar every second and a half instead of
    redrawing the stat rail, the three tabs and both orb prices.

    "Show what has landed" is not decoration either. ingest commits every fifty
    listings, so a run twenty minutes in has most of its haul already in the
    database -- the charts and the export on the Inspect tab can read it without
    the run having to finish first.
    """
    job = st.session_state.get("collect_job")
    if job is None:
        return

    if job.finished:
        # Rows were written by another thread, so the session's cached reads are
        # stale by definition. Drop them, then hand the page back to the full
        # script, which reports the outcome and retrains if it was asked to.
        invalidate_data()
        st.rerun()

    already = int(st.session_state.get("collect_baseline", 0))
    if job.progress is not None:
        fraction, headline = collect_headline(job.progress, already)
        st.progress(fraction, text=headline)
        st.caption(str(job.progress))
    elif job.waiting_for_lock:
        st.progress(0.0, text="Waiting for the passive scan to finish")
        st.caption(
            "Only one collection reads the market at a time -- two would each "
            "obey GGG's policy while together breaking it."
        )
    else:
        st.progress(0.0, text="Starting…")
        st.caption("waiting on the first response from the trade API")

    columns = st.columns([1, 1, 2])
    if job.cancelling:
        columns[0].button(
            "Stopping…",
            disabled=True,
            key="collect_cancel",
            help="The request already in flight has to finish. A rate-limit "
            "wait does not: that is cut short.",
        )
    elif columns[0].button(
        "Cancel collection",
        key="collect_cancel",
        help="Stops at the next slice or fetch boundary. Every listing already "
        "read is kept, and no retrain follows -- press 'Retrain on stored "
        "data' when you want one.",
    ):
        job.cancel()
        st.rerun(scope="fragment")

    if columns[1].button(
        "Show what has landed",
        key="collect_peek",
        help="Redraws the page with everything written so far. The run keeps "
        "going.",
    ):
        invalidate_data()
        st.rerun()


def _report_finished_collection(cfg, repo, job, already: int) -> None:
    """Say what the run came to, then retire it.

    The handle is dropped first and unconditionally. `_retrain` ends in
    `st.rerun()`, and a job still sitting in session state at that point would
    be reported, and retrained on, once per rerun forever.
    """
    st.session_state.pop("collect_job", None)
    train_after = bool(st.session_state.pop("collect_train_after", False))
    baseline = int(st.session_state.pop("collect_baseline", already))
    stats = job.stats or {}

    if job.error:
        st.error(f"The collection stopped on an error: {job.error}")
        return
    if stats.get("error"):
        st.error(stats["error"])
        return

    total = repo.count_listings(cfg.get("league"))["total"]
    summary = (
        f"Inserted {stats.get('inserted', 0)}, refreshed {stats.get('updated', 0)}, "
        f"flagged {stats.get('flagged', 0)} outliers. "
        f"{total} maps indexed in total (was {baseline})."
    )
    if stats.get("cancelled"):
        # Said out loud, because it is the one outcome where the sample is
        # incomplete in a way that matters: a cancelled walk stopped part way
        # through the stratification, so it is weighted towards the slices it
        # reached.
        st.warning(
            f"Collection cancelled. Everything it had already read was kept: "
            f"{summary} The model has not been retrained -- press **Retrain on "
            f"stored data** when you want these listings priced with."
        )
    else:
        st.success(summary)

    if should_retrain_after(stats, train_after):
        _retrain(cfg)


#: How often the passive panel redraws. Slower than the collect panel: nothing
#: on it needs to feel like a button, and a countdown ticking every two seconds
#: is as legible as one ticking every one.
PASSIVE_REFRESH_SECONDS = 2.0


def _passive(cfg) -> None:
    """The unattended collector: a switch, four settings, and what it is doing.

    A mode rather than an action, which is why it is a tab of its own and not a
    fourth button next to Collect. See `src/market/passive.py` for why the loop
    lives in a process-global rather than in session state, and why the switch
    is deliberately not remembered between launches.
    """
    from src.market.passive import PassiveSettings, controller

    control = controller()
    st.markdown(
        "Collects on a loop for as long as this app is open: one scan, then a "
        "quiet period long enough for the rate limit to reset, then another. "
        "**It stops when you close the app and does not restart itself.**"
    )

    settings = PassiveSettings.from_config(cfg)
    on = st.toggle(
        "Passive collection",
        value=control.running,
        help="Starts a background scan loop. The manual Collect buttons stay "
        "usable -- a manual run simply waits for any scan in flight to finish.",
    )
    if on and not control.running:
        control.start(cfg, settings)
        st.rerun()
    elif not on and control.running:
        control.stop()
        st.rerun()

    if control.running:
        _passive_panel(cfg)
    else:
        state = control.state
        if state.scans_done:
            st.caption(
                f"Last session: {state.scans_done} scans, "
                f"{state.listings_total:,} listings. {state.stopped_reason}."
            )
        _passive_settings(cfg, settings)

    _publish_passive_model(control)


def _passive_settings(cfg, settings) -> None:
    """The four knobs. Editable only while the loop is off, because changing
    the cooldown under a sleeping worker would not take effect until the sleep
    it is already in has finished -- a setting that appears to do nothing."""
    st.markdown("##### Settings")
    left, right = st.columns(2)
    maps = left.number_input(
        "Maps per scan", 50, 2000, int(settings.maps_per_scan), step=50,
        help="About one request per ten maps, plus one per query slice. 400 "
        "maps is roughly 56 requests -- well inside what GGG's policy allows.",
    )
    cooldown = right.number_input(
        "Cooldown (minutes)", 1, 360, int(settings.cooldown_minutes), step=5,
        help="Time off the wire between scans. This is politeness, not safety: "
        "the rate limiter obeys GGG's own headers whatever this says.",
    )
    auto_off = left.number_input(
        "Switch off after (hours)", 0, 48, int(settings.auto_off_after_hours),
        help="0 runs until you close the app.",
    )
    retrain = right.checkbox(
        "Retrain automatically", value=settings.auto_retrain,
        help="Otherwise the model quietly falls behind the data it is being "
        "shown over, which is the one failure state that is invisible.",
    )
    every = right.number_input(
        "Retrain every N scans", 1, 50, int(settings.retrain_every_scans),
        disabled=not retrain,
    )
    if st.button("Save settings"):
        cfg.set("collection.passive.maps_per_scan", int(maps))
        cfg.set("collection.passive.cooldown_minutes", int(cooldown))
        cfg.set("collection.passive.auto_off_after_hours", int(auto_off))
        cfg.set("collection.passive.auto_retrain", bool(retrain))
        cfg.set("collection.passive.retrain_every_scans", int(every))
        cfg.save()
        flash("success", "Passive collection settings saved.")
        st.rerun()


@st.fragment(run_every=PASSIVE_REFRESH_SECONDS)
def _passive_panel(cfg) -> None:
    from src.market.passive import COOLING, SCANNING, TRAINING, WAITING, controller

    control = controller()
    if not control.running:
        # The loop ended between renders. Hand the tab back to the full page so
        # the settings and the toggle come back, and stop this fragment ticking.
        st.rerun()
        return

    state = control.state
    labels = {
        WAITING: "waiting for the manual collector to finish",
        SCANNING: "scanning",
        TRAINING: "retraining on everything collected so far",
        COOLING: "cooling down",
    }
    st.markdown(f"**{labels.get(state.phase, state.phase).capitalize()}**")

    if state.phase == COOLING:
        remaining = state.seconds_until_next_scan()
        st.progress(
            1.0 - min(1.0, remaining / max(1.0, _cooldown_seconds(cfg))),
            text=f"Next scan in {int(remaining // 60)}m {int(remaining % 60)}s",
        )
    elif state.phase == SCANNING:
        st.caption(f"{state.listings_this_scan:,} listings this scan")

    stat_rail(
        [
            ("Scans", f"{state.scans_done:,}", "this session"),
            ("Collected", f"{state.listings_total:,}", "listings"),
            ("Model", f"v+{state.model_generation}", "retrains"),
        ]
    )
    if state.last_error:
        st.warning(state.last_error)

    if st.button("Stop passive collection"):
        control.stop()
        st.rerun()


def _cooldown_seconds(cfg) -> float:
    from src.market.passive import PassiveSettings

    return PassiveSettings.from_config(cfg).cooldown_minutes * 60.0


def _publish_passive_model(control) -> None:
    """Drop the model caches when the worker has retrained behind our back.

    The worker cannot do this for itself: `invalidate_model_cache` touches
    `st.session_state` and calls `.clear()` on `@st.cache_resource` objects,
    and neither is legal off the script thread. So it bumps a counter and this
    runs the Streamlit half on the next render that notices.
    """
    generation = control.state.model_generation
    if st.session_state.get("passive_model_generation", 0) == generation:
        return
    st.session_state["passive_model_generation"] = generation
    if generation:
        invalidate_model_cache()
        flash("success", "Passive collection retrained the model. Prices are new.")
        st.rerun()


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
                invalidate_data()  # every price_chaos in the table just moved
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
    invalidate_data()  # an import is a write to the listings table
    st.success(
        f"Inserted {stats.get('inserted', 0)}, refreshed {stats.get('updated', 0)}, "
        f"flagged {stats.get('flagged', 0)} outliers."
    )


@st.cache_data(show_spinner=False, max_entries=4)
def _view_frame(league: str | None, unit: str, divine: float, version: int, include_outliers: bool):
    """The display table, its reward-combination counts and its two exports.

    All four are derived from one query and rebuilt only when the data, the
    league or the display unit changes. Previously every one of them was
    recomputed on each rerun of the page -- including both CSVs, which were
    serialised in full to feed download buttons whether or not anybody clicked
    them, and a Counter built with frame.iterrows() over 5,000 rows.
    """
    from src.pricing.display import PriceFormatter

    fmt = PriceFormatter(unit=unit, divine_to_chaos=divine)
    # One query for the superset; the unflagged view is a list filter on it,
    # which is free next to a second trip through sqlite and pandas.
    every = listing_records(league, True, version)
    rows = every if include_outliers else [r for r in every if not r["is_outlier"]]
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
    # Counted off the dicts rather than the frame: iterrows() builds a Series
    # per row and was the single slowest statement on the page.
    combos = Counter(
        tuple(name for name in REWARD_COLUMNS if row[name] > 0) for row in rows
    )
    flagged = pd.DataFrame(
        [
            {
                "price": fmt.value(row["price_chaos"]) or 0.0,
                "currency": row["price_currency"],
                "reason": row["outlier_reason"],
            }
            for row in every
            if row["is_outlier"]
        ]
    )
    return frame, combos, flagged, listing_rows_to_csv(rows), frame.to_csv(index=False)


@st.fragment
def _inspect(cfg, repo, fmt) -> None:
    """A fragment because nothing above it depends on anything inside it.

    The outlier checkbox and the export buttons used to rerun the entire page
    -- header, stat rail, sidebar and the two sibling tabs -- to change one
    table. As a fragment, only this block redraws, so the rest of the page
    stops being greyed out on every touch.
    """
    show_outliers = st.checkbox("Include flagged outliers", value=False)
    frame, combos, flagged_frame, dataset_csv, view_csv = _view_frame(
        cfg.get("league"), fmt.unit, fmt.divine_to_chaos, data_version(), show_outliers
    )
    if frame.empty:
        st.info("No listings yet.")
        return

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
    if flagged_frame.empty:
        st.caption("Nothing flagged.")
    else:
        st.dataframe(
            flagged_frame["reason"].value_counts().rename("listings"),
            use_container_width=True,
        )
        if st.button("Re-run outlier detection"):
            result = refresh_outliers(cfg, repo)
            invalidate_data()
            flash(
                "success",
                f"Flagged {result['flagged']} of {result['evaluated']} listings.",
            )
            # scope="app": re-flagging changes the counts in the stat rail and
            # the sidebar, which live outside this fragment.
            st.rerun(scope="app")

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
            dataset_csv,
            "originator_dataset.csv",
            "text/csv",
            use_container_width=True,
        )
        st.caption("Importer's own schema. Drop it back into the Import tab as-is.")
    with view_col:
        st.download_button(
            "Export view (for reading)",
            view_csv,
            "originator_view.csv",
            "text/csv",
            use_container_width=True,
        )
        st.caption(f"What is on screen: prices in {fmt.unit}, plus outlier reasons.")
