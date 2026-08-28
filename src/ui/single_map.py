"""Single Map page: price one map from numbers or from copied item text."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.market.comparables import format_age
from src.parsing.item_parser import ItemParseError, classify, parse_item_text
from src.pricing.sell_strategy import STRATEGIES, STRATEGY_LABELS
from src.schema import FEATURE_NAMES, NormalizedMap
from src.ui import prefs
from src.ui.common import (
    get_config,
    get_service,
    page_header,
    render_confidence,
)
from src.ui.exalt_panel import render_verdict, verdict_for

#: The two ways of describing one map. Named so the remembered choice can be
#: checked against what is still on offer; the first is the default.
INPUT_MODES = ["Enter values", "Paste item text"]

LABELS = {
    "quantity": "Item Quantity",
    "rarity": "Item Rarity",
    "pack_size": "Monster Pack Size",
    "more_currency": "More Currency",
    "more_maps": "More Maps",
    "more_scarabs": "More Scarabs",
    "more_divination_cards": "More Divination Cards",
}


def render() -> None:
    cfg = get_config()
    service, model_error = get_service()
    fmt = service.formatter

    page_header(
        "Price",
        "Single Map",
        "One map, priced by the same model the whole tab uses -- for when you "
        "have an item in hand rather than a stash to work through.",
    )
    if model_error:
        st.markdown(f'<div class="warn">{model_error}</div>', unsafe_allow_html=True)

    mode = st.radio(
        "Input",
        INPUT_MODES,
        horizontal=True,
        key=prefs.sticky("single_map_input", INPUT_MODES[0], INPUT_MODES),
    )
    item: NormalizedMap | None = None

    if mode == "Enter values":
        cols = st.columns(4)
        values: dict[str, int] = {}
        for index, name in enumerate(FEATURE_NAMES):
            values[name] = cols[index % 4].number_input(
                LABELS[name], min_value=0, max_value=1000, value=0, step=1, key=f"sm_{name}"
            )
        item = NormalizedMap(
            base_type=f"Map (Tier {cfg.get('scope.map_tier')})",
            map_tier=cfg.get("scope.map_tier"),
            is_originator=True,
            **values,
        )
        item = classify(
            item,
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
    else:
        text = st.text_area(
            "Copied item text (Ctrl+C on the map in game)", height=280, key="sm_text"
        )
        if text.strip():
            try:
                item = classify(
                    parse_item_text(text),
                    map_tier=cfg.get("scope.map_tier"),
                    require_uncorrupted=cfg.get("scope.require_uncorrupted"),
                )
            except ItemParseError as exc:
                st.error(f"Could not parse that: {exc}")
                item = None
        if item is not None:
            st.caption(
                "Parsed: "
                + ", ".join(f"{LABELS[name]} {getattr(item, name)}" for name in FEATURE_NAMES)
            )

    if item is None:
        return

    strategy = st.selectbox(
        "Selling strategy",
        STRATEGIES,
        format_func=STRATEGY_LABELS.get,
        key=prefs.sticky("single_map_strategy", STRATEGIES[1], STRATEGIES),
    )
    if service is not None:
        st.caption(f"Strategy multipliers from {service.multipliers.source}")

    if not item.supported:
        st.warning(f"Unsupported: {item.unsupported_reason}")
        return
    if service is None or service.predictor is None:
        st.info("Train a model first (Settings -> Retrain model).")
        return

    priced = service.price(item, strategy=strategy)
    est = priced.estimate
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown(f"### {fmt.format(est.fair_value_chaos)}")
        st.caption("Estimated fair value")
        st.markdown(
            f"**Suggested listing:** {fmt.format(priced.suggested_chaos)}  \n"
            f"**Likely range:** "
            f"{fmt.range(est.interval_low_chaos, est.interval_high_chaos)}"
        )
        render_confidence(est.confidence, est.confidence_score)
        for reason in est.confidence_reasons:
            st.caption(f"- {reason}")
        st.markdown(f'<div class="note-box">{priced.note}</div>', unsafe_allow_html=True)
        st.caption("Copy this into the item's note in game yourself.")

    with right:
        st.markdown("**Closest recent listings**")
        if priced.comparables.empty:
            st.info("No comparable listings yet.")
        else:
            frame = priced.comparables.copy()
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
            if priced.market_reference_chaos:
                st.caption(
                    f"Median of comparables: **{fmt.format(priced.market_reference_chaos)}**"
                )

    st.markdown("---")
    render_verdict(verdict_for(priced.item), fmt)

    st.markdown("---")
    _explanation(priced, fmt)


def _explanation(priced, fmt) -> None:
    from src.ml.explain import explain_item
    from src.ui.common import get_predictor

    predictor, _ = get_predictor()
    if predictor is None:
        return
    st.subheader("Feature explanation")
    try:
        result = explain_item(predictor, priced.item)
    except Exception as exc:
        st.caption(f"Explanation unavailable: {exc}")
        return
    st.caption(
        f"Model-derived attribution ({result['method']}). These are the model's own "
        "contributions, not hand-written weights."
    )
    unit = fmt.unit.title()
    rows = [{"Component": "Estimated baseline", unit: fmt.value(result["baseline_chaos"])}]
    for part in result["contributions"]:
        if abs(part["contribution_chaos"]) < 1e-9:
            continue
        rows.append(
            {
                "Component": f"{part['label']} = {part['value']}",
                unit: fmt.value(part["contribution_chaos"]),
            }
        )
    rows.append({"Component": "Estimated value", unit: fmt.value(result["total_chaos"])})
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            unit: st.column_config.NumberColumn(
                unit, format="%+.0f" if fmt.unit == "chaos" else "%+.2f"
            )
        },
    )
