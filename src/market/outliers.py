"""Flagging of bad and troll listings.

Asking price is noisy. The market is full of people who list a map at a round
number -- 3 divine is a favourite -- and hope, and of maps parked at absurd
prices so they never sell. Nothing is deleted: rows are flagged with
is_outlier / outlier_reason so they stay auditable and can be un-flagged.

The peer test compares a listing to *maps like it* rather than to the market as
a whole, using nearest neighbours in scaled feature space. An earlier version
grouped peers by quantised reward values, which sounded similar but was far too
fine-grained: most maps have a near-unique reward combination, so they never
reached the minimum peer count and the check silently never fired. That let a
zero-reward map sit at 690 chaos and a 158-currency map at 69,000.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from src.database.repository import _parse_iso
from src.schema import FEATURE_NAMES

#: How many similar maps form the peer group.
NEIGHBOURS = 25

#: A listing is flagged when it is this many times above the 90th percentile of
#: its peers' prices, or the same factor below their 10th percentile.
#:
#: Comparing against a percentile rather than the median is deliberate. A
#: genuinely top-end map sits at the edge of the feature space, so its nearest
#: neighbours are all weaker maps and their *median* badly under-states what it
#: is worth -- against the median, the best map in the dataset gets flagged as a
#: troll listing. Against the peer p90 it does not.
PEER_FACTOR = 3.0

#: Below this many usable listings the peer test is switched off rather than run
#: on a sample too small to mean anything.
MIN_ROWS_FOR_PEERS = 40

#: Absolute sanity bounds in chaos, as a backstop for the peer test, and the
#: default for market.plausible_price_chaos.
#:
#: These used to be deliberately wide (1c to 20000c) on the reasoning that the
#: peer test should do the real work. In practice the peer test cannot: it needs
#: MIN_ROWS_FOR_PEERS comparable listings, and the extreme asks are extreme
#: precisely because nothing is comparable to them. The uncorrupted T16 market
#: runs from roughly 30c to 3 divine, so a listing above 1000c is almost always
#: parked inventory rather than a price -- 0.7% of a 820-listing sample, but
#: enough to own the top of every histogram and stretch the model's upper range.
DEFAULT_PLAUSIBLE_CHAOS = (5.0, 1000.0)


def _peer_bounds(matrix: np.ndarray, prices: np.ndarray, k: int = NEIGHBOURS):
    """(low, high) percentile price of each row's k nearest neighbours.

    Distances are computed on per-feature-scaled values so that rarity (0-300)
    does not drown out more_scarabs (0-150) purely because its numbers are
    bigger.
    """
    spread = matrix.std(axis=0)
    scale = np.where(spread > 1e-6, spread, 1.0)
    scaled = matrix / scale

    n = len(scaled)
    k = min(k, n - 1)
    low = np.empty(n)
    high = np.empty(n)

    # Chunked so a large dataset does not allocate an n x n distance matrix.
    chunk = max(1, min(n, 2000))
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        deltas = scaled[start:stop, None, :] - scaled[None, :, :]
        distances = np.sqrt((deltas**2).sum(axis=2))
        # Exclude self by pushing its own distance to the back.
        for offset in range(stop - start):
            distances[offset, start + offset] = np.inf
        nearest = np.argpartition(distances, k, axis=1)[:, :k]
        neighbour_prices = prices[nearest]
        low[start:stop] = np.quantile(neighbour_prices, 0.10, axis=1)
        high[start:stop] = np.quantile(neighbour_prices, 0.90, axis=1)
    return low, high


def evaluate(
    rows: Sequence[Any],
    max_age_days: float | None = None,
    plausible_chaos: tuple[float, float] = DEFAULT_PLAUSIBLE_CHAOS,
) -> list[tuple[int, bool, str]]:
    """Return (row_id, is_outlier, reason) decisions for every row given."""
    now = datetime.now(timezone.utc)

    # -- cheap, per-row checks first ------------------------------------
    decisions: dict[int, str] = {}
    usable: list[Any] = []
    for row in rows:
        reason = _basic_reason(row, now, max_age_days, plausible_chaos)
        decisions[row["id"]] = reason
        if not reason:
            usable.append(row)

    # -- peer test on whatever survived ---------------------------------
    if len(usable) >= MIN_ROWS_FOR_PEERS:
        matrix = np.array(
            [[row[name] or 0 for name in FEATURE_NAMES] for row in usable], dtype=float
        )
        prices = np.array([row["price_chaos"] for row in usable], dtype=float)
        low, high = _peer_bounds(matrix, prices)
        for index, row in enumerate(usable):
            price = prices[index]
            if high[index] > 0 and price > PEER_FACTOR * high[index]:
                decisions[row["id"]] = (
                    f"{price / high[index]:.0f}x the price of comparable maps "
                    f"(peers around {high[index]:.0f}c)"
                )
            elif low[index] > 0 and price < low[index] / PEER_FACTOR:
                decisions[row["id"]] = (
                    f"{low[index] / price:.0f}x below comparable maps "
                    f"(peers around {low[index]:.0f}c)"
                )

    return [(row_id, bool(reason), reason) for row_id, reason in decisions.items()]


def _column(row, name: str) -> str:
    """Read an optional column from either a sqlite3.Row or a plain dict.

    sqlite3.Row raises IndexError for a name it does not carry, and dicts built
    by callers do not always carry every column.
    """
    try:
        return row[name] or ""
    except (IndexError, KeyError):
        return ""


def _basic_reason(row, now, max_age_days, plausible_chaos) -> str:
    price = row["price_chaos"]

    if price is None:
        return "no usable price (unpriced or unknown currency)"
    if price <= 0:
        return "zero or negative price"
    if not math.isfinite(price):
        return "non-finite price"

    # A map with no reward values at all cannot be priced by this model, and in
    # practice these are the listings used to park inventory at absurd prices.
    if all((row[name] or 0) == 0 for name in FEATURE_NAMES):
        return "no reward values on the item"

    # The search now asks GGG for rare maps only, but rows collected before that
    # are still in the database. Flagging rather than deleting keeps them
    # auditable and reversible, like every other outlier decision here.
    rarity_frame = _column(row, "item_rarity_frame")
    if rarity_frame and rarity_frame.strip().lower() != "rare":
        return f"{rarity_frame} rarity (the model is scoped to rare maps)"

    if (row["price_currency"] or "").lower() == "mirror":
        return "priced in mirrors (price-fixing listing)"

    low, high = plausible_chaos
    if price < low:
        return f"below the plausible range ({low:g}c)"
    if price > high:
        return f"above the plausible range ({high:g}c)"

    if max_age_days is not None:
        indexed = _parse_iso(row["indexed_at"] or row["captured_at"])
        if indexed and (now - indexed).total_seconds() > max_age_days * 86400:
            return f"older than {max_age_days} days"

    return ""
