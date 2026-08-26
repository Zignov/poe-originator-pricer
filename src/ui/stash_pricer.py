"""Stash Pricer -- the main workflow.

    choose a source -> choose tabs -> Analyze -> grid + table + comparables
"""
from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd
import streamlit as st

from src.config import ROOT
from src.market.comparables import format_age
from src.overlay.calibration import Calibration, resolution_warning
from src.overlay.state import state_from_priced, write_state
from src.pricing.sell_strategy import STRATEGIES, STRATEGY_LABELS
from src.stash.base import StashSource
from src.stash.clipboard_import import ClipboardStashSource
from src.stash.highlight_regex import build_highlight_regex
from src.stash.json_import import JsonStashSource, parse_csv
from src.stash.official_api import SessionStashSource, StashAccessError
from src.pricing.fair_value import PricedMap
from src.ui.common import (
    get_config,
    get_repo,
    get_service,
    hint,
    page_header,
    render_confidence,
    section,
    stat_rail,
)
from src.ui.exalt_panel import affix_label, affixes_for, render_verdict, verdict_for
from src.ui.stash_grid import render_grid

MODE_LABELS = {
    "model": "Model",
    "market": "Market comparison",
}

SOURCE_LABELS = {
    "session": "Connect account (POESESSID)",
    "json": "Stash JSON export",
    "csv": "CSV file",
    "clipboard": "Paste copied item text",
    "snapshot": "Re-open a saved scan",
}


def render() -> None:
    cfg = get_config()
    repo = get_repo()

    service, model_error = get_service()
    fmt = service.formatter

    page_header(
        "Price",
        "Stash Pricer",
        "Load a tab, then read it as a set of decisions: what to list, at what "
        "price, and which maps are worth an Exalted Orb first.",
    )
    if model_error:
        st.markdown(f'<div class="warn">{model_error}</div>', unsafe_allow_html=True)

    # The source picker is the first thing on the page and the last thing you
    # need once a tab is loaded, so it folds itself away. Left open it pushed
    # every price below the fold and made re-pricing look like a five-step form.
    loaded = st.session_state.get("stash_items")
    with st.expander(
        _source_summary(loaded), expanded=not loaded
    ):
        source_key = st.radio(
            "Where should the maps come from?",
            list(SOURCE_LABELS),
            format_func=SOURCE_LABELS.get,
            horizontal=True,
        )

        items = None
        if source_key == "session":
            items = _from_session(cfg)
        elif source_key == "json":
            items = _from_json(cfg)
        elif source_key == "csv":
            items = _from_csv(cfg)
        elif source_key == "snapshot":
            items = _from_snapshot(cfg, repo)
        else:
            items = _from_clipboard(cfg)

    if items is not None:
        st.session_state["stash_items"] = items
        st.session_state["stash_source"] = SOURCE_LABELS[source_key]
        # The expander's label and open state were decided at the top of this
        # run, before these items existed, so without a second pass the picker
        # stays open saying "choose a source" over a tab it has already loaded.
        # Guarded by a fingerprint rather than a flag: a loader that returns the
        # same tab on every rerun would otherwise rerun forever.
        stamp = (len(items), SOURCE_LABELS[source_key])
        if st.session_state.get("stash_stamp") != stamp:
            st.session_state["stash_stamp"] = stamp
            st.rerun()
    items = st.session_state.get("stash_items")
    if not items:
        return

    mode = st.radio(
        "Pricing mode",
        list(MODE_LABELS),
        format_func=MODE_LABELS.get,
        horizontal=True,
        key="pricing_mode",
        help=(
            "The model prices from what maps like this have historically been listed "
            "at. Market comparison asks the trade site what maps like this are listed "
            "at right now, and shows you those listings."
        ),
    )

    controls = st.columns([1.2, 1, 1, 1, 1])
    strategy = controls[0].selectbox(
        "Selling strategy", STRATEGIES, index=1, format_func=STRATEGY_LABELS.get
    )
    min_value = controls[1].number_input(
        f"Min value ({fmt.column_suffix()})", min_value=0.0, value=0.0,
        step=5.0 if fmt.unit == "chaos" else 0.5,
    )
    # The two checkboxes are one question -- what to leave out -- and without a
    # label of their own they also sat a label's height higher than every other
    # control in the row.
    controls[2].markdown(
        '<div class="filter-label">Show only</div>', unsafe_allow_html=True
    )
    only_confident = controls[2].checkbox("High confidence only")
    exaltable_only = controls[2].checkbox(
        "Exaltable only", help="Uncorrupted maps with fewer than 6 affixes."
    )
    reward_filter = controls[3].selectbox(
        "Reward filter", ["Any", "More Currency", "More Maps", "More Scarabs", "More Div Cards"]
    )
    sort_key = controls[4].selectbox(
        "Sort by",
        [
            "Highest value",
            "Lowest value",
            "More currency",
            "More maps",
            "More scarabs",
            "Pack size",
            "Confidence",
            "Stash position",
        ],
    )

    model_priced = service.price_many(items, strategy=strategy)
    if mode == "market":
        priced = _market_mode(cfg, repo, service, items, strategy)
    else:
        priced = model_priced

    supported = [p for p in priced if p.supported]
    total_chaos = sum(p.suggested_chaos or 0 for p in supported)
    exaltable = sum(
        1 for p in supported if (v := verdict_for(p.item)) and v.available and v.worth_it
    )
    stat_rail(
        [
            ("Maps in tab", f"{len(items)}"),
            ("Priceable", f"{len(supported)}", f"of {len(items)}"),
            ("Skipped", f"{len(items) - len(supported)}", "unsupported"),
            ("Tab total", fmt.format(total_chaos), STRATEGY_LABELS[strategy].lower()),
            ("Worth an exalt", f"{exaltable}"),
        ]
    )
    if service.multipliers.source.startswith("config (observed spread rejected"):
        st.markdown(
            f'<div class="warn">{service.multipliers.source}</div>',
            unsafe_allow_html=True,
        )

    visible = _apply_filters(
        priced, fmt, min_value, only_confident, reward_filter, exaltable_only
    )
    visible = _sort(visible, sort_key)

    # The stash grid is a fixed-pixel layout, so it gets the wider share and a
    # large gap; the table beside it reflows to whatever is left.
    grid_col, table_col = st.columns([1.25, 1], gap="large")
    with grid_col:
        section("Stash layout", f"{STRATEGY_LABELS[strategy].lower()} prices")
        hint(
            "Each tile is the price to list that map at. A solid cyan outline "
            "means an Exalted Orb pays for itself; a dashed one means it does "
            "not. Hover a tile for fair value and the reasoning."
        )
        # Verdicts are keyed by object identity so the grid never has to know how
        # a map is priced -- ~2ms each, so a full tab costs about a tenth of a
        # second per rerun.
        verdicts = {id(p): verdict_for(p.item) for p in priced if p.supported}
        render_grid(priced, fmt, verdicts=verdicts)

    with table_col:
        section("Maps", f"{len(visible)} shown")
        if not visible:
            st.info(_no_matches_reason(priced, exaltable_only))
        else:
            frame = service.to_frame(visible)
            # Derived here rather than in PricedMap: the affix structure comes
            # from the payload the stash source preserved, which the pricing
            # layer has no reason to know about.
            frame.insert(2, "Affixes", [affix_label(p.item) for p in visible])
            previous = st.session_state.get("snapshot_previous")
            if previous:
                frame["Then"] = [
                    fmt.value(previous.get(fp)) if previous.get(fp) else None
                    for fp in frame["_fingerprint"]
                ]
                frame["Change %"] = [
                    None
                    if not then or not now
                    else (now / then - 1) * 100
                    for then, now in zip(frame["Then"], frame["Fair Value"])
                ]
            display = frame.drop(columns=["_fingerprint"])
            st.dataframe(
                display,
                use_container_width=True,
                hide_index=True,
                column_config=_price_columns(frame, fmt),
            )

    # Everything below the results used to be one long scroll of unrelated
    # tools: inspect a map, then the market evidence, then a stash-search
    # pattern, then a copy block, then the overlay, then a save button. They
    # answer different questions on different occasions and only one is ever
    # wanted at a time, so they are tabs.
    names = ["Inspect a map", "Highlight in game", "Copy & save", "Overlay"]
    if mode == "market":
        names.insert(1, "Market evidence")
    tabs = st.tabs(names)
    panes = dict(zip(names, tabs))

    with panes["Inspect a map"]:
        _detail_panel(visible, fmt)
    if mode == "market":
        with panes["Market evidence"]:
            _market_evidence(priced, fmt)
            _mode_comparison(model_priced, priced, fmt)
    with panes["Highlight in game"]:
        # The whole tab, not `visible`: the pattern must know about every map
        # that has to stay dark, and the table filters only change what is on
        # screen. repo and league are what let it name every map that *could*
        # be in the tab, not just the ones scanned into it.
        _highlight_tool(priced, fmt, repo, cfg.get("league"))
    with panes["Copy & save"]:
        _copy_tools(visible, fmt)
        _snapshot_button(cfg, repo, items, priced)
    with panes["Overlay"]:
        _overlay_panel(cfg, priced, fmt, strategy, verdicts)


# ---------------------------------------------------------------------------
def _source_summary(items) -> str:
    """The one line the collapsed source picker shows.

    Says what is loaded rather than what the control does, because once a tab is
    in hand that is the only question left about it -- and it keeps the way back
    visible without keeping five radio options and a file picker on screen.
    """
    if not items:
        return "Choose where the maps come from"
    where = st.session_state.get("stash_source", "a saved source")
    return f"{len(items)} maps loaded from {where}  --  click to load something else"


def _from_session(cfg):
    st.caption(
        "Uses the POESESSID cookie from your own browser session, against GGG's own "
        "site, to read your own stash. Set it in the Settings page or in .env."
    )
    source = SessionStashSource(
        poesessid=st.session_state.get("poesessid") or None,
        account_name=st.session_state.get("account_name") or None,
        realm=cfg.get("realm", "pc"),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    ok, why = source.available()
    if not ok:
        st.warning(f"{why}. {source.setup_hint}")
        return None

    if st.button("Load stash tab list", type="primary"):
        try:
            st.session_state["tabs"] = source.list_tabs(cfg.get("league"))
        except StashAccessError as exc:
            st.error(str(exc))
            return None

    tabs = st.session_state.get("tabs")
    if not tabs:
        return None

    chosen_names = st.multiselect(
        "Tabs to analyze", [t.name for t in tabs], default=[tabs[0].name] if tabs else []
    )
    chosen = [t for t in tabs if t.name in chosen_names]
    if chosen and st.button("Analyze", type="primary"):
        with st.spinner("Fetching tab contents..."):
            try:
                return source.fetch_tabs(cfg.get("league"), chosen)
            except StashAccessError as exc:
                st.error(str(exc))
    return None


def _from_json(cfg):
    st.caption(
        "Accepts a saved /character-window/get-stash-items response, an OAuth /stash "
        "response, or a bare list of item objects."
    )
    upload = st.file_uploader("Stash JSON export", type=["json"])
    if upload is None:
        return None
    try:
        payload = json.load(upload)
        source: StashSource = JsonStashSource(
            payload=payload,
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
    except Exception as exc:
        st.error(f"Could not read that file: {exc}")
        return None

    tabs = source.list_tabs(cfg.get("league"))
    chosen_names = st.multiselect(
        "Tabs in the file", [t.name for t in tabs], default=[t.name for t in tabs]
    )
    chosen = [t for t in tabs if t.name in chosen_names]
    if chosen and st.button("Analyze", type="primary"):
        return source.fetch_tabs(cfg.get("league"), chosen)
    return None


def _from_csv(cfg):
    st.caption(
        "One row per map. Required columns: quantity, rarity, pack_size, more_currency, "
        "more_maps, more_scarabs, more_divination_cards. Optional: position, tab, "
        "map_tier, item_level, corrupted."
    )
    upload = st.file_uploader("CSV", type=["csv"])
    if upload is None:
        return None
    try:
        return parse_csv(
            upload.getvalue().decode("utf-8"),
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
    except Exception as exc:
        st.error(f"Could not read that CSV: {exc}")
        return None


def _from_snapshot(cfg, repo):
    """Re-open an old scan and show how the recommendation has moved since."""
    snapshots = repo.list_snapshots(limit=100)
    if not snapshots:
        st.info("No saved scans yet. Analyze a tab and click *Save this scan as a snapshot*.")
        return None

    labels = {
        f"#{s['id']}  {s['created_at'][:16]}  {s['item_count']} items  "
        f"({', '.join(json.loads(s['tab_names'] or '[]')) or s['source']})": s["id"]
        for s in snapshots
    }
    choice = st.selectbox("Saved scan", list(labels))
    snapshot_id = labels[choice]

    previous = repo.snapshot_predictions(snapshot_id)
    if previous:
        st.caption(
            f"{len(previous)} prices were recorded at the time, by model "
            f"{previous[0]['model_version']}. Re-pricing now shows what changed."
        )
        st.session_state["snapshot_previous"] = {
            row["item_fingerprint"]: row["predicted_chaos"] for row in previous
        }
    else:
        st.session_state.pop("snapshot_previous", None)

    if st.button("Re-price this scan", type="primary"):
        return repo.load_snapshot_items(snapshot_id)
    return None


def _from_clipboard(cfg):
    st.caption(
        "Hover each map in game and press Ctrl+C, then paste. Several maps can be "
        "pasted one after another. Positions reflect paste order, not the real layout."
    )
    text = st.text_area("Copied item text", height=220, key="clipboard_text")
    if not text.strip():
        return None
    if st.button("Analyze", type="primary"):
        source = ClipboardStashSource(
            text,
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
        return source.fetch_tab(cfg.get("league"), source.list_tabs(cfg.get("league"))[0])
    return None


# ---------------------------------------------------------------------------
# Market comparison mode
# ---------------------------------------------------------------------------
def _market_mode(cfg, repo, service, items, strategy):
    """The live-listings mode: warn, run on a button, then show the evidence.

    Nothing happens until the button is pressed. This is the one page in the app
    that can spend a few hundred API requests and ten minutes of a rate-limited
    connection, so it says what it will cost before it costs it, and the run is
    resumable rather than all-or-nothing -- Streamlit reruns the whole script on
    every widget touch, and a mode that started over each time would never
    finish.
    """
    from src.market.live_comparables import MarketRunState, format_duration
    from src.pricing.market_comparison import (
        MarketComparisonPricer,
        MarketComparisonSettings,
    )

    settings = MarketComparisonSettings.from_config(cfg)
    supported = [i for i in items if i.supported]
    state: MarketRunState = st.session_state.get("market_run_state") or MarketRunState()

    left, right = st.columns([1, 2])
    budget = int(
        left.number_input(
            "Request budget",
            min_value=1,
            max_value=5000,
            value=int(settings.request_budget),
            step=25,
            help="Hard ceiling on API requests for one run. Hitting it stops "
            "cleanly; press the button again to continue where it left off.",
        )
    )

    pricer = MarketComparisonPricer.build(cfg, repo, service, budget=budget)
    remaining = len(state.pending(items))
    # Costed against GGG's published policy for each endpoint rather than
    # multiplied out at one nominal rate: search and fetch are limited
    # separately, and the fetches are the larger half of a per-map run.
    plan = pricer.plan(remaining)
    if plan.pooled:
        how = (
            f"It reads the market <b>once for the whole tab</b> -- "
            f"{plan.searches} searches and {plan.fetches} fetches, the same "
            f"whether you price three maps or seventy -- and derives every map's "
            f"price from that shared set. A map the pool has nothing like falls "
            f"back to a search of its own."
        )
    else:
        how = (
            f"It searches per map, widened until enough comparables come back, "
            f"plus a fetch per ten listings: {plan.searches} searches and "
            f"{plan.fetches} fetches. That is cheaper than one shared read at "
            f"this few maps."
        )
    with right:
        st.markdown(
            f'<div class="warn"><b>This mode calls the official trade API.</b> '
            f"{how} For {remaining} maps that is about "
            f"<b>{format_duration(plan.seconds)}</b> under GGG's rate limits. "
            f"Results are cached for {settings.cache_ttl_minutes:g} minutes.</div>",
            unsafe_allow_html=True,
        )

    columns = st.columns([1, 1, 2])
    if columns[0].button(
        "Price from live listings" if not state.outcomes else "Continue pricing",
        type="primary",
    ):
        bar = st.progress(0.0)
        caption = st.empty()

        def report(update):
            bar.progress(min(1.0, update.done / max(update.total, 1)))
            caption.caption(f"{update.caption()}  |  {update.message}")

        state = pricer.run(items, state=state, progress=report)
        st.session_state["market_run_state"] = state
        bar.empty()

    if columns[1].button("Clear market prices"):
        st.session_state.pop("market_run_state", None)
        state = MarketRunState()

    if state.outcomes:
        st.caption(
            f"{state.priced_count} of {len(supported)} priceable maps have a live "
            f"price  |  {state.requests_used} requests used  |  "
            f"{state.cache_hits} answered from the bucket cache at no request cost"
        )
        if state.stopped_reason:
            st.markdown(
                f'<div class="warn">{state.stopped_reason}</div>', unsafe_allow_html=True
            )
    else:
        st.info(
            "No live prices yet. The table below stays empty until you run the "
            "mode — the model's numbers are one radio button away and cost nothing."
        )

    return pricer.priced(items, state, strategy=strategy)


def _market_evidence(priced, fmt):
    """One expander per map: the actual listings the price was derived from.

    This is the mode's whole selling point over the model. A model price is a
    number you either trust or do not; a market price is a number you can check,
    and checking it means seeing the rows -- their rewards, their price, how old
    they are, who is selling, how far they sit from your map and how much of the
    answer they therefore carried.
    """
    rows = [p for p in priced if p.source == "market" and p.derivation is not None]
    if not rows:
        return

    st.markdown("### Where each number came from")
    rows.sort(key=lambda p: p.estimate.fair_value_chaos, reverse=True)
    for entry in rows:
        derivation = entry.derivation
        header = (
            f"{entry.item.position_label()}  ·  {fmt.format(entry.estimate.fair_value_chaos)}  ·  "
            f"{len(derivation.comparables)} comparables at {derivation.tolerance:g}x tolerance  ·  "
            f"{entry.estimate.confidence}"
        )
        with st.expander(header):
            from src.market.live_comparables import describe_window

            st.caption(
                f"Search window: {describe_window(derivation.window)}. "
                f"{derivation.total_matching} listings matched; the newest "
                f"{len(derivation.comparables)} were read"
                + (" (from the cache, no requests)" if derivation.from_cache else "")
                + "."
            )
            for reason in entry.estimate.confidence_reasons:
                st.caption(f"- {reason}")

            frame = entry.comparables.copy()
            frame["Price"] = frame["price_chaos"].map(fmt.value)
            frame["Age"] = frame["age_hours"].map(format_age)
            frame["Share"] = frame["weight"] / frame["weight"].sum()
            st.dataframe(
                frame[
                    [
                        "more_currency",
                        "more_maps",
                        "more_scarabs",
                        "more_divination_cards",
                        "pack_size",
                        "quantity",
                        "rarity",
                        "Price",
                        "Age",
                        "seller",
                        "distance",
                        "Share",
                    ]
                ].rename(
                    columns={
                        "more_currency": "Cur",
                        "more_maps": "Maps",
                        "more_scarabs": "Scar",
                        "more_divination_cards": "Div",
                        "pack_size": "Pack",
                        "quantity": "Qty",
                        "rarity": "Rar",
                        "seller": "Seller",
                        "distance": "Distance",
                    }
                ),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Price": st.column_config.NumberColumn(
                        f"Price ({fmt.column_suffix()})",
                        format="%.0f" if fmt.unit == "chaos" else "%.2f",
                    ),
                    "Distance": st.column_config.NumberColumn("Distance", format="%.2f"),
                    "Share": st.column_config.NumberColumn("Weight", format="%.0f%%"),
                },
            )
            if derivation.discarded:
                st.caption(
                    "Discarded: "
                    + "; ".join(
                        f"{fmt.format(c.price_chaos)} ({why})"
                        for c, why in derivation.discarded[:6]
                    )
                )


def _mode_comparison(model_priced, market_priced, fmt):
    """Model against market, worst disagreement first.

    Shown rather than reconciled. A big gap is not an error to be averaged away:
    it is either a map the model has never seen anything like, or a corner of the
    market that has moved since the model was trained, and both are things the
    person about to set a price wants to look at.
    """
    from src.pricing.market_comparison import compare_modes

    rows = [r for r in compare_modes(model_priced, market_priced) if r.gap_ratio]
    if not rows:
        return
    st.markdown("### Model vs market")
    frame = pd.DataFrame(
        [
            {
                "Pos": row.item.position_label(),
                "Model": fmt.value(row.model_chaos),
                "Market": fmt.value(row.market_chaos),
                "Market / model": row.gap_ratio,
            }
            for row in rows
        ]
    )
    digits = 0 if fmt.unit == "chaos" else 2
    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Model": st.column_config.NumberColumn(
                f"Model ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Market": st.column_config.NumberColumn(
                f"Market ({fmt.column_suffix()})", format=f"%.{digits}f"
            ),
            "Market / model": st.column_config.NumberColumn(
                "Market / model", format="%.2fx"
            ),
        },
    )
    st.caption(
        "Neither column overwrites the other. The rows at the top are where the "
        "trained model and the live market disagree most — worth a look before "
        "you list those maps."
    )


# ---------------------------------------------------------------------------
def _no_matches_reason(priced, exaltable_only: bool) -> str:
    """Say *why* nothing matched, rather than leaving an empty table.

    The common case is not a strict filter: GGG's stash endpoints return each
    modifier as description text with no affix name or tier, so affix counts
    cannot be derived from a stash at all and every map is "unknown" rather than
    exaltable. Silently showing nothing made that look like a bug in the filter.
    """
    if not exaltable_only:
        return "No maps match the current filters."

    states = [affixes_for(p.item) for p in priced]
    if states and not any(a.known for a in states):
        return (
            "No affix data in this stash payload, so no map can be judged exaltable. "
            "GGG's stash endpoints return modifier text only — no affix names or "
            "tiers — which is what the Affixes column is showing as `-`. Affix "
            "counts currently only work on maps that came from the trade API."
        )
    if states and all(a.corrupted or a.is_full for a in states if a.known):
        return "Every map here is either corrupted or already has all its affixes."
    return "No maps match the current filters."


def _price_columns(frame, fmt):
    """Number formatting for every price column, in the chosen unit."""
    digits = 0 if fmt.unit == "chaos" else 2
    config = {
        col: st.column_config.NumberColumn(f"{col} ({fmt.column_suffix()})", format=f"%.{digits}f")
        for col in PricedMap.PRICE_COLUMNS
        if col in frame.columns
    }
    if "Then" in frame.columns:
        config["Then"] = st.column_config.NumberColumn(
            f"Then ({fmt.column_suffix()})", format=f"%.{digits}f"
        )
    if "Change %" in frame.columns:
        config["Change %"] = st.column_config.NumberColumn("Change %", format="%+.0f%%")
    return config


def _apply_filters(priced, fmt, min_value, only_confident, reward_filter, exaltable_only=False):
    field = {
        "More Currency": "more_currency",
        "More Maps": "more_maps",
        "More Scarabs": "more_scarabs",
        "More Div Cards": "more_divination_cards",
    }.get(reward_filter)

    out = []
    for entry in priced:
        if field and getattr(entry.item, field, 0) <= 0:
            continue
        if exaltable_only and not affixes_for(entry.item).can_be_exalted:
            continue
        if entry.supported:
            if min_value and (fmt.value(entry.estimate.fair_value_chaos) or 0) < min_value:
                continue
            if only_confident and entry.estimate.confidence != "High":
                continue
        elif min_value or only_confident:
            continue
        out.append(entry)
    return out


def _sort(priced, key):
    def value(entry):
        return entry.estimate.fair_value_chaos if entry.estimate else -1

    keys = {
        "Highest value": (value, True),
        "Lowest value": (value, False),
        "More currency": (lambda e: e.item.more_currency, True),
        "More maps": (lambda e: e.item.more_maps, True),
        "More scarabs": (lambda e: e.item.more_scarabs, True),
        "Pack size": (lambda e: e.item.pack_size, True),
        "Confidence": (
            lambda e: {"High": 3, "Medium": 2, "Low": 1}.get(
                e.estimate.confidence if e.estimate else "", 0
            ),
            True,
        ),
        "Stash position": (lambda e: (e.item.y or 0, e.item.x or 0), False),
    }
    func, reverse = keys[key]
    return sorted(priced, key=func, reverse=reverse)


def _detail_panel(priced, fmt):
    supported = [p for p in priced if p.supported]
    if not supported:
        return
    labels = {
        f"{p.item.position_label()}  -  {fmt.format(p.estimate.fair_value_chaos)}": p
        for p in supported
    }
    choice = st.selectbox("Inspect a map", list(labels))
    entry = labels[choice]
    est = entry.estimate

    left, right = st.columns([1, 1.4])
    with left:
        # Set as a readout rather than four bold sentences: the three numbers are
        # read against each other -- is the suggestion inside the range -- and
        # three lines of running text is the one layout that makes comparing
        # them hard. Confidence follows as a meter rather than a fourth row:
        # "Medium 0.51" and "Medium 0.68" are the same sentence at a glance and
        # visibly different bars, and that difference is most of what the score
        # is for.
        rows = [
            ("Fair value", fmt.format(est.fair_value_chaos), ""),
            (
                "List at",
                fmt.format(entry.suggested_chaos),
                STRATEGY_LABELS[entry.strategy].lower(),
            ),
            (
                "Likely range",
                fmt.range(est.interval_low_chaos, est.interval_high_chaos),
                "",
            ),
        ]
        body = "".join(
            f'<div class="row"><span class="k">{k}</span>'
            f'<span class="v">{v}{f" <em>{n}</em>" if n else ""}</span></div>'
            for k, v, n in rows
        )
        st.markdown(f'<div class="readout">{body}</div>', unsafe_allow_html=True)
        render_confidence(est.confidence, est.confidence_score)
        for reason in est.confidence_reasons:
            st.markdown(
                f'<div class="reason">{reason}</div>', unsafe_allow_html=True
            )
        st.markdown(f'<div class="note-box">{entry.note}</div>', unsafe_allow_html=True)

        st.markdown("---")
        render_verdict(verdict_for(entry.item), fmt)

        _explanation(entry, fmt)

    with right:
        st.markdown("**Closest recent listings**")
        if entry.comparables.empty:
            st.info("No comparable listings in the dataset yet.")
        else:
            frame = entry.comparables.copy()
            frame["Price"] = frame["price_chaos"].map(fmt.value)
            frame["Age"] = frame["age_hours"].map(format_age)
            st.dataframe(
                frame[
                    [
                        "more_currency",
                        "more_maps",
                        "more_scarabs",
                        "more_divination_cards",
                        "pack_size",
                        "quantity",
                        "rarity",
                        "Price",
                        "Age",
                    ]
                ].rename(
                    columns={
                        "more_currency": "Cur",
                        "more_maps": "Maps",
                        "more_scarabs": "Scar",
                        "more_divination_cards": "Div",
                        "pack_size": "Pack",
                        "quantity": "Qty",
                        "rarity": "Rar",
                    }
                ),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Price": st.column_config.NumberColumn(
                        f"Price ({fmt.column_suffix()})",
                        format="%.0f" if fmt.unit == "chaos" else "%.2f",
                    )
                },
            )
            if entry.market_reference_chaos:
                st.caption(
                    f"Median of comparables: **{fmt.format(entry.market_reference_chaos)}** "
                    "-- an independent cross-check on the model."
                )


def _explanation(entry, fmt):
    from src.ml.explain import explain_item
    from src.ui.common import get_predictor

    predictor, _ = get_predictor()
    if predictor is None:
        return
    with st.expander("Why this price?"):
        try:
            result = explain_item(predictor, entry.item)
        except Exception as exc:
            st.caption(f"Explanation unavailable: {exc}")
            return
        st.caption(
            f"Model-derived attribution ({result['method']}). Every number below comes "
            "from the trained model; none are hand-written weights."
        )
        rows = [{"": "Estimated baseline", "Contribution": fmt.value(result["baseline_chaos"])}]
        for part in result["contributions"]:
            if abs(part["contribution_chaos"]) < 1e-9:
                continue
            rows.append(
                {
                    "": f"{part['label']} = {part['value']}",
                    "Contribution": fmt.value(part["contribution_chaos"]),
                }
            )
        rows.append({"": "Estimated value", "Contribution": fmt.value(result["total_chaos"])})
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Contribution": st.column_config.NumberColumn(
                    fmt.unit.title(), format="%+.0f" if fmt.unit == "chaos" else "%+.2f"
                )
            },
        )


def _item_text(item) -> str:
    """The lines the game prints on a map, as the stash search sees them.

    Modifiers are included, not just the name: every map in the tab prints
    "Map (Tier 16)" and "Item Quantity", and a fragment that happened to sit
    inside one of those shared lines would highlight the whole tab. Handing the
    whole text to the exclusion check rules that out without special-casing any
    particular line.

    The raw payload is mined for the rest of it -- and the **price note** is the
    one that actually bit. A selling tab has `~price 5 chaos` on every map, the
    parser keeps none of it, and a map named "Chaos Bearings" then yields `hao`
    as an apparently unambiguous fragment that highlights the whole tab. The
    generator carries a static list of this vocabulary as a floor, but the note
    is the seller's own text and can say things no list predicts, so it is read
    off the item wherever the source provided one.
    """
    parts = [item.name or "", item.base_type or ""]
    parts.extend(item.raw_modifiers or [])
    parts.extend(_unparsed_lines(item))
    return "\n".join(p for p in parts if p)


def _unparsed_lines(item) -> list[str]:
    """Searchable text sitting in the raw payload that the parser drops.

    Best-effort by design: the raw data is whatever the source handed over --
    an API object, a clipboard blob, or nothing at all for a CSV row -- so a
    missing field is normal and never a reason to fail the pattern.
    """
    raw = getattr(item, "raw_item_data", None)
    if not isinstance(raw, dict):
        return []
    if "text" in raw:  # clipboard import keeps the whole tooltip
        return str(raw["text"]).splitlines()

    out = [
        str(raw.get(key) or "")
        for key in ("note", "typeLine", "descrText", "secDescrText", "rarity")
    ]
    for prop in raw.get("properties") or []:
        values = " ".join(str(v[0]) for v in (prop.get("values") or []) if v)
        out.append(f"{prop.get('name') or ''}: {values}".strip())
    if raw.get("ilvl"):
        out.append(f"Item Level: {raw['ilvl']}")
    if raw.get("corrupted"):
        out.append("Corrupted")
    if raw.get("identified") is False:
        out.append("Unidentified")
    return [line for line in out if line.strip()]


@st.cache_data(show_spinner=False, max_entries=32)
def _cached_regex(
    names: tuple[str, ...], texts: tuple[str, ...], known: tuple[str, ...] = ()
):
    """Memoised because this page reruns on every widget touch.

    Building the pattern costs about 20ms for a 60-map tab but a third of a
    second across a couple of quad tabs, and paying that again every time a
    checkbox moves is what would make the page feel slow. The arguments are the
    whole input, so the key is exact -- change a price and the entry misses.
    """
    return build_highlight_regex(names, texts, known_names=known)


@st.cache_data(show_spinner=False, max_entries=4)
def _known_names(_repo, league) -> tuple[str, ...]:
    """Every rare map name the app has seen, for the pattern to steer around.

    The tab the pattern is built from is not necessarily the tab being
    searched -- the import can be an old snapshot, or simply miss whatever was
    dropped in since -- and a map the app never saw is a map the pattern was
    never checked against. Leading underscore on `_repo` so Streamlit hashes the
    league and not the database handle.
    """
    if _repo is None:
        return ()
    try:
        return tuple(_repo.known_map_names(league))
    except Exception:
        # A missing table or an older database is not a reason to refuse to
        # build a pattern; it only means falling back to the tab itself.
        return ()


def _highlight_tool(priced, fmt, repo=None, league=None):
    """Turn a price threshold into a pattern for the in-game stash search.

    Deliberately fed the *whole* tab rather than the filtered table above: the
    pattern has to be built against every map that must stay dark, and the
    filters exist to change what is on screen, not what is in the tab.
    """
    supported = [p for p in priced if p.supported and p.suggested_chaos]
    if not supported:
        return

    st.markdown("### Highlight these maps in game")
    st.caption(
        "The game cannot see this app's prices, so the threshold is turned into "
        "a stash-search pattern built from the maps' own names. Paste it into "
        "the stash search box (**Ctrl+F**) and the maps above your price light up."
    )

    values = sorted(float(p.suggested_chaos) for p in supported)
    default = values[max(0, int(len(values) * 0.75) - 1)]
    columns = st.columns([1, 3])
    threshold = columns[0].number_input(
        f"Highlight at or above ({fmt.column_suffix()})",
        min_value=0.0,
        value=round(float(fmt.value(default)) or 0.0, 2),
        step=5.0 if fmt.unit == "chaos" else 0.5,
        key="highlight_threshold",
    )

    selected, excluded = [], []
    for entry in priced:
        value = fmt.value(entry.suggested_chaos) if entry.suggested_chaos else None
        # An unpriced map is not "above the threshold", so it belongs in the
        # exclusion corpus like any other map that must stay dark.
        if entry.supported and value is not None and value >= threshold:
            selected.append(entry)
        else:
            excluded.append(entry)

    result = _cached_regex(
        tuple(e.item.name for e in selected),
        tuple(_item_text(e.item) for e in excluded),
        _known_names(repo, league),
    )

    with columns[1]:
        if result.ok:
            st.code(result.pattern, language="text")
        if result.exact:
            st.success(result.explain())
        else:
            st.markdown(
                f'<div class="warn">{result.explain()}</div>', unsafe_allow_html=True
            )

    missed = [*result.ambiguous, *result.unmatchable, *result.dropped]
    if missed:
        positions = {e.item.name: e.item.position_label() for e in selected}
        st.caption(
            "Not covered by the pattern: "
            + ", ".join(f"{n} ({positions.get(n, '?')})" for n in missed)
        )

    st.caption(
        f"Matched against **{len(selected)} of {len(priced)}** maps in the tab, on "
        "the suggested listing price shown on the tiles. The pattern is text for "
        "you to paste yourself — this tool never touches the game client."
    )


def _copy_tools(priced, fmt):
    supported = [p for p in priced if p.supported]
    if not supported:
        return
    block = "\n".join(
        f"{p.item.position_label()} -> {fmt.format(p.suggested_chaos)}" for p in supported
    )
    with st.expander("Copy all recommendations"):
        st.code(block, language="text")
        st.caption(
            "Prices are for you to type into the game yourself. This tool never "
            "modifies the game client or your stash."
        )


def _overlay_panel(cfg, priced, fmt, strategy, verdicts):
    """Hand the priced tab to the desktop overlay.

    The overlay is a second process -- tkinter cannot live inside a Streamlit
    rerun -- so this page's job is only to write the file it watches. See
    src/overlay/state.py for why the seam is a file.

    The launch button spawns that process **explicitly**, on a click, and says
    what it did: a GUI quietly appearing over the game because a checkbox was
    ticked is the sort of thing that gets a tool uninstalled. If the spawn
    fails, the command to type is on screen either way.
    """
    positioned = [p for p in priced if p.item.x is not None and p.item.y is not None]
    if not positioned:
        return

    st.markdown("### Draw this over the game")
    st.caption(
        "The overlay is a separate, click-through window that paints these same "
        "tiles on top of your real stash tab, so you can price out of the tab "
        "without looking back at the browser. It draws pixels and nothing else — "
        "it never reads or touches the game client."
    )

    calibration = Calibration.from_config(cfg)
    warning = resolution_warning(calibration, None)
    if warning:
        st.markdown(f'<div class="warn">{warning}</div>', unsafe_allow_html=True)

    columns = st.columns([1.1, 1, 2])
    if columns[0].button("Send this tab to the overlay", type="primary"):
        tab_names = sorted({p.item.stash_tab_name for p in positioned if p.item.stash_tab_name})
        path = write_state(
            state_from_priced(
                priced,
                fmt,
                strategy=strategy,
                league=cfg.get("league"),
                tab_names=tab_names,
                verdicts=verdicts,
            )
        )
        st.session_state["overlay_state_path"] = str(path)
        st.success(
            f"Wrote {len(positioned)} maps to `{path}`. A running overlay picks this "
            "up within half a second — no need to restart it."
        )

    if columns[1].button("Launch overlay window"):
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "src.cli", "overlay"],
                cwd=str(ROOT),
            )
        except OSError as exc:
            st.error(f"Could not start the overlay process: {exc}")
        else:
            st.success(
                f"Started the overlay as process {process.pid}. It opens its own "
                "window with a small control strip; close it from there."
            )

    columns[2].caption(
        "Or run it yourself, which is the better way if you want to see its "
        "errors:  \n`python -m src.cli overlay --calibrate` once to line the grid "
        "up with your stash tab, then `python -m src.cli overlay`."
    )


def _snapshot_button(cfg, repo, items, priced):
    if st.button("Save this scan as a snapshot"):
        snapshot_id = repo.save_snapshot(cfg.get("league"), items, source="ui")
        repo.save_predictions(
            snapshot_id,
            [
                {
                    "item_fingerprint": p.item.fingerprint(),
                    "features": p.item.features(),
                    "predicted_chaos": p.estimate.fair_value_chaos,
                    "suggested_chaos": p.suggested_chaos,
                    "interval_low_chaos": p.estimate.interval_low_chaos,
                    "interval_high_chaos": p.estimate.interval_high_chaos,
                    "confidence": p.estimate.confidence,
                    "model_version": p.estimate.model_version,
                }
                for p in priced
                if p.supported
            ],
        )
        st.success(f"Saved snapshot {snapshot_id}. You can re-open it later to compare prices.")
