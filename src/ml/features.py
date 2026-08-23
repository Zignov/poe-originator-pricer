"""Feature construction.

The seven reward values stay seven independent numeric columns -- they are never
collapsed into a hand-weighted score. Optional context columns are added behind
flags so their value can be measured rather than assumed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.database.repository import _parse_iso
from src.schema import FEATURE_NAMES, NormalizedMap

# Context columns. Off by default; enable and re-validate before relying on them.
OPTIONAL_FEATURES: tuple[str, ...] = (
    "item_level",
    "quality",
    "hour_of_day",
    "day_of_week",
    "days_since_first_observation",
    "divine_to_chaos",
)


#: Features where more is never worth less. All seven rewards qualify: a map with
#: higher quantity, rarity, pack size or any "More" value is strictly better than
#: the same map without it, so no price model should ever say otherwise.
#:
#: This is not a modelling convenience, it is a property of the game -- and an
#: unconstrained gradient-boosted model broke it on 11% of maps, which silently
#: inverts the exalt calculation, that being nothing but a comparison between a
#: map and a strictly better version of itself. Context columns are deliberately
#: absent: nothing says a later hour of day should mean a higher price.
MONOTONE_FEATURES: frozenset[str] = frozenset(FEATURE_NAMES)


def monotone_constraints(columns: Sequence[str]) -> list[int]:
    """Per-column monotonicity: 1 for non-decreasing, 0 for unconstrained."""
    return [1 if name in MONOTONE_FEATURES else 0 for name in columns]


def feature_columns(optional: Sequence[str] = ()) -> list[str]:
    unknown = [c for c in optional if c not in OPTIONAL_FEATURES]
    if unknown:
        raise ValueError(f"unknown optional features: {unknown}")
    return list(FEATURE_NAMES) + list(optional)


def rows_to_frame(rows: Sequence[Any], divine_to_chaos: float | None = None) -> pd.DataFrame:
    """Turn originator_listings rows into a DataFrame."""
    records: list[dict[str, Any]] = []
    for row in rows:
        indexed = _parse_iso(row["indexed_at"]) or _parse_iso(row["captured_at"])
        record: dict[str, Any] = {name: int(row[name] or 0) for name in FEATURE_NAMES}
        record.update(
            {
                "id": row["id"],
                "price_chaos": row["price_chaos"],
                "price_currency": row["price_currency"],
                "price_type": row["price_type"],
                "observed_at": indexed,
                "captured_at": _parse_iso(row["captured_at"]),
                "item_level": row["item_level"] or 0,
                "quality": row["quality"] or 0,
                "corrupted": int(row["corrupted"] or 0),
                "map_tier": row["map_tier"],
                "seller": row["seller"],
                "divine_to_chaos": divine_to_chaos or np.nan,
            }
        )
        records.append(record)

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame

    frame = frame.sort_values("observed_at", kind="stable").reset_index(drop=True)
    times = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    frame["hour_of_day"] = times.dt.hour.fillna(0).astype(int)
    frame["day_of_week"] = times.dt.dayofweek.fillna(0).astype(int)
    first = times.min()
    frame["days_since_first_observation"] = (
        (times - first).dt.total_seconds() / 86400.0
    ).fillna(0.0)
    return frame


def map_to_row(nm: NormalizedMap, optional: Sequence[str] = (), divine_to_chaos: float | None = None) -> pd.DataFrame:
    """One-row frame for prediction, matching the training column order."""
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = nm.features()
    values.update(
        {
            "item_level": nm.item_level or 0,
            "quality": nm.quality or 0,
            "hour_of_day": now.hour,
            "day_of_week": now.weekday(),
            "days_since_first_observation": 0.0,
            "divine_to_chaos": divine_to_chaos if divine_to_chaos else np.nan,
        }
    )
    columns = feature_columns(optional)
    return pd.DataFrame([[values[c] for c in columns]], columns=columns)


def recency_weights(
    observed_at: pd.Series, schedule: Sequence[Sequence[float]], now: datetime | None = None
) -> np.ndarray:
    """Weight listings by age using a (max_age_hours, weight) schedule.

    The schedule is a tunable starting point, not a claim about the market --
    `train.py --tune-weights` picks between candidate schedules on validation.
    """
    now = now or datetime.now(timezone.utc)
    times = pd.to_datetime(observed_at, utc=True, errors="coerce")
    age_hours = (pd.Timestamp(now) - times).dt.total_seconds() / 3600.0
    age_hours = age_hours.fillna(age_hours.max() if len(age_hours) else 0.0)

    weights = np.full(len(age_hours), float(schedule[-1][1]) if schedule else 1.0)
    ordered = sorted(schedule, key=lambda pair: pair[0])
    assigned = np.zeros(len(age_hours), dtype=bool)
    for max_hours, weight in ordered:
        mask = (~assigned) & (age_hours.to_numpy() <= float(max_hours))
        weights[mask] = float(weight)
        assigned |= mask
    return weights


def target_transform(y: np.ndarray, mode: str) -> np.ndarray:
    if mode == "log1p":
        return np.log1p(y)
    if mode == "raw":
        return y
    raise ValueError(f"unknown target mode {mode!r}")


def target_inverse(y: np.ndarray, mode: str) -> np.ndarray:
    if mode == "log1p":
        return np.expm1(y)
    if mode == "raw":
        return y
    raise ValueError(f"unknown target mode {mode!r}")


def bulk_listing_weights(frame: pd.DataFrame, exponent: float = 0.5) -> np.ndarray:
    """Down-weight rows that belong to a one-seller, one-price bulk dump.

    Sellers routinely list a whole tab at a single flat price. Inside such a
    block the price never moves while the rewards vary wildly, so every row in it
    is evidence *against* the rewards mattering -- and a 142-row block outvotes a
    hundred independent sellers. Collecting with `collapse` keeps most of these
    out, but manual imports have no such protection, so the guard also lives
    here.

    Weight is `1 / block_size ** exponent`: at the default 0.5 a block of 100
    counts for 10 independent observations rather than 100, which discounts it
    heavily without discarding a genuine signal that several similar maps really
    do sell around one price.
    """
    if frame.empty:
        return np.ones(0)
    if "seller" not in frame.columns or "price_chaos" not in frame.columns:
        return np.ones(len(frame))

    block = frame.assign(
        _seller=frame["seller"].fillna("<unknown>"),
        _price=frame["price_chaos"].round(2),
    )
    sizes = block.groupby(["_seller", "_price"])["_price"].transform("size")
    return (1.0 / np.power(sizes.to_numpy(dtype=float), exponent)).astype(float)
