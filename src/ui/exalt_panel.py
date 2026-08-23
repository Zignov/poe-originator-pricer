"""Rendering of the affix count and the exalt-worthy verdict.

Kept apart from the pages so the Stash Pricer and the Single Map view show the
same thing, and so the wording of a judgement about spending currency lives in
one place.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.parsing.affixes import AffixSet, extract_affixes
from src.pricing.display import PriceFormatter
from src.pricing.exalt import ExaltVerdict, evaluate_exalt
from src.schema import NormalizedMap
from src.ui.common import get_catalogue, get_exalt_cost, get_predictor


def affixes_for(item: NormalizedMap) -> AffixSet:
    """Affix structure of a map, from the payload its source preserved.

    Trade payloads name every affix outright. Stash payloads carry modifier text
    and nothing else, so those go through the catalogue, which resolves the text
    against what it learned from the market and checks the reading against the
    map's own Item Quantity.
    """
    payload = item.raw_item_data or {}
    catalogue = get_catalogue()
    if catalogue is not None:
        return catalogue.read_affixes(payload)
    return extract_affixes(payload)


def affix_label(item: NormalizedMap) -> str:
    """Short column text: '5/6', or '-' when the source cannot tell us."""
    affixes = affixes_for(item)
    return f"{affixes.count}/{affixes.max_affixes}" if affixes.known else "-"


def verdict_for(item: NormalizedMap, divine_to_chaos: float | None = None) -> ExaltVerdict:
    predictor, _ = get_predictor()
    return evaluate_exalt(
        item,
        affixes_for(item),
        get_catalogue(),
        predictor,
        cost_chaos=get_exalt_cost(),
        divine_to_chaos=divine_to_chaos,
    )


def render_verdict(verdict: ExaltVerdict, fmt: PriceFormatter) -> None:
    """The exalt judgement for one map."""
    st.markdown("**Exalting this map**")

    if not verdict.available:
        label = verdict.affix_label
        st.caption(f"{label} — {verdict.reason}.")
        return

    affixes = verdict.affixes
    headline = (
        f"{affixes.label()}  ·  {verdict.slot_label()}  ·  "
        f"orb costs {fmt.format(verdict.cost_chaos)}"
    )
    st.caption(headline)

    if verdict.worth_it:
        st.markdown(
            f'<div class="exalt-yes"><b>Worth exalting.</b> Expected '
            f"{fmt.format(verdict.expected_value_chaos)} after, a net gain of "
            f"<b>{fmt.format(verdict.net_gain_chaos)}</b> once the orb is paid for."
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="exalt-no"><b>Not worth the orb.</b> Expected gain '
            f"{fmt.format(verdict.expected_gain_chaos)} against a "
            f"{fmt.format(verdict.cost_chaos)} orb."
            "</div>",
            unsafe_allow_html=True,
        )

    left, right = st.columns(2)
    with left:
        st.markdown(
            f"**Likely range:** {fmt.format(verdict.low_gain_chaos)} to "
            f"{fmt.format(verdict.high_gain_chaos)} gain  \n"
            f"**Chance it beats the orb:** {verdict.chance_of_profit:.0%}  \n"
            f"**Outcomes considered:** {len(verdict.outcomes)}"
        )
    with right:
        st.markdown(
            f"**Best case:** {verdict.best.affix.label()} — "
            f"{fmt.format(verdict.best.gain_chaos)}  \n"
            f"**Worst case:** {verdict.worst.affix.label()} — "
            f"{fmt.format(verdict.worst.gain_chaos)}"
        )

    with st.expander(f"Every affix that could roll ({len(verdict.outcomes)})"):
        frame = pd.DataFrame(
            [
                {
                    "Affix": o.affix.label(),
                    "Slot": o.affix.slot,
                    "Chance": o.probability * 100.0,
                    "Gain": fmt.value(o.gain_chaos),
                    "Then worth": fmt.value(o.value_chaos),
                }
                for o in sorted(verdict.outcomes, key=lambda o: -o.gain_chaos)
            ]
        )
        digits = 0 if fmt.unit == "chaos" else 2
        st.dataframe(
            frame,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Chance": st.column_config.NumberColumn("Chance", format="%.1f%%"),
                "Gain": st.column_config.NumberColumn(
                    f"Gain ({fmt.column_suffix()})", format=f"%+.{digits}f"
                ),
                "Then worth": st.column_config.NumberColumn(
                    f"Then worth ({fmt.column_suffix()})", format=f"%.{digits}f"
                ),
            },
        )
        st.caption(
            "Chance is how often each affix appears in the collected market, among "
            "those that could still roll into an open slot. Every outcome is priced "
            "by the same model that priced the map."
        )
