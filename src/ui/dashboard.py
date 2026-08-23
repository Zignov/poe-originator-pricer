"""Dashboard page."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.ui.common import (
    get_config,
    get_converter,
    get_formatter,
    get_predictor,
    get_repo,
    metric_cards,
    price_histogram,
    typical_focus,
)


def render() -> None:
    cfg = get_config()
    repo = get_repo()
    converter = get_converter(cfg, repo)
    fmt = get_formatter(cfg, converter)
    predictor, model_error = get_predictor()

    st.title("Dashboard")
    st.caption(
        f"Pricing uncorrupted T{cfg.get('scope.map_tier')} Originator Maps in "
        f"**{cfg.get('league')}**."
        if cfg.get("scope.require_uncorrupted")
        else f"Pricing T{cfg.get('scope.map_tier')} Originator Maps in **{cfg.get('league')}**."
    )

    counts = repo.count_listings(cfg.get("league"))
    latest = repo.latest_capture()
    run = repo.active_model_run()
    rate = repo.get_divine_rate(cfg.get("league"))
    snapshots = repo.list_snapshots(limit=200)

    metrics = json.loads(run["metrics"])["test"] if run else {}
    mae_divine = metrics.get("mae")
    metric_cards(
        [
            {
                "label": "Listings in dataset",
                "value": f"{counts['total'] - counts['outliers']:,}",
                "sub": f"{counts['outliers']} flagged as outliers",
            },
            {
                "label": "Collected last 24h",
                "value": f"{counts['last_24h']:,}",
                "sub": _ago(latest),
            },
            {
                "label": "Divine / chaos",
                "value": f"{converter.divine_to_chaos:.0f}",
                "sub": f"as of {rate[1]:%Y-%m-%d %H:%M} UTC" if rate else "from config",
            },
            {
                "label": "Model MAE",
                "value": fmt.format(mae_divine) if mae_divine else "-",
                "sub": f"median {fmt.format(metrics['median_ae'])}"
                if metrics.get("median_ae")
                else "no model trained",
            },
            {
                "label": "Stash snapshots",
                "value": f"{len(snapshots)}",
                "sub": f"last {_ago(_parse(snapshots[0]['created_at']))}" if snapshots else "none yet",
            },
            {
                "label": "Model trained",
                "value": predictor.version if predictor else "-",
                "sub": _ago(_parse(run["trained_at"])) if run else "run Retrain in Settings",
            },
        ]
    )

    if model_error:
        st.markdown(f'<div class="warn">{model_error}</div>', unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Price distribution")
        rows = repo.listing_rows(league=cfg.get("league"), include_outliers=False)
        if not rows:
            st.info("No market data yet. Collect some on the Market Data page.")
        else:
            chaos = [r["price_chaos"] for r in rows if r["price_chaos"]]
            prices = pd.DataFrame({"amount": [fmt.value(c) for c in chaos]})
            st.bar_chart(price_histogram(prices["amount"], focus=typical_focus(cfg, fmt)), height=260)
            st.caption(f"Buckets are upper edges, in {fmt.unit}.")
            median = float(pd.Series(chaos).median())
            p90 = float(pd.Series(chaos).quantile(0.9))
            st.caption(
                f"{len(chaos)} priced listings, median {fmt.format(median)}, "
                f"90th percentile {fmt.format(p90)}"
            )

    with right:
        st.subheader("Next step")
        if counts["total"] < 200:
            st.warning(
                "Collect more market data before trusting predictions. "
                "Open **Market Data** in the sidebar and click *Collect from trade API*."
            )
        elif predictor is None:
            st.warning("Market data is present but no model has been trained yet.")
        else:
            st.success("Ready. Open **Stash Pricer** to price a tab, or **Single Map** for one item.")
        st.markdown("---")
        st.markdown(
            "**Reminder.** Every number is an estimate from *asking* prices, not "
            "completed sales. Always cross-check against the comparable listings "
            "shown next to each prediction."
        )


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ago(when: datetime | None) -> str:
    if when is None:
        return "never"
    delta = datetime.now(timezone.utc) - when
    seconds = delta.total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"
