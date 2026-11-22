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
from src.pricing.exalt import ExaltPolicy, ExaltVerdict, evaluate_exalt
from src.schema import NormalizedMap
from src.ui.common import get_catalogue, get_config, get_exalt_cost, get_predictor


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
        policy=ExaltPolicy.from_config(get_config()),
    )


def render_verdict(verdict: ExaltVerdict, fmt: PriceFormatter) -> None:
    """The exalt judgement for one map."""
    st.markdown("**Exalting this map**")

    if not verdict.available:
        headline, why = verdict.headline()
        st.caption(f"{verdict.affix_label} — {verdict.reason}.")
        # Amber rather than the yes/no colours: "we will not judge this" is a
        # third answer, and dressing it as a refusal would read as "no".
        st.markdown(
            f'<div class="exalt-unknown"><b>{headline}</b> {why}</div>',
            unsafe_allow_html=True,
        )
        return

    affixes = verdict.affixes
    st.caption(
        f"{affixes.label()}  ·  {verdict.slot_label()}  ·  {verdict.cost_label()}"
    )

    headline, why = verdict.headline()
    css = "exalt-yes" if verdict.worth_it else "exalt-no"
    st.markdown(
        f'<div class="{css}"><b>{headline}</b> {why}</div>', unsafe_allow_html=True
    )

    # Deliberately drawn whether the verdict passed or failed. A rejection is
    # exactly the case where the range and the odds are worth reading, and the
    # panel used to show *less* on the maps it had just told you not to touch.
    left, right = st.columns(2)
    with left:
        st.markdown(
            f"**Likely range:** {fmt.format(verdict.low_gain_chaos)} to "
            f"{fmt.format(verdict.high_gain_chaos)} gain  \n"
            f"**Chance it beats the cost:** {verdict.chance_of_profit:.0%}  \n"
            f"**Bar to clear:** {fmt.format(verdict.required_gain_chaos)} net"
            + (
                f"  \n**Model can resolve:** ±{fmt.format(verdict.resolution_chaos)}"
                if verdict.resolution_chaos
                else ""
            )
        )
    with right:
        st.markdown(
            f"**Best case:** {verdict.best.affix.label()} — "
            f"{fmt.format(verdict.best.gain_chaos)}  \n"
            f"**Worst case:** {verdict.worst.affix.label()} — "
            f"{fmt.format(verdict.worst.gain_chaos)}"
        )

    if verdict.sampled:
        st.caption(
            f"The verdict above prices all {verdict.orbs} slots, over "
            f"{verdict.sample_count} sampled fills -- enumerating every "
            "combination of that many affixes is out of reach. The table below "
            "is the next orb alone, and is exact."
        )

    label = (
        "What the next orb alone could roll"
        if verdict.orbs > 1
        else "Every affix that could roll"
    )
    with st.expander(f"{label} ({len(verdict.outcomes)})"):
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
            width="stretch",
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
