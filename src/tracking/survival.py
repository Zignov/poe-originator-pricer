"""How fast your maps clear, fitted from data that mostly doesn't know when.

Every consumer asks the same question: *if I list at this ratio to the model's
estimate, what are the odds it sells within W hours?* Answering it from this data
is awkward because most sales are only bracketed -- polling stops when the app
closes, so an overnight sale is known to have happened somewhere inside a nine
hour window and no more precisely.

There are three ways to handle that and two of them are wrong. Discarding
bracketed sales throws away most of the data and biases what is left toward
whatever happened to sell while the app was open. Midpointing them invents a
precision nobody has, and the fit believes it. The third way is to let a
bracketed sale contribute to every bucket it could have happened in, in
proportion to how likely each bucket is -- which is circular, so it is solved by
iteration: guess the hazards, use them to split the bracketed sales across
buckets, re-estimate, repeat. That is the Turnbull/EM idea, and on a coarse grid
it is short.

The grid is deliberately coarse. Eight time buckets and a handful of ratio
bands, because a personal history is a few dozen maps rather than a few thousand,
and a fine grid would fit noise beautifully.

Refusals matter as much as estimates. A band with no data returns None rather
than borrowing from its neighbours -- you cannot learn what a 1.5x listing does
from having only ever listed at 0.9x, and a plausible-looking number would hide
that you never tried.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

#: Upper edges, in hours. The final bucket runs from the last edge to 4x it,
#: which is wide on purpose: past four days the question is no longer "how long"
#: but "at all".
TIME_EDGES_HOURS: tuple[float, ...] = (1.0, 3.0, 6.0, 12.0, 24.0, 48.0, 96.0)

#: Ratio band edges (ask / model estimate). Lower-inclusive, upper-exclusive.
DEFAULT_RATIO_EDGES: tuple[float, ...] = (0.0, 0.85, 1.0, 1.15, 1.3, 99.0)


def _uppers(time_edges: Sequence[float]) -> list[float]:
    return list(time_edges) + [float(time_edges[-1]) * 4.0]


def _bucket_index(hours: float, uppers: Sequence[float]) -> int:
    for index, upper in enumerate(uppers):
        if hours < upper:
            return index
    return len(uppers) - 1


@dataclass(frozen=True)
class Fit:
    ratio_edges: tuple[float, ...]
    time_edges: tuple[float, ...]
    hazard: np.ndarray  # (bands, buckets)
    at_risk: np.ndarray
    sales: np.ndarray
    ok: bool = False
    reason: str = ""

    def band_for(self, ratio: float) -> int:
        return int(
            np.clip(
                np.searchsorted(self.ratio_edges, ratio, side="right") - 1,
                0,
                len(self.ratio_edges) - 2,
            )
        )

    def probability_within(self, ratio: float, hours: float) -> float | None:
        """P(sells within `hours`) at this ratio, or None where we cannot say."""
        if not self.ok:
            return None
        band = self.band_for(ratio)
        if self.at_risk[band].sum() <= 0:
            return None

        uppers = _uppers(self.time_edges)
        survival = 1.0
        lower = 0.0
        for bucket, upper in enumerate(uppers):
            if hours <= lower:
                break
            covered = min(hours, upper) - lower
            width = upper - lower
            lower = upper
            if width <= 0 or covered <= 0:
                continue
            # Partial credit for a bucket the window only reaches into, so the
            # curve rises smoothly with time instead of in steps.
            survival *= (1.0 - self.hazard[band, bucket]) ** (covered / width)
        return float(1.0 - survival)


def fit_hazard(
    observations,
    ratio_edges: tuple[float, ...] = DEFAULT_RATIO_EDGES,
    time_edges: tuple[float, ...] = TIME_EDGES_HOURS,
    min_sales: int = 8,
    max_iter: int = 200,
    tol: float = 1e-9,
) -> Fit:
    """Discrete-time hazard per (ratio band, time bucket), by Turnbull-style EM."""
    usable = [o for o in observations if o.ratio is not None]
    n_bands = len(ratio_edges) - 1
    uppers = _uppers(time_edges)
    n_buckets = len(uppers)
    empty = np.zeros((n_bands, n_buckets))

    sold = [o for o in usable if o.sold]
    if not usable or len(sold) < min_sales:
        return Fit(
            ratio_edges=ratio_edges, time_edges=time_edges, hazard=empty,
            at_risk=empty.copy(), sales=empty.copy(), ok=False,
            reason=(
                f"not enough data: {len(sold)} sold listings with a model estimate, "
                f"{min_sales} needed"
            ),
        )

    def band_of(ratio: float) -> int:
        return int(
            np.clip(np.searchsorted(ratio_edges, ratio, side="right") - 1, 0, n_bands - 1)
        )

    # Right-censored observations never sell, and are at risk in every bucket
    # they completed. The bucket they were cut off inside is dropped rather than
    # counted as a full period at risk.
    censored_at_risk = np.zeros((n_bands, n_buckets))
    for obs in usable:
        if obs.sold:
            continue
        censored_at_risk[band_of(obs.ratio), : _bucket_index(obs.lo_hours, uppers)] += 1.0

    # Each sale, as the span of buckets it might have happened in.
    spans: list[tuple[int, int, int]] = []
    for obs in sold:
        low = _bucket_index(obs.lo_hours, uppers)
        high = _bucket_index(
            obs.hi_hours if obs.hi_hours is not None else obs.lo_hours, uppers
        )
        spans.append((band_of(obs.ratio), low, max(low, high)))

    hazard = np.full((n_bands, n_buckets), 0.1)
    sales = np.zeros((n_bands, n_buckets))
    at_risk = censored_at_risk.copy()

    for _ in range(max_iter):
        sales = np.zeros((n_bands, n_buckets))
        at_risk = censored_at_risk.copy()

        for band, low, high in spans:
            span = range(low, high + 1)

            # E-step: how likely is each candidate bucket, given the current
            # hazards? P(survive to k) * P(sell in k), renormalised over the span.
            weights: dict[int, float] = {}
            survival = 1.0
            for bucket in range(0, high + 1):
                if bucket >= low:
                    weights[bucket] = survival * hazard[band, bucket]
                survival *= 1.0 - hazard[band, bucket]
            total = sum(weights.values())
            if total <= 0:
                weights = {bucket: 1.0 / len(span) for bucket in span}
                total = 1.0

            # M-step bookkeeping. Before the span it was certainly unsold, so it
            # is fully at risk. Inside the span it is at risk only to the extent
            # it has not already sold in an earlier bucket of the same span.
            remaining = 1.0
            for bucket in range(0, low):
                at_risk[band, bucket] += 1.0
            for bucket in span:
                share = weights[bucket] / total
                at_risk[band, bucket] += remaining
                sales[band, bucket] += share
                remaining -= share

        with np.errstate(divide="ignore", invalid="ignore"):
            updated = np.where(at_risk > 0, sales / np.maximum(at_risk, 1e-12), 0.0)
        updated = np.clip(updated, 0.0, 1.0)
        converged = np.max(np.abs(updated - hazard)) < tol
        hazard = updated
        if converged:
            break

    return Fit(
        ratio_edges=ratio_edges, time_edges=time_edges, hazard=hazard,
        at_risk=at_risk, sales=sales, ok=True,
        reason=(
            f"fitted on {len(sold)} sold and {len(usable) - len(sold)} unsold episodes"
        ),
    )
