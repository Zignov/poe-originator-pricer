"""Confidence scoring.

Deliberately not a probability. It answers a narrower question: how much does the
market actually tell us about a map like this one? Four signals are combined:

  * how many close training examples exist
  * how far the nearest ones are in scaled feature space
  * how wide the calibrated prediction interval is, relative to the estimate
  * how fresh the supporting observations are

A low score is surfaced as "Low" and the UI is expected to present it as a rough
estimate rather than a price.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from src.ml.predict import Predictor
    from src.schema import NormalizedMap

NEAR_DISTANCE = 1.0     # scaled feature-space radius counted as "close"
GOOD_NEIGHBOURS = 12    # enough close comparables to be confident
FRESH_HOURS = 24.0


def score_confidence(
    predictor: "Predictor",
    item: "NormalizedMap",
    fair: float,
    low: float,
    high: float,
    vector: np.ndarray | None = None,
) -> tuple[str, float, list[str]]:
    """`vector` is the item's already-built feature row, when the caller has one."""
    reasons: list[str] = []
    parts: list[float] = []

    indices, distances = predictor.neighbour_distances(item, k=40, vector=vector)
    close = int((distances <= NEAR_DISTANCE).sum()) if distances.size else 0
    support = min(close / GOOD_NEIGHBOURS, 1.0)
    parts.append(support)
    if close == 0:
        reasons.append("no closely comparable listings in the dataset")
    elif close < GOOD_NEIGHBOURS:
        reasons.append(f"only {close} closely comparable listings")
    else:
        reasons.append(f"{close} closely comparable listings")

    if distances.size:
        nearest = float(distances[0])
        proximity = float(np.clip(1.0 - nearest / (2 * NEAR_DISTANCE), 0.0, 1.0))
        parts.append(proximity)
        if nearest > 2 * NEAR_DISTANCE:
            reasons.append("unusual reward combination -- nearest example is far away")
    else:
        parts.append(0.0)

    if fair > 0:
        width = (high - low) / fair
        tightness = float(np.clip(1.5 - width, 0.0, 1.0))
        parts.append(tightness)
        if width > 1.0:
            reasons.append(f"wide calibrated interval (+/-{width * 50:.0f}%)")
    else:
        parts.append(0.0)
        reasons.append("model predicts an effectively zero price")

    if indices.size and predictor.training_times.size:
        ages_h = (time.time() - predictor.training_times[indices]) / 3600.0
        median_age = float(np.median(ages_h))
        freshness = float(np.clip(1.0 - median_age / (FRESH_HOURS * 7), 0.0, 1.0))
        parts.append(freshness)
        if median_age > FRESH_HOURS * 3:
            reasons.append(f"supporting listings are ~{median_age / 24:.0f} days old")
    else:
        parts.append(0.5)

    score = float(np.mean(parts))
    if score >= 0.7:
        level = "High"
    elif score >= 0.45:
        level = "Medium"
    else:
        level = "Low"
    return level, score, reasons
