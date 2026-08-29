"""Validation splits and metrics.

Map prices move during a league, so where the data spans several collection
sessions the split is chronological -- a random split would let the model see
the future and flatter itself.

A single snapshot of the current listing book is a different situation, and
`choose_split` handles it: there, ordering by listing time measures how long a
listing has sat unsold, not when it was observed, so the split falls back to a
seller-grouped random one. See `is_snapshot`.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def time_split(
    frame: pd.DataFrame, train: float = 0.70, valid: float = 0.15
) -> tuple[pd.Index, pd.Index, pd.Index]:
    """Oldest `train` -> train, next `valid` -> validation, newest rest -> test."""
    if not 0 < train < 1 or not 0 <= valid < 1 or train + valid >= 1:
        raise ValueError("train and valid must be fractions summing to less than 1")
    ordered = frame.sort_values("observed_at", kind="stable")
    n = len(ordered)
    n_train = int(n * train)
    n_valid = int(n * valid)
    return (
        ordered.index[:n_train],
        ordered.index[n_train : n_train + n_valid],
        ordered.index[n_train + n_valid :],
    )


#: If every row was captured within this window, the dataset is one snapshot of
#: the current listing book rather than a series of observations over time.
SNAPSHOT_CAPTURE_WINDOW_HOURS = 6.0

#: Fewer distinct groups than this and a grouped split cannot produce three
#: usable slices, so it falls back to a row-level split.
MIN_GROUPS_FOR_GROUPED_SPLIT = 10


def is_snapshot(frame: pd.DataFrame, capture_column: str = "captured_at") -> bool:
    """Was this dataset collected in a single sitting?

    It matters because of what `indexed_at` means. In a series of collections
    spread over days, ordering by listing time is a genuine chronological split.
    In a single snapshot it is a *staleness* split: the oldest rows are the
    listings that have sat unsold longest, which are systematically overpriced
    leftovers, while the newest are fresh asks. Training on one and testing on
    the other compares two different populations and reports a misleadingly poor
    score -- observed here as a train median of 60 chaos against a test median of
    15.5, from a dataset captured over seven minutes.
    """
    if frame.empty or capture_column not in frame.columns:
        return False
    captured = pd.to_datetime(frame[capture_column], utc=True, errors="coerce").dropna()
    if len(captured) < 2:
        return True
    span_hours = (captured.max() - captured.min()).total_seconds() / 3600.0
    return span_hours <= SNAPSHOT_CAPTURE_WINDOW_HOURS


def grouped_split(
    frame: pd.DataFrame,
    group_column: str = "seller",
    train: float = 0.70,
    valid: float = 0.15,
    seed: int = 7,
) -> tuple[pd.Index, pd.Index, pd.Index]:
    """Random split with whole sellers kept on one side.

    Used for snapshot datasets, where a chronological split is meaningless. The
    grouping is the important part: a seller who lists several maps at one flat
    price would otherwise appear on both sides, and the model could score well by
    recognising that seller's price rather than the map's rewards.
    """
    if frame.empty:
        empty = frame.index[:0]
        return empty, empty, empty

    if group_column in frame.columns:
        keys = frame[group_column]
        # An unrecorded seller means the identity is unknown, not that the rows
        # share one. Collapsing them into a single "<unknown>" group is the
        # difference between a working split and no split at all on an
        # anonymised import: the bundled sample has its seller column blanked,
        # so all ~4,100 of its rows became one group, went to whichever side the
        # shuffle sent it, and left validation with a handful of rows and an
        # R2 of nan. Blank and missing both get a key of their own here, which
        # is the honest reading and the one that keeps the slices usable.
        # `.fillna` alone does not do it -- an anonymised export writes an empty
        # string, which is not null.
        anonymous = keys.isna() | (keys.astype("string").fillna("").str.strip() == "")
        keys = keys.astype("string").mask(
            anonymous, pd.Series(frame.index.astype(str), index=frame.index)
        )
    else:
        keys = pd.Series(frame.index.astype(str), index=frame.index)

    # Too few distinct groups and the split degenerates -- with one group,
    # everything lands on one side and training gets nothing. Fall back to a
    # row-level split, which loses the leakage guard but at least produces three
    # usable slices.
    if keys.nunique() < MIN_GROUPS_FOR_GROUPED_SPLIT:
        keys = pd.Series(frame.index.astype(str), index=frame.index)

    unique = pd.Index(pd.unique(keys))
    rng = np.random.default_rng(seed)
    shuffled = unique[rng.permutation(len(unique))]

    n_train = int(len(shuffled) * train)
    n_valid = int(len(shuffled) * valid)
    train_keys = set(shuffled[:n_train])
    valid_keys = set(shuffled[n_train : n_train + n_valid])

    in_train = keys.isin(train_keys)
    in_valid = keys.isin(valid_keys)
    return (
        frame.index[in_train],
        frame.index[in_valid],
        frame.index[~(in_train | in_valid)],
    )


def choose_split(
    frame: pd.DataFrame, train: float = 0.70, valid: float = 0.15
) -> tuple[pd.Index, pd.Index, pd.Index, str]:
    """Pick the appropriate split and say which one was used.

    Returns (train, valid, test, description). The description is written into
    model metadata and shown on the Model Analysis page, so the reader is never
    left guessing how the numbers were produced.
    """
    if is_snapshot(frame):
        idx = grouped_split(frame, train=train, valid=valid)
        return (*idx, "seller-grouped random (dataset is a single snapshot)")
    idx = time_split(frame, train=train, valid=valid)
    return (*idx, "chronological (oldest train, newest test)")


def rolling_time_folds(frame: pd.DataFrame, folds: int = 4) -> list[tuple[pd.Index, pd.Index]]:
    """Expanding-window folds: always train on the past, score on the future."""
    ordered = frame.sort_values("observed_at", kind="stable")
    n = len(ordered)
    if n < folds * 4:
        return []
    step = n // (folds + 1)
    out = []
    for k in range(1, folds + 1):
        out.append((ordered.index[: step * k], ordered.index[step * k : step * (k + 1)]))
    return out


# ---------------------------------------------------------------------------
def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Symmetric MAPE, in percent. Well-behaved when actuals span orders of magnitude."""
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    mask = denom > 0
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs(y_pred[mask] - y_true[mask]) / denom[mask]) * 100.0)


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float | None:
    """Plain MAPE, reported only when no actual is near zero -- otherwise it explodes."""
    if np.any(np.abs(y_true) < 1e-6):
        return None
    return float(np.mean(np.abs((y_pred - y_true) / y_true)) * 100.0)


def r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    if ss_tot == 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    errors = np.abs(y_pred - y_true)
    out: dict[str, Any] = {
        "n": int(len(y_true)),
        "mae": float(np.mean(errors)) if len(errors) else float("nan"),
        "median_ae": float(np.median(errors)) if len(errors) else float("nan"),
        "rmse": float(np.sqrt(np.mean((y_pred - y_true) ** 2))) if len(errors) else float("nan"),
        "r2": r2(y_true, y_pred) if len(y_true) > 1 else float("nan"),
        "smape": smape(y_true, y_pred),
    }
    value = mape(y_true, y_pred)
    if value is not None:
        out["mape"] = value
    # Share of predictions within a factor of 1.25 / 1.5 of the actual: the
    # practical question is "is this the right ballpark", not squared error.
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(y_true > 0, y_pred / y_true, np.nan)
    finite = ratio[np.isfinite(ratio) & (ratio > 0)]
    if len(finite):
        out["within_25pct"] = float(np.mean((finite >= 0.8) & (finite <= 1.25)) * 100.0)
        out["within_50pct"] = float(np.mean((finite >= 0.667) & (finite <= 1.5)) * 100.0)
    return out
