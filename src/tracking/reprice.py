"""What to re-price a map to when it has not sold.

A map that has not sold at its price over some hours is evidence that its
clearing price is lower. This module turns that into a number, and is careful
about two things.

**It says where the number came from.** The ladder here is a fallback: fixed
steps by staleness, used until there is enough of your own sale history to fit
the real thing. A fallback labelled as a measurement is worse than no number at
all, so `source` is on every recommendation and the page prints it.

**It has a floor.** Left alone, a ladder walks a map that nobody wants down
until it is being given away -- each markdown producing more evidence that the
price is still too high, which is exactly what a ladder is built to react to.
The floor is the bottom of the model's own confidence interval: below that, the
honest answer is that the market may not want this map at a sensible price, not
a smaller number.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LadderConfig:
    #: (hours listed, multiplier) rungs, ascending by hours. The last rung whose
    #: hours the listing has passed is the one that applies.
    steps: tuple[tuple[float, float], ...] = ((24, 0.92), (72, 0.85), (168, 0.78))
    #: Hard floor as a fraction of the prediction, used when no interval is known.
    floor_ratio: float = 0.75
    stale_after_hours: float = 24.0


@dataclass(frozen=True)
class Recommendation:
    price_chaos: float
    reason: str
    source: str  # "ladder" | "fit"
    floored: bool = False


def recommend_ladder(
    price_chaos: float,
    hours_listed: float,
    predicted_chaos: float | None,
    interval_low_chaos: float | None,
    cfg: LadderConfig,
) -> Recommendation | None:
    """A lower price for a listing that has sat, or None if it has not sat long."""
    if price_chaos is None or price_chaos <= 0:
        return None

    rung = None
    for hours, multiplier in cfg.steps:
        if hours_listed >= hours:
            rung = multiplier
    if rung is None:
        return None

    floor = interval_low_chaos
    if floor is None and predicted_chaos is not None:
        floor = predicted_chaos * cfg.floor_ratio

    stepped = price_chaos * rung
    if floor is not None and stepped <= floor:
        at_floor = price_chaos <= floor + 1e-6
        reason = (
            f"listed {hours_listed:.0f} h and has not sold, but {stepped:.0f}c would "
            f"be under the {floor:.0f}c floor. "
            + (
                "It is already at or below the floor -- the market may simply not "
                "want it at a sensible price."
                if at_floor
                else f"Recommending the floor, {floor:.0f}c."
            )
        )
        return Recommendation(
            price_chaos=round(floor, 1), reason=reason, source="ladder", floored=True
        )

    return Recommendation(
        price_chaos=round(stepped, 1),
        reason=(
            f"listed {hours_listed:.0f} h at {price_chaos:.0f}c and has not sold; "
            f"stepping to {stepped:.0f}c. (Fallback ladder -- not enough of your own "
            f"sale history yet to measure what actually clears.)"
        ),
        source="ladder",
    )
