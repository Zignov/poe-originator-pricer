"""Nearest recent listings for a given map.

The model output is never the only evidence shown. For every priced map the UI
also shows the closest real listings, which is the sanity check that catches a
model that has drifted or a reward combination it has never seen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.database.repository import Repository, _parse_iso
from src.schema import FEATURE_NAMES, NormalizedMap


def _scaled_distance(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Euclidean distance after per-feature scaling.

    Without scaling, rarity (0-160) would drown out more_scarabs (0-40) purely
    because its numbers are bigger.
    """
    spread = matrix.std(axis=0)
    scale = np.where(spread > 1e-6, spread, 1.0)
    deltas = (matrix - vector) / scale
    return np.sqrt((deltas**2).sum(axis=1))


def find_comparables(
    repo: Repository,
    item: NormalizedMap,
    league: str,
    limit: int = 8,
    max_age_days: float | None = 7.0,
    map_tier: int | None = 16,
    require_uncorrupted: bool | None = True,
) -> pd.DataFrame:
    """Closest recent listings to `item`, nearest first."""
    rows = repo.listing_rows(
        league=league,
        include_outliers=False,
        max_age_days=max_age_days,
        map_tier=map_tier,
        require_uncorrupted=require_uncorrupted,
    )
    rows = [r for r in rows if r["price_chaos"] and r["price_chaos"] > 0]
    if not rows:
        return pd.DataFrame(columns=list(FEATURE_NAMES) + ["price_chaos", "age", "distance"])

    matrix = np.array([[row[name] or 0 for name in FEATURE_NAMES] for row in rows], dtype=float)
    vector = np.array(item.feature_vector(), dtype=float)
    distances = _scaled_distance(matrix, vector)
    order = np.argsort(distances)[:limit]

    now = datetime.now(timezone.utc)
    records: list[dict[str, Any]] = []
    for rank in order:
        row = rows[int(rank)]
        observed = _parse_iso(row["indexed_at"]) or _parse_iso(row["captured_at"])
        records.append(
            {
                **{name: int(row[name] or 0) for name in FEATURE_NAMES},
                "price_chaos": float(row["price_chaos"]),
                "price_amount": row["price_amount"],
                "price_currency": row["price_currency"],
                "seller": row["seller"],
                "age_hours": (now - observed).total_seconds() / 3600.0 if observed else None,
                "distance": float(distances[int(rank)]),
            }
        )
    return pd.DataFrame.from_records(records)


def market_reference_price(comparables: pd.DataFrame) -> float | None:
    """Median price of the comparables, as an independent cross-check."""
    if comparables.empty:
        return None
    return float(comparables["price_chaos"].median())


def format_age(hours: float | None) -> str:
    if hours is None:
        return "-"
    if hours < 1:
        return f"{hours * 60:.0f}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def percentile_bands(values: Sequence[float]) -> dict[str, float]:
    """Percentile-based value thresholds, so nothing league-specific is hardcoded."""
    arr = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"low": 0.0, "medium": 0.0, "high": 0.0}
    return {
        "low": float(np.quantile(arr, 0.35)),
        "medium": float(np.quantile(arr, 0.70)),
        "high": float(np.quantile(arr, 0.90)),
    }


def value_category(price: float | None, bands: dict[str, float]) -> str:
    if price is None:
        return "unknown"
    if price >= bands["high"]:
        return "very high"
    if price >= bands["medium"]:
        return "high"
    if price >= bands["low"]:
        return "medium"
    return "low"
