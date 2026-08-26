"""Model Analysis page: metrics, importance, residuals, interactions, history."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

from src.ml.explain import INTERESTING_INTERACTIONS, global_importance, interaction_grid
from src.ml.features import rows_to_frame, target_inverse
from src.ml.validation import metrics as compute_metrics
from src.ui.common import (
    CHART_SERIES,
    get_config,
    get_converter,
    get_formatter,
    get_predictor,
    get_repo,
    page_header,
    stat_rail,
)


def render() -> None:
    cfg = get_config()
    repo = get_repo()
    converter = get_converter(cfg, repo)
    fmt = get_formatter(cfg, converter)
    predictor, model_error = get_predictor()

    page_header(
        "Tool",
        "Model Analysis",
        "What the model learned, how wrong it tends to be, and where.",
    )
    if predictor is None:
        st.warning(model_error or "No trained model.")
        return

    meta = predictor.metadata
    rate = converter.divine_to_chaos  # only for the residual plot's own scaling

    stat_rail(
        [
            ("Version", meta["model_version"]),
            ("Backend", meta["backend"]),
            ("Target", meta["target"]),
            ("Fitted on", f"{int(meta['dataset_size']):,}", "listings"),
            (
                "Data window",
                meta.get("market_date_start", "?")[:10],
                f"to {meta.get('market_date_end', '?')[:10]}",
            ),
        ]
    )

    tabs = st.tabs(
        ["Validation", "Feature importance", "Prediction vs actual", "Interactions", "History"]
    )

    with tabs[0]:
        _validation(meta, fmt)
    with tabs[1]:
        _importance(predictor)
    with tabs[2]:
        _residuals(cfg, repo, predictor, fmt, rate)
    with tabs[3]:
        _interactions(predictor, fmt)
    with tabs[4]:
        _history(repo, fmt)


def _validation(meta, fmt) -> None:
    split = meta.get("split", "chronological (oldest train, newest test)")
    st.markdown(f"**Split used:** {split}")
    if "snapshot" in split:
        st.caption(
            "This dataset was collected in one sitting, so ordering by listing "
            "time would measure how long a listing has sat unsold rather than "
            "when it was observed -- training on stale leftovers and testing on "
            "fresh asks compares two different populations. Whole sellers are "
            "kept on one side of the split instead, so the model cannot score "
            "well by recognising a seller's flat price. Collect across several "
            "days and the split becomes chronological automatically."
        )
    else:
        st.caption(
            "70% oldest train, next 15% validation, newest 15% held-out test. "
            "Never random: a random split would let the model see the future."
        )
    frames = []
    for name in ("validation", "test"):
        block = meta["metrics"].get(name) or {}
        if block:
            frames.append({"split": name, **_readable(block, fmt)})
    if frames:
        st.dataframe(pd.DataFrame(frames), hide_index=True, use_container_width=True)

    rolling = meta["metrics"].get("rolling_folds") or []
    if rolling:
        st.markdown("**Rolling expanding-window folds**")
        st.dataframe(
            pd.DataFrame([_readable(f, fmt) for f in rolling]),
            hide_index=True,
            use_container_width=True,
        )

    comparison = meta["metrics"].get("backend_comparison") or []
    if comparison:
        st.markdown("**Backend comparison (on validation)**")
        st.dataframe(
            pd.DataFrame(
                [
                    {**{"backend": c["backend"], "weights": c["weights"]}, **_readable(c, fmt)}
                    for c in comparison
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )

    conformal = meta.get("conformal") or {}
    if conformal:
        st.markdown("**Prediction interval calibration**")
        st.caption(
            f"Split-conformal band on the ratio actual/predicted: "
            f"x{conformal.get('low', 0):.2f} to x{conformal.get('high', 0):.2f}, "
            f"calibrated on {conformal.get('calibrated_on', 0)} validation listings, "
            f"empirical coverage {conformal.get('coverage', float('nan')):.0f}%."
        )
    st.caption(
        f"Recency weighting in use: **{meta.get('recency_weights_name')}** "
        f"{meta.get('recency_weights')}"
    )


def _readable(block, fmt) -> dict:
    suffix = fmt.column_suffix()
    out = {
        "n": block.get("n"),
        f"MAE ({suffix})": _amount(block.get("mae"), fmt),
        f"Median AE ({suffix})": _amount(block.get("median_ae"), fmt),
        "R2": _round(block.get("r2")),
        "SMAPE %": _round(block.get("smape")),
    }
    if block.get("within_25pct") is not None:
        out["within 25%"] = _round(block.get("within_25pct"))
    if block.get("mape") is not None:
        out["MAPE %"] = _round(block.get("mape"))
    return out


def _amount(chaos, fmt):
    value = fmt.value(chaos)
    return None if value is None else round(value, 0 if fmt.unit == "chaos" else 3)


def _round(value):
    return None if value is None else round(float(value), 3)


def _importance(predictor) -> None:
    frame = global_importance(predictor)
    if frame.empty:
        st.info("This backend exposes no feature importance.")
        return
    st.bar_chart(frame.set_index("label")["importance"], height=320, color=CHART_SERIES)
    st.dataframe(frame, hide_index=True, use_container_width=True)
    st.caption(
        "Importance is whatever the trained backend reports. It says which features "
        "the model splits on, not what a map is 'worth' -- read the per-map "
        "explanation on the Single Map page for that."
    )


def _residuals(cfg, repo, predictor, fmt, rate) -> None:
    rows = repo.listing_rows(
        league=cfg.get("league"),
        include_outliers=False,
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    frame = rows_to_frame(rows, divine_to_chaos=rate)
    # The emptiness check has to come before the price filter, not after it:
    # with no listings at all rows_to_frame returns a frame with no columns
    # either, and asking it for price_chaos is a KeyError rather than an empty
    # result. That is the state a fresh install is in -- the release ships a
    # trained model but no database -- so this page reached it on first run.
    if frame.empty:
        st.info("No listings to score against.")
        return

    frame = frame[frame["price_chaos"].notna() & (frame["price_chaos"] > 0)]
    if frame.empty:
        st.info("No listings to score against.")
        return

    columns = predictor.columns
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        st.warning(f"Dataset is missing columns the model needs: {missing}")
        return

    predicted = target_inverse(
        np.asarray(predictor.model.predict(frame[columns]), dtype=float), predictor.target_mode
    )
    predicted = np.clip(predicted, 0.0, None)
    actual = frame["price_chaos"].to_numpy(dtype=float)

    st.caption(
        "In-sample for the rows the model trained on, so read this as a shape check, "
        "not an accuracy claim. The honest numbers are on the Validation tab."
    )
    unit = fmt.unit
    scale = 1.0 if unit == "chaos" else rate
    plot = pd.DataFrame({f"actual ({unit})": actual / scale, f"predicted ({unit})": predicted / scale})
    st.scatter_chart(
        plot, x=f"actual ({unit})", y=f"predicted ({unit})", height=340, color=CHART_SERIES
    )

    residual = pd.DataFrame(
        {f"predicted ({unit})": predicted / scale, "residual": (predicted - actual) / scale}
    )
    st.scatter_chart(
        residual, x=f"predicted ({unit})", y="residual", height=280, color=CHART_SERIES
    )
    st.dataframe(
        pd.DataFrame([_readable(compute_metrics(actual, predicted), fmt)]),
        hide_index=True,
        use_container_width=True,
    )


def _interactions(predictor, fmt) -> None:
    st.markdown(
        "Two-way partial dependence: the model's own output as two features are "
        "varied and everything else is held at its median."
    )
    pair = st.selectbox(
        "Interaction",
        INTERESTING_INTERACTIONS,
        format_func=lambda p: f"{p[0]} x {p[1]}",
    )
    grid = interaction_grid(predictor, pair[0], pair[1])
    if grid.empty:
        st.info("Not enough training data stored with the model.")
        return
    grid["amount"] = grid["predicted_chaos"].map(fmt.value)
    pivot = grid.pivot_table(index=pair[1], columns=pair[0], values="amount")
    st.dataframe(
        pivot.style.background_gradient(cmap="viridis").format(
            "{:.0f}" if fmt.unit == "chaos" else "{:.2f}"
        ),
        use_container_width=True,
    )
    st.caption(
        f"Rows: {pair[1]}. Columns: {pair[0]}. Cells: estimated value in {fmt.unit}."
    )


def _history(repo, fmt) -> None:
    runs = repo.model_runs(limit=25)
    if not runs:
        st.info("No training runs recorded.")
        return
    records = []
    for run in runs:
        test = json.loads(run["metrics"]).get("test", {})
        records.append(
            {
                "version": run["model_version"],
                "trained": run["trained_at"][:19],
                "backend": run["backend"],
                "rows": run["dataset_size"],
                f"MAE ({fmt.column_suffix()})": _amount(test.get("mae"), fmt),
                f"Median AE ({fmt.column_suffix()})": _amount(test.get("median_ae"), fmt),
                "R2": _round(test.get("r2")),
                "active": bool(run["is_active"]),
            }
        )
    st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True)
