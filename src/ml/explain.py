"""Per-map feature attribution.

Every number here comes out of the trained model. There are no hand-written
weights anywhere in this file -- that is the whole point of it.

SHAP is used when available. When it is not, the fallback is a leave-one-feature-
out attribution: each feature is replaced by its dataset median and the change in
the model's own output is reported. It is cruder than SHAP but still model-
derived, and it is labelled as such so the UI can say which method produced it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from src.schema import FEATURE_LABELS as SCHEMA_FEATURE_LABELS
from src.schema import FEATURE_NAMES

if TYPE_CHECKING:  # pragma: no cover
    from src.ml.predict import Predictor
    from src.schema import NormalizedMap

#: Re-exported from the schema, which is the single place a reward's display
#: name is decided. It used to be a second copy of the same dictionary, and
#: that is how Covens' rename would have left the explanations saying
#: "More Scarabs" about a thread count long after the parser had moved on.
FEATURE_LABELS = SCHEMA_FEATURE_LABELS


def explain_item(predictor: "Predictor", item: "NormalizedMap") -> dict[str, Any]:
    """Baseline + per-feature contributions, all in chaos."""
    try:
        return _shap_explanation(predictor, item)
    except Exception:
        return _ablation_explanation(predictor, item)


def _shap_explanation(predictor: "Predictor", item: "NormalizedMap") -> dict[str, Any]:
    import shap

    row = predictor.frame_for([item])
    background = predictor.training_features
    if background.size == 0:
        raise RuntimeError("no training data stored with the model")

    # Cached on the predictor, which is itself a cached resource: building a
    # TreeExplainer walks the whole forest, and the model it is built from
    # cannot change without the predictor being replaced.
    explainer = getattr(predictor, "_shap_explainer", None)
    if explainer is None:
        explainer = shap.TreeExplainer(predictor.model)
        predictor._shap_explainer = explainer
    values = explainer.shap_values(row)
    values = np.asarray(values).reshape(-1)
    base = float(np.asarray(explainer.expected_value).reshape(-1)[0])

    # Contributions live in the model's target space. Convert by walking the
    # cumulative sum back through the inverse transform, so the reported chaos
    # deltas actually add up to the prediction.
    return _to_chaos_space(predictor, item, base, values, row, method="shap")


def _ablation_explanation(predictor: "Predictor", item: "NormalizedMap") -> dict[str, Any]:
    row = predictor.frame_for([item])
    background = predictor.training_features
    if background.size == 0:
        medians = np.zeros(len(predictor.columns))
    else:
        medians = np.median(background, axis=0)


    baseline_row = pd.DataFrame([medians], columns=predictor.columns)
    base_raw = float(predictor.model.predict(baseline_row)[0])
    full_raw = float(predictor.model.predict(row)[0])

    contributions = []
    current = medians.copy()
    running = base_raw
    for name in predictor.columns:
        pos = predictor.columns.index(name)
        current[pos] = row.iloc[0][name]
        step_raw = float(predictor.model.predict(pd.DataFrame([current], columns=predictor.columns))[0])
        contributions.append(step_raw - running)
        running = step_raw

    # Distribute any residual so the parts sum to the actual prediction.
    residual = full_raw - running
    if contributions:
        contributions[-1] += residual

    return _to_chaos_space(
        predictor, item, base_raw, np.array(contributions), row, method="ablation"
    )


def _to_chaos_space(
    predictor: "Predictor",
    item: "NormalizedMap",
    base_raw: float,
    contributions: np.ndarray,
    row: pd.DataFrame,
    method: str,
) -> dict[str, Any]:
    from src.ml.features import target_inverse

    mode = predictor.target_mode
    base_chaos = float(target_inverse(np.array([base_raw]), mode)[0])

    running_raw = base_raw
    parts: list[dict[str, Any]] = []
    previous_chaos = base_chaos
    for name, delta in zip(predictor.columns, contributions):
        running_raw += float(delta)
        chaos_now = float(target_inverse(np.array([running_raw]), mode)[0])
        parts.append(
            {
                "feature": name,
                "label": FEATURE_LABELS.get(name, name),
                "value": int(row.iloc[0][name]) if name in FEATURE_NAMES else row.iloc[0][name],
                "contribution_chaos": chaos_now - previous_chaos,
            }
        )
        previous_chaos = chaos_now

    total_chaos = previous_chaos
    parts.sort(key=lambda p: abs(p["contribution_chaos"]), reverse=True)
    return {
        "method": method,
        "baseline_chaos": base_chaos,
        "contributions": parts,
        "total_chaos": total_chaos,
    }


def global_importance(predictor: "Predictor") -> pd.DataFrame:
    """Model-wide feature importance, using whatever the backend exposes."""
    model = predictor.model
    importances = getattr(model, "feature_importances_", None)
    if importances is None and hasattr(model, "get_feature_importance"):
        importances = model.get_feature_importance()
    if importances is None and hasattr(model, "coef_"):
        importances = np.abs(np.asarray(model.coef_).reshape(-1))
    if importances is None:
        return _permutation_importance(predictor)

    frame = pd.DataFrame(
        {"feature": predictor.columns, "importance": np.asarray(importances, dtype=float)}
    )
    frame["label"] = frame["feature"].map(lambda f: FEATURE_LABELS.get(f, f))
    return frame.sort_values("importance", ascending=False).reset_index(drop=True)


def _permutation_importance(predictor: "Predictor") -> pd.DataFrame:
    """Fallback for backends without a native importance attribute."""
    X = pd.DataFrame(predictor.training_features, columns=predictor.columns)
    if X.empty:
        return pd.DataFrame(columns=["feature", "importance", "label"])
    base = predictor.model.predict(X)
    rng = np.random.default_rng(7)
    scores = []
    for name in predictor.columns:
        shuffled = X.copy()
        shuffled[name] = rng.permutation(shuffled[name].to_numpy())
        scores.append(float(np.mean(np.abs(predictor.model.predict(shuffled) - base))))
    frame = pd.DataFrame({"feature": predictor.columns, "importance": scores})
    frame["label"] = frame["feature"].map(lambda f: FEATURE_LABELS.get(f, f))
    return frame.sort_values("importance", ascending=False).reset_index(drop=True)


def interaction_grid(
    predictor: "Predictor", feature_a: str, feature_b: str, steps: int = 9
) -> pd.DataFrame:
    """Two-way partial dependence: the model's own view of an interaction."""
    X = pd.DataFrame(predictor.training_features, columns=predictor.columns)
    if X.empty:
        return pd.DataFrame()
    from src.ml.features import target_inverse

    medians = X.median()
    a_values = np.linspace(X[feature_a].quantile(0.05), X[feature_a].quantile(0.95), steps)
    b_values = np.linspace(X[feature_b].quantile(0.05), X[feature_b].quantile(0.95), steps)

    rows = []
    for a in a_values:
        for b in b_values:
            point = medians.copy()
            point[feature_a] = a
            point[feature_b] = b
            rows.append(point)
    grid = pd.DataFrame(rows, columns=predictor.columns)
    predicted = target_inverse(
        np.asarray(predictor.model.predict(grid), dtype=float), predictor.target_mode
    )
    out = grid[[feature_a, feature_b]].copy()
    out["predicted_chaos"] = np.clip(predicted, 0.0, None)
    return out


#: Combinations worth inspecting, per the project brief.
INTERESTING_INTERACTIONS: list[tuple[str, str]] = [
    ("more_currency", "more_maps"),
    ("more_currency", "pack_size"),
    ("more_maps", "pack_size"),
    ("more_scarabs", "pack_size"),
    ("more_divination_cards", "pack_size"),
    ("quantity", "pack_size"),
]
