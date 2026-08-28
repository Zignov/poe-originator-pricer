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

COMPARABLE_COLUMNS = tuple(
    dict.fromkeys((*FEATURE_NAMES, "price_chaos", "price_amount", "price_currency",
                   "seller", "indexed_at", "captured_at"))
)


def scaled_distance(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Euclidean distance after per-feature scaling.

    Without scaling, rarity (0-160) would drown out more_scarabs (0-40) purely
    because its numbers are bigger.

    Public because the live market-comparison mode weighs its comparables on the
    same notion of distance (src/market/live_comparables.py). The two modes
    disagreeing about what "close" means would make their numbers incomparable,
    which is the one thing the side-by-side view exists to do.
    """
    spread = matrix.std(axis=0)
    scale = np.where(spread > 1e-6, spread, 1.0)
    deltas = (matrix - vector) / scale
    return np.sqrt((deltas**2).sum(axis=1))


class ComparablePool:
    """The candidate listings, queried and shaped once, searched many times.

    `find_comparables` used to do the whole job per map: one full query, one
    N x 7 matrix built from sqlite rows, one std() over it -- and a stash tab is
    sixty maps, so pricing a tab ran that sixty times over the same unchanged
    listings. Measured at 6.8k listings it was 210ms a map, 12.8s a tab, which
    was essentially the entire cost of clicking Analyze.

    None of that work depends on the item being priced. Only the distance does.
    So the pool holds the part that does not vary and `nearest()` is the part
    that does: one subtraction and one argsort, sub-millisecond per map.

    The pool is a snapshot. Build a new one after writing to the listings table
    rather than reusing one across a collect.
    """

    __slots__ = ("rows", "matrix", "scale")

    def __init__(self, rows: list[Any], matrix: np.ndarray, scale: np.ndarray):
        self.rows = rows
        self.matrix = matrix
        self.scale = scale

    @classmethod
    def build(
        cls,
        repo: Repository,
        league: str,
        max_age_days: float | None = 7.0,
        map_tier: int | None = 16,
        require_uncorrupted: bool | None = True,
    ) -> "ComparablePool":
        rows = [
            row
            for row in repo.listing_rows(
                league=league,
                include_outliers=False,
                max_age_days=max_age_days,
                map_tier=map_tier,
                require_uncorrupted=require_uncorrupted,
                columns=COMPARABLE_COLUMNS,
            )
            if row["price_chaos"] and row["price_chaos"] > 0
        ]
        if not rows:
            return cls([], np.empty((0, len(FEATURE_NAMES))), np.ones(len(FEATURE_NAMES)))
        matrix = np.array(
            [[row[name] or 0 for name in FEATURE_NAMES] for row in rows], dtype=float
        )
        # Same per-feature scaling `scaled_distance` applies, hoisted out of
        # the search so the two modes still agree on what "close" means.
        spread = matrix.std(axis=0)
        return cls(rows, matrix, np.where(spread > 1e-6, spread, 1.0))

    @property
    def empty(self) -> bool:
        return not self.rows

    def nearest(self, item: NormalizedMap, limit: int = 8) -> pd.DataFrame:
        """Closest listings to `item`, nearest first."""
        if self.empty:
            return pd.DataFrame(
                columns=list(FEATURE_NAMES) + ["price_chaos", "age", "distance"]
            )

        vector = np.array(item.feature_vector(), dtype=float)
        deltas = (self.matrix - vector) / self.scale
        distances = np.sqrt((deltas**2).sum(axis=1))
        order = np.argsort(distances)[:limit]

        now = datetime.now(timezone.utc)
        records: list[dict[str, Any]] = []
        for rank in order:
            row = self.rows[int(rank)]
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


def find_comparables(
    repo: Repository,
    item: NormalizedMap,
    league: str,
    limit: int = 8,
    max_age_days: float | None = 7.0,
    map_tier: int | None = 16,
    require_uncorrupted: bool | None = True,
) -> pd.DataFrame:
    """Closest recent listings to `item`, nearest first.

    A one-shot search that builds its own pool. Correct for a single map and
    wrong for a tab of them -- price a batch through `ComparablePool` so the
    query and the matrix are paid once rather than once per map.
    """
    return ComparablePool.build(
        repo, league, max_age_days, map_tier, require_uncorrupted
    ).nearest(item, limit)


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
