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


#: How far below the current ask the search for a clearing price will go, and in
#: how many steps. Half is deliberate: past that the floor should be deciding,
#: not this.
_SCAN_FLOOR = 0.5
_SCAN_STEPS = 40


def recommend(
    price_chaos: float,
    hours_listed: float,
    predicted_chaos: float | None,
    interval_low_chaos: float | None,
    fit,
    target_window_hours: float,
    target_probability: float,
    cfg: LadderConfig,
) -> Recommendation | None:
    """The highest price that still clears inside your target window.

    A map that has not sold is evidence its clearing price is lower, but "lower"
    is not a number. The fit turns it into one: it knows, for a given ratio to
    the model's estimate, the odds of selling within the window. So the search
    walks down from what you are asking now and stops at the first price whose
    odds are acceptable -- the dearest price that still moves, derived rather
    than stepped.

    Where the fit cannot answer -- too little history, or no price it has seen
    clears in time -- this falls back to the ladder rather than extrapolating,
    and `source` says which happened. A guess labelled as a measurement is worse
    than a guess.
    """
    if price_chaos is None or price_chaos <= 0:
        return None
    if hours_listed < cfg.stale_after_hours:
        return None
    if not getattr(fit, "ok", False) or not predicted_chaos:
        return recommend_ladder(
            price_chaos, hours_listed, predicted_chaos, interval_low_chaos, cfg
        )

    current_ratio = price_chaos / predicted_chaos
    best_ratio: float | None = None
    best_probability = 0.0
    for step in range(_SCAN_STEPS + 1):
        ratio = current_ratio * (1.0 - (step / _SCAN_STEPS) * (1.0 - _SCAN_FLOOR))
        probability = fit.probability_within(ratio, target_window_hours)
        if probability is None:
            continue
        if probability >= target_probability:
            best_ratio, best_probability = ratio, probability
            break

    if best_ratio is None:
        # Nothing the fit has actually seen clears inside the window. That is a
        # real answer -- it is just not a price -- so hand back the ladder.
        return recommend_ladder(
            price_chaos, hours_listed, predicted_chaos, interval_low_chaos, cfg
        )

    candidate = predicted_chaos * best_ratio
    floor = interval_low_chaos
    if floor is None:
        floor = predicted_chaos * cfg.floor_ratio

    if candidate <= floor:
        return Recommendation(
            price_chaos=round(floor, 1),
            reason=(
                f"listed {hours_listed:.0f} h without selling, and the price that "
                f"clears within {target_window_hours:.0f} h is under the "
                f"{floor:.0f}c floor -- so {floor:.0f}c is as far as this should go."
            ),
            source="fit",
            floored=True,
        )

    return Recommendation(
        price_chaos=round(candidate, 1),
        reason=(
            f"listed {hours_listed:.0f} h at {price_chaos:.0f}c without selling. Your "
            f"maps at {best_ratio:.2f}x the estimate clear within "
            f"{target_window_hours:.0f} h {best_probability:.0%} of the time, so "
            f"{candidate:.0f}c is the dearest price that still moves."
        ),
        source="fit",
    )
