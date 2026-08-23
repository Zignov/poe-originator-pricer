"""Stash Pricer -- the main workflow.

    choose a source -> choose tabs -> Analyze -> grid + table + comparables
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.market.comparables import format_age
from src.pricing.sell_strategy import STRATEGIES, STRATEGY_LABELS
from src.stash.base import StashSource
from src.stash.clipboard_import import ClipboardStashSource
from src.stash.json_import import JsonStashSource, parse_csv
from src.stash.official_api import SessionStashSource, StashAccessError
from src.pricing.fair_value import PricedMap
from src.ui.common import get_config, get_repo, get_service
from src.ui.exalt_panel import affix_label, affixes_for, render_verdict, verdict_for
from src.ui.stash_grid import render_grid

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

    st.title("Stash Pricer")
    if model_error:
        st.markdown(f'<div class="warn">{model_error}</div>', unsafe_allow_html=True)

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
    items = st.session_state.get("stash_items")
    if not items:
        return

    st.markdown("---")
    controls = st.columns([1.2, 1, 1, 1, 1])
    strategy = controls[0].selectbox(
        "Selling strategy", STRATEGIES, index=1, format_func=STRATEGY_LABELS.get
    )
    min_value = controls[1].number_input(
        f"Min value ({fmt.column_suffix()})", min_value=0.0, value=0.0,
        step=5.0 if fmt.unit == "chaos" else 0.5,
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

    priced = service.price_many(items, strategy=strategy)
    supported = [p for p in priced if p.supported]
    st.caption(
        f"{len(items)} maps found  |  {len(supported)} priceable  |  "
        f"{len(items) - len(supported)} unsupported  |  "
        f"strategy multipliers from {service.multipliers.source}"
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
        st.subheader("Stash layout")
        st.caption(
            f"Tiles show the suggested listing price on the "
            f"**{STRATEGY_LABELS[strategy].lower()}** strategy, outlined by whether "
            "an Exalted Orb pays for itself. Hover for fair value and the exalt call."
        )
        # Verdicts are keyed by object identity so the grid never has to know how
        # a map is priced -- ~2ms each, so a full tab costs about a tenth of a
        # second per rerun.
        verdicts = {id(p): verdict_for(p.item) for p in priced if p.supported}
        render_grid(priced, fmt, verdicts=verdicts)
        total = sum(p.suggested_chaos or 0 for p in supported)
        st.metric("Tab total (suggested)", fmt.format(total))

    with table_col:
        st.subheader("Maps")
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

    st.markdown("---")
    _detail_panel(visible, fmt)
    _copy_tools(visible, fmt)
    _snapshot_button(cfg, repo, items, priced)


# ---------------------------------------------------------------------------
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
        st.markdown(
            f"**Estimated fair value:** {fmt.format(est.fair_value_chaos)}  \n"
            f"**Suggested listing ({STRATEGY_LABELS[entry.strategy]}):** "
            f"{fmt.format(entry.suggested_chaos)}  \n"
            f"**Likely range:** "
            f"{fmt.range(est.interval_low_chaos, est.interval_high_chaos)}  \n"
            f"**Confidence:** {est.confidence} ({est.confidence_score:.2f})"
        )
        for reason in est.confidence_reasons:
            st.caption(f"- {reason}")
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
