"""Regression backends.

Standard tabular regression only -- no neural networks. Each backend is optional
except sklearn_hgb, which ships with scikit-learn and is always available, so the
application still trains if none of the boosting libraries are installed.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

BackendFactory = Callable[[dict[str, Any]], Any]


def _monotone(params: dict[str, Any]) -> list[int] | None:
    """Per-column monotonicity vector, or None when unconstrained.

    Passed through `params` rather than read from the schema so a backend stays
    a pure function of its parameters and the tests can build one either way.
    """
    constraints = params.get("monotone")
    if not constraints or not any(constraints):
        return None
    return list(constraints)


def _as_tuple(constraints: list[int] | None) -> tuple[int, ...] | None:
    return None if constraints is None else tuple(constraints)


def _catboost(params: dict[str, Any]):
    from catboost import CatBoostRegressor

    return CatBoostRegressor(
        iterations=params.get("iterations", 900),
        depth=params.get("depth", 6),
        learning_rate=params.get("learning_rate", 0.05),
        loss_function="RMSE",
        random_seed=params.get("seed", 7),
        verbose=False,
        allow_writing_files=False,
        monotone_constraints=_monotone(params),
    )


def _lightgbm(params: dict[str, Any]):
    from lightgbm import LGBMRegressor

    return LGBMRegressor(
        n_estimators=params.get("iterations", 900),
        learning_rate=params.get("learning_rate", 0.05),
        num_leaves=params.get("num_leaves", 31),
        random_state=params.get("seed", 7),
        verbose=-1,
        monotone_constraints=_monotone(params),
    )


def _xgboost(params: dict[str, Any]):
    from xgboost import XGBRegressor

    return XGBRegressor(
        n_estimators=params.get("iterations", 900),
        learning_rate=params.get("learning_rate", 0.05),
        max_depth=params.get("depth", 6),
        random_state=params.get("seed", 7),
        verbosity=0,
        tree_method="hist",
        # XGBoost accepts a tuple or a {feature: direction} dict here, but not a
        # list -- a list reaches a code path that calls .keys() on it.
        monotone_constraints=_as_tuple(_monotone(params)),
    )


def _catboost_mae(params: dict[str, Any]):
    """Absolute-error objective.

    Model selection scores on median absolute error, and listing prices are
    long-tailed even after log1p, so squared error still chases the few very
    expensive asks. Optimising the metric that is actually used is worth trying
    rather than assuming RMSE is fine.
    """
    from catboost import CatBoostRegressor

    return CatBoostRegressor(
        iterations=params.get("iterations", 900),
        depth=params.get("depth", 6),
        learning_rate=params.get("learning_rate", 0.05),
        loss_function="MAE",
        random_seed=params.get("seed", 7),
        verbose=False,
        allow_writing_files=False,
        monotone_constraints=_monotone(params),
    )


class ConstraintUnsupported(RuntimeError):
    """A backend cannot honour the monotonicity the schema requires."""


def _lightgbm_mae(params: dict[str, Any]):
    """LightGBM's L1 objective cannot carry monotone constraints.

    Refused rather than quietly built unconstrained. An unconstrained model that
    wins selection would reintroduce exactly the defect the constraints exist to
    prevent -- and it would win on the metric that selection scores, because an
    unconstrained fit is always at least as free as a constrained one.
    """
    from lightgbm import LGBMRegressor

    if _monotone(params) is not None:
        raise ConstraintUnsupported(
            "lightgbm_mae cannot combine monotone constraints with its L1 "
            "objective; use lightgbm or catboost_mae instead"
        )
    return LGBMRegressor(
        n_estimators=params.get("iterations", 900),
        learning_rate=params.get("learning_rate", 0.05),
        num_leaves=params.get("num_leaves", 31),
        objective="l1",
        random_state=params.get("seed", 7),
        verbose=-1,
    )


def _sklearn_hgb(params: dict[str, Any]):
    """The always-available fallback, so its constraint support is probed.

    `monotonic_cst` arrived in scikit-learn 1.4. This backend has to keep working
    on older versions -- it is the one that ships with scikit-learn and is the
    reason the app trains at all when no boosting library is installed -- so an
    unsupported keyword degrades to an unconstrained model rather than an error.
    """
    import inspect

    from sklearn.ensemble import HistGradientBoostingRegressor

    kwargs: dict[str, Any] = {
        "max_iter": params.get("iterations", 500),
        "learning_rate": params.get("learning_rate", 0.06),
        "max_depth": params.get("depth", None),
        "random_state": params.get("seed", 7),
    }
    constraints = _monotone(params)
    if constraints is not None and "monotonic_cst" in inspect.signature(
        HistGradientBoostingRegressor
    ).parameters:
        kwargs["monotonic_cst"] = constraints
    return HistGradientBoostingRegressor(**kwargs)


def _random_forest(params: dict[str, Any]):
    from sklearn.ensemble import RandomForestRegressor

    return RandomForestRegressor(
        n_estimators=params.get("iterations", 400),
        random_state=params.get("seed", 7),
        n_jobs=-1,
    )


def _extra_trees(params: dict[str, Any]):
    from sklearn.ensemble import ExtraTreesRegressor

    return ExtraTreesRegressor(
        n_estimators=params.get("iterations", 400),
        random_state=params.get("seed", 7),
        n_jobs=-1,
    )


def _linear(params: dict[str, Any]):
    from sklearn.linear_model import Ridge

    return Ridge(alpha=params.get("alpha", 1.0))


BACKENDS: dict[str, BackendFactory] = {
    "catboost": _catboost,
    "catboost_mae": _catboost_mae,
    "lightgbm": _lightgbm,
    "lightgbm_mae": _lightgbm_mae,
    "xgboost": _xgboost,
    "sklearn_hgb": _sklearn_hgb,
    "random_forest": _random_forest,
    "extra_trees": _extra_trees,
    "linear": _linear,
}

#: Always tried alongside the configured backends, as a sanity baseline.
BASELINE_BACKEND = "linear"


def available_backends() -> list[str]:
    """Backends whose library actually imports in this environment."""
    out: list[str] = []
    for name, factory in BACKENDS.items():
        try:
            factory({})
            out.append(name)
        except Exception:
            continue
    return out


def build(name: str, params: dict[str, Any] | None = None):
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; known: {sorted(BACKENDS)}")
    return BACKENDS[name](params or {})


def fit(model, X, y, weights: np.ndarray | None = None):
    """Fit with sample weights where the backend supports them."""
    try:
        model.fit(X, y, sample_weight=weights)
    except TypeError:
        model.fit(X, y)
    return model
