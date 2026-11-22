"""Does predicting a low quantile beat the current central estimate?

The claim under test: a trade-search snapshot samples *asking* prices, which are
the upper envelope of what things actually sell for, so the model should predict
roughly the 0.25 quantile rather than a central value.

**Read this before reading the numbers.** MAE and MAPE cannot settle the claim,
and reporting them alone would be misleading. Those metrics are minimised by
predicting the conditional median, so a model deliberately aimed below the
median *must* score worse on them -- not because it is wrong, but because it is
answering a different question. Confirming that the q0.25 model has a worse MAE
tells you nothing except that the loss functions differ.

What can actually be measured, without sale data:

1. **Where each prediction sits in the distribution of asks.** For every held-out
   row, what fraction of comparable asks fall below the prediction. A central
   model should land near 0.50, a q0.25 model near 0.25. This says whether each
   model is where it claims to be.
2. **Whether the existing `quick_sell` strategy already lands there.** This is
   the decision. The app already shows three prices, and `quick_sell` is a
   multiplier on the central estimate fitted to observed seller behaviour. If it
   already sits near the 0.25 quantile, an explicit quantile model adds a second
   mechanism for something the code already does.
3. **The cost in the direction that matters.** `thrown_away_pct`: sellable maps
   predicted below the reroll line. A model biased low will throw away more of
   them, and that is the real risk of the change.

What *cannot* be measured here: whether a lower ask actually sells faster. That
needs sale or delisting data, which this dataset does not contain. The honest
answer to "is q0.25 closer to a transactable price" is that it is unverifiable
from asks alone -- so this script reports position, not accuracy, and the
decision should be made on (2) and (3).

    python scripts/quantile_experiment.py
    python scripts/quantile_experiment.py --alpha 0.25 --seeds 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.database.repository import Repository  # noqa: E402
from src.ml import models as backends  # noqa: E402
from src.ml.decision_bands import (  # noqa: E402
    REROLL_BELOW_CHAOS,
    decision_metrics,
    value_weights,
)
from src.ml.features import (  # noqa: E402
    bulk_listing_weights,
    feature_columns,
    monotone_constraints,
    recency_weights,
    target_inverse,
    target_transform,
)
from src.ml.train import load_training_frame  # noqa: E402
from src.ml.validation import choose_split, metrics  # noqa: E402


def _quantile_model(alpha: float, monotone):
    """CatBoost with a pinball objective.

    Built directly rather than through `backends.build`, because every factory
    in src/ml/models.py hardcodes its `loss_function` and silently ignores an
    unknown parameter -- which is exactly how the first version of this script
    "compared" two identical models. Everything else is kept the same as
    `_catboost_mae` so the only difference between the arms is the objective.
    """
    from catboost import CatBoostRegressor

    return CatBoostRegressor(
        iterations=900,
        depth=6,
        learning_rate=0.05,
        loss_function=f"Quantile:alpha={alpha:g}",
        random_seed=7,
        verbose=False,
        allow_writing_files=False,
        monotone_constraints=monotone,
    )


def _fit(frame, columns, target_mode, idx_train, model):
    X = frame.loc[idx_train, columns]
    y_raw = frame.loc[idx_train, "price_chaos"].to_numpy(dtype=float)
    weights = recency_weights(frame.loc[idx_train, "observed_at"], [(0.0, 1.0)])
    weights = weights * bulk_listing_weights(frame.loc[idx_train])
    weights = weights * value_weights(y_raw)
    backends.fit(model, X, target_transform(y_raw, target_mode), weights)
    return model


def _predict(model, frame, columns, idx, target_mode):
    raw = np.asarray(model.predict(frame.loc[idx, columns]), dtype=float)
    return np.clip(target_inverse(raw, target_mode), 0.0, None)


def _distribution_position(frame, columns, idx, preds, neighbours=40):
    """For each row: the fraction of similar maps' asks below the prediction.

    "Similar" is nearest-neighbour in the scaled feature space -- the same
    notion of comparable the Predictor already uses for confidence. A central
    estimate should average near 0.50 and a q0.25 model near 0.25.
    """
    X = frame[columns].to_numpy(dtype=float)
    prices = frame["price_chaos"].to_numpy(dtype=float)
    spread = X.std(axis=0)
    scale = np.where(spread > 1e-6, spread, 1.0)
    target = frame.loc[idx, columns].to_numpy(dtype=float)

    positions = []
    for row, pred in zip(target, preds):
        distance = np.linalg.norm((X - row) / scale, axis=1)
        near = prices[np.argsort(distance)[:neighbours]]
        if len(near):
            positions.append(float((near < pred).mean()))
    return float(np.mean(positions)) if positions else float("nan")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha", type=float, default=0.25, help="quantile to test")
    parser.add_argument("--backend", default="catboost_mae", help="backend to use for both models")
    args = parser.parse_args(argv)

    cfg = load_config()
    repo = Repository(cfg.db_path)
    frame = load_training_frame(cfg, repo)
    columns = feature_columns([])
    target_mode = str(cfg.get("model.target", "log1p"))

    idx_train, idx_valid, idx_test, how = choose_split(frame)
    idx_train = idx_train.union(idx_valid)
    print(f"rows: {len(frame)}   split: {how}")
    print(f"train: {len(idx_train)}   test: {len(idx_test)}\n")

    y_test = frame.loc[idx_test, "price_chaos"].to_numpy(dtype=float)

    monotone = monotone_constraints(columns)
    runs = {
        f"current ({args.backend})": lambda: backends.build(
            args.backend, {"monotone": monotone}
        ),
    }
    for alpha in sorted({args.alpha, 0.5}):
        runs[f"quantile a={alpha:g}"] = (
            lambda a=alpha: _quantile_model(a, monotone)
        )

    rows = []
    for label, make in runs.items():
        try:
            model = _fit(frame, columns, target_mode, idx_train, make())
        except Exception as exc:
            print(f"{label}: could not fit ({type(exc).__name__}: {exc})")
            continue
        pred = _predict(model, frame, columns, idx_test, target_mode)
        stats = metrics(y_test, pred)
        # The project's own decision-oriented metrics, so these numbers are
        # comparable with what training already reports rather than a parallel
        # definition of the same ideas.
        decision = decision_metrics(y_test, pred)
        rows.append(
            {
                "model": label,
                "MAE": stats["mae"],
                "medAE": stats["median_ae"],
                "SMAPE %": stats["smape"],
                "sellable MAPE %": decision.get("sell_mape", float("nan")),
                "position": _distribution_position(frame, columns, idx_test, pred),
                "thrown away %": decision.get("thrown_away_pct", float("nan")),
                "median pred": float(np.median(pred)),
            }
        )

    if not rows:
        print("nothing fitted")
        return 1

    table = pd.DataFrame(rows).set_index("model")
    print(table.to_string(float_format=lambda v: f"{v:,.3f}"))

    print(
        f"\nFor reference: the median held-out ask is {np.median(y_test):,.1f}c.\n"
        "`position` is the fraction of comparable asks below the prediction --\n"
        "~0.50 means a central estimate, ~0.25 means the low quantile.\n"
        "MAE and SMAPE are minimised by the median, so the quantile model is\n"
        "*expected* to score worse on them; that is not evidence against it.\n"
        "`thrown away %` is the real cost: sellable maps predicted below the\n"
        f"{REROLL_BELOW_CHAOS:g}c reroll line."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
