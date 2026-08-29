"""Telling you a map was mispriced, but only when the data actually says so.

Two rules keep this from crying wolf.

**The bracket has to be decisive.** A map that sold somewhere between twenty
minutes and nine hours might have sold fast or might have sold slowly, and the
overnight gap that produced that bracket is not a licence to guess. An alert
fires only when the whole bracket sits on one side of the threshold. The
observation still feeds the fit -- it is not wasted, it simply does not get to
make a claim on screen.

**The threshold is yours, not mine.** "Fast" is meaningless in the abstract; it
depends on your maps, your league and your prices. So the thresholds start from
config and are replaced by your own quartiles once there are enough sales to have
quartiles -- the same idiom `sell_strategy.estimate_multipliers` already uses for
the strategy multipliers, and for the same reason.

Note which side of the data each alert rests on. "Overpriced" is measured
exactly: a map still listed after sixty hours has been listed sixty hours
whatever the polling gaps. "Underpriced" is the side the gaps damage, which is
why it carries the decisive-bracket rule and the other does not need it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

MIN_SOLD_FOR_OBSERVED = 20


@dataclass(frozen=True)
class Thresholds:
    fast_hours: float
    slow_hours: float
    source: str  # "config" | "observed"


@dataclass(frozen=True)
class Alert:
    listing_id: int
    kind: str  # "underpriced" | "overpriced"
    headline: str
    evidence: str


def observed_thresholds(
    observations,
    fallback_fast: float,
    fallback_slow: float,
    min_sold: int = MIN_SOLD_FOR_OBSERVED,
) -> Thresholds:
    """Your own lower and upper quartile time-to-sell, once there are enough sales."""
    sold = [o for o in observations if o.sold and o.hi_hours is not None]
    if len(sold) < min_sold:
        return Thresholds(fallback_fast, fallback_slow, "config")
    # The upper bound of each bracket is the conservative end: it is the latest
    # the sale can have happened, so a threshold built from it never overstates
    # how quickly your maps move.
    times = np.array([o.hi_hours for o in sold], dtype=float)
    fast, slow = (float(v) for v in np.quantile(times, [0.25, 0.75]))
    return Thresholds(fast_hours=fast, slow_hours=slow, source="observed")


def _hours(value: float) -> str:
    return f"{value * 60:.0f} min" if value < 1 else f"{value:.0f} h"


def alerts_for(rows: Sequence[dict], thresholds: Thresholds) -> list[Alert]:
    """Every listing worth telling the user about, with the evidence behind it."""
    out: list[Alert] = []
    for row in rows:
        if row.get("exclude_from_learning") or row.get("ambiguous"):
            continue
        ask = row.get("price_chaos")
        model = row.get("predicted_chaos")
        if not ask or not model:
            continue

        if row.get("status") == "gone":
            latest = row.get("sold_hi_hours")
            # Not decisively fast: the bracket straddles the threshold, so the
            # honest answer is that we do not know how quickly it went.
            if latest is None or latest > thresholds.fast_hours:
                continue
            if ask > model:
                continue
            out.append(
                Alert(
                    listing_id=row["id"],
                    kind="underpriced",
                    headline=f"{row.get('name') or 'map'} sold within {_hours(latest)}",
                    evidence=(
                        f"asked {ask:.0f}c against a {model:.0f}c estimate and it went "
                        f"within {_hours(latest)}, inside your "
                        f"{_hours(thresholds.fast_hours)} fast mark "
                        f"({thresholds.source}). The market would likely have paid more."
                    ),
                )
            )
            continue

        hours = row.get("hours_listed") or 0.0
        if hours <= thresholds.slow_hours or ask <= model:
            continue
        out.append(
            Alert(
                listing_id=row["id"],
                kind="overpriced",
                headline=f"{row.get('name') or 'map'} has sat {_hours(hours)}",
                evidence=(
                    f"asking {ask:.0f}c against a {model:.0f}c estimate, listed "
                    f"{_hours(hours)} -- past your {_hours(thresholds.slow_hours)} mark "
                    f"({thresholds.source})."
                ),
            )
        )
    return out
