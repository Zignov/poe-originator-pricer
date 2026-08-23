"""More reward must never predict a lower price.

Every one of the seven reward features is "more is never worse" in the game. A
gradient-boosted tree does not know that, and an unconstrained model trained on
noisy asking prices priced a strictly-better map *lower* 11% of the time, by as
much as 77 chaos. That is invisible when pricing one map at a time and fatal to
the exalt calculation, which is entirely a comparison between a map and a
strictly better version of itself.

These tests train each backend on data that actively rewards violating the
constraint, then assert it holds anyway -- so a backend whose constraint is not
actually wired through cannot pass.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml import models as backends
from src.ml.features import feature_columns, monotone_constraints
from src.schema import FEATURE_NAMES

CONSTRAINABLE = ("catboost", "lightgbm", "xgboost", "sklearn_hgb")


def test_the_seven_reward_features_are_all_constrained():
    constraints = monotone_constraints(list(FEATURE_NAMES))
    assert constraints == [1] * len(FEATURE_NAMES)


def test_context_columns_are_left_unconstrained():
    """Nothing says a later hour of day should mean a higher price."""
    columns = feature_columns(["item_level", "hour_of_day"])
    constraints = monotone_constraints(columns)
    assert constraints[: len(FEATURE_NAMES)] == [1] * len(FEATURE_NAMES)
    assert constraints[len(FEATURE_NAMES) :] == [0, 0]


def test_constraints_follow_column_order_not_declaration_order():
    reordered = ["pack_size", "quality", "quantity"]
    assert monotone_constraints(reordered) == [1, 0, 1]


# ---------------------------------------------------------------------------
def _adversarial_frame(n: int = 600, seed: int = 3):
    """Data where price *falls* as quantity rises, to make violation attractive."""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {name: rng.integers(0, 100, size=n).astype(float) for name in FEATURE_NAMES}
    )
    price = 500.0 - 3.0 * frame["quantity"] + rng.normal(0, 5, size=n)
    return frame[list(FEATURE_NAMES)], np.clip(price.to_numpy(), 1.0, None)


@pytest.mark.parametrize("name", CONSTRAINABLE)
def test_a_constrained_backend_never_prices_more_quantity_lower(name):
    if name not in backends.available_backends():
        pytest.skip(f"{name} is not installed")

    X, y = _adversarial_frame()
    model = backends.build(name, {"monotone": monotone_constraints(list(X.columns))})
    backends.fit(model, X, y, np.ones(len(y)))

    probe = pd.DataFrame([{name_: 50.0 for name_ in FEATURE_NAMES} for _ in range(20)])
    probe["quantity"] = np.arange(0, 100, 5, dtype=float)
    predictions = np.asarray(model.predict(probe[list(FEATURE_NAMES)]), dtype=float)

    drops = np.diff(predictions)
    assert (drops >= -1e-6).all(), f"{name} decreased by {drops.min():.3f} as quantity rose"


@pytest.mark.parametrize("name", CONSTRAINABLE)
def test_the_same_backend_unconstrained_does_violate_it(name):
    """Proves the assertion above is testing the constraint, not the data."""
    if name not in backends.available_backends():
        pytest.skip(f"{name} is not installed")

    X, y = _adversarial_frame()
    model = backends.build(name, {})
    backends.fit(model, X, y, np.ones(len(y)))

    probe = pd.DataFrame([{name_: 50.0 for name_ in FEATURE_NAMES} for _ in range(20)])
    probe["quantity"] = np.arange(0, 100, 5, dtype=float)
    predictions = np.asarray(model.predict(probe[list(FEATURE_NAMES)]), dtype=float)
    assert np.diff(predictions).min() < -1e-6


def test_a_backend_that_cannot_be_constrained_refuses_rather_than_silently_unconstrains():
    """An unconstrained model must never win selection by being freer than the rest."""
    if "lightgbm_mae" not in backends.available_backends():
        pytest.skip("lightgbm is not installed")
    with pytest.raises(backends.ConstraintUnsupported):
        backends.build("lightgbm_mae", {"monotone": monotone_constraints(list(FEATURE_NAMES))})
    # Still usable when nothing is being constrained.
    assert backends.build("lightgbm_mae", {}) is not None
