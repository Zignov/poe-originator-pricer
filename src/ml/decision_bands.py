"""What the price is *for*, expressed as numbers the trainer can optimise.

A price prediction on an Originator Map feeds exactly one of two decisions, and
they need completely different things from the model:

  below ~30c   The map gets rerolled. The number is irrelevant -- nobody cares
               whether a map they are about to reroll was worth 11c or 19c. All
               that matters is not mistaking a sellable map for one of these.
  50c and up   The map gets sold, and the number is money. Being 30% low here
               is the difference between 2 divine and 3.

Trained on plain MAE over everything, the model optimises the wrong one. The
reroll pile is the dense end of the distribution -- 1,319 of 4,102 rows sit
under 25c -- so it dominates the average, and every model-selection decision
gets made by maps whose prices do not matter. Measured on a chronological split
averaged over three split points and two seeds, that cost:

    all rows, uniform weight     sellable-map MAPE 36.9%, 9.4% of sellable
                                 maps predicted below the reroll line
    floor + value weighting      sellable-map MAPE 29.5%, 0.8%

29.5% is not a coincidence: identical maps are listed at prices scattering ~29%
around their own median at every price level, so that is the floor for
asking-price data. The second model is at it; the first was seven points off.

Weighting by the target looks like leakage and is not. It never enters
inference -- it says that an error on a 400c map costs more than the same
proportional error on a 40c one, which is simply true, and is the textbook
cost-sensitive setup.
"""
from __future__ import annotations

import numpy as np

#: Below this a map gets rerolled, so its predicted price carries no decision.
REROLL_BELOW_CHAOS = 30.0

#: At and above this the prediction is what someone lists the map for.
WORTH_SELLING_CHAOS = 50.0

#: Rows under the reroll line are dropped from training rather than downweighted.
#: Downweighting them to 0.15 was measured too and came out worse on every
#: sellable-band number, because they still anchor the low end of every split.
TRAINING_FLOOR_CHAOS = REROLL_BELOW_CHAOS

#: Never drop so much that the model is fitted on a sliver. If a dataset is
#: mostly cheap listings the floor is skipped and the run says so -- a worse
#: model over the right band beats no model at all.
MIN_FLOOR_KEEP_FRACTION = 0.35

#: Weight cap, as a multiple of WORTH_SELLING_CHAOS. Uncapped, the handful of
#: 800c+ listings would own the fit.
MAX_VALUE_RATIO = 16.0


def value_weights(prices: np.ndarray) -> np.ndarray:
    """Per-row weight rising with the money at stake, on a square-root curve.

    Linear-in-price weighting was measured as well: it removes the low-end bias
    just as effectively but hands the fit to the top band, and the share of junk
    maps correctly identified as junk fell from 88% to 71%. The square root
    keeps both.
    """
    ratio = np.clip(np.asarray(prices, dtype=float) / WORTH_SELLING_CHAOS, 0.1, MAX_VALUE_RATIO)
    return np.sqrt(ratio)


def apply_training_floor(prices: np.ndarray) -> tuple[np.ndarray, str]:
    """(keep-mask, explanation) for the reroll-pile filter."""
    prices = np.asarray(prices, dtype=float)
    keep = prices >= TRAINING_FLOOR_CHAOS
    if not len(prices):
        return keep, ""
    fraction = keep.mean()
    if fraction < MIN_FLOOR_KEEP_FRACTION:
        return np.ones(len(prices), dtype=bool), (
            f"training floor skipped: only {fraction:.0%} of listings reach "
            f"{TRAINING_FLOOR_CHAOS:g}c, too few to train on"
        )
    dropped = int((~keep).sum())
    if not dropped:
        return keep, ""
    return keep, (
        f"training floor: {dropped} listings under {TRAINING_FLOOR_CHAOS:g}c held out "
        f"(reroll pile -- their price carries no decision)"
    )


def decision_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Accuracy where it is spent, plus the two mistakes that cost something.

    `sell_mape` is the headline: median absolute percentage error over maps that
    are actually worth selling. `thrown_away_pct` is the expensive mistake -- a
    map worth 50c+ that the app puts under the reroll line, so you destroy it.
    `junk_found_pct` is the counterweight that stops the fix being "predict a
    big number for everything".
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    sell = y_true >= WORTH_SELLING_CHAOS
    junk = y_true < REROLL_BELOW_CHAOS
    out: dict[str, float] = {"n_sellable": int(sell.sum())}
    if sell.any():
        error = np.abs(y_pred[sell] - y_true[sell])
        out["sell_mae"] = float(error.mean())
        out["sell_mape"] = float(np.median(error / y_true[sell]) * 100.0)
        out["sell_bias"] = float(np.median(y_pred[sell] - y_true[sell]))
        out["thrown_away_pct"] = float((y_pred[sell] < REROLL_BELOW_CHAOS).mean() * 100.0)
    if junk.any():
        out["junk_found_pct"] = float(
            (y_pred[junk] < WORTH_SELLING_CHAOS).mean() * 100.0
        )
    return out


#: What the app shows in place of a price it cannot justify.
REROLL = "Reroll"
MARGINAL = "Marginal"
SELL = "Sell"


def verdict(chaos: float | None) -> str:
    """Reroll / Marginal / Sell for a predicted price.

    This exists because the training floor has a visible consequence: the model
    is no longer fitted below 30c, so it returns roughly the same number for
    every map down there -- a 0-reward map and a 40-quantity map both come back
    around 24c. That number is not a cheap price, it is the absence of one.
    Printing it as "24.6c" would be the app's most confident-looking lie, so the
    band gets a word instead.
    """
    if chaos is None:
        return ""
    if chaos < REROLL_BELOW_CHAOS:
        return REROLL
    if chaos < WORTH_SELLING_CHAOS:
        return MARGINAL
    return SELL
