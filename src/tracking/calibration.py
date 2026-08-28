"""How far your listings actually clear from what the model says.

The model learns from asks -- what sellers wanted. Your own listings are the one
place this tool sees what a buyer paid, so a factor measured from them is the
only correction here fitted on outcomes rather than intentions.

It is also the most dangerous number in the app, because it silently moves every
suggested price. So it is guarded the way `sell_strategy.estimate_multipliers`
guards its observed-spread multipliers, deliberately mirroring that code:

* **Evidence on both sides, or nothing.** Fast sales bracket the clearing ratio
  from below -- you would have got at least that. Stale listings bracket it from
  above. With only one side there is no bracket, just a direction, and a
  direction is not a factor.
* **Clamped, and it says when it clamped.**
* **A measurement far outside the sane range is refused, not clamped.** A factor
  of 0.25 does not mean sell at a quarter price; it means the model is
  miscalibrated, and quietly scaling by 0.25 would hide the one thing worth
  seeing.
* **Bound to a model version.** The model's own known underpricing is a hedging
  artefact with a measured fix, so this factor is measured *on top of* whatever
  the active model already does. Carry it across a model change and the two
  corrections stack, the second fitted on prices the first already moved.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Outside this the measurement is not seller behaviour, it is a broken model.
SANE_FACTOR_RANGE = (0.5, 2.0)


@dataclass(frozen=True)
class CalibrationConfig:
    min_sold: int = 20
    clamp: tuple[float, float] = (0.8, 1.3)


@dataclass(frozen=True)
class PersonalCalibration:
    factor: float = 1.0
    source: str = ""
    model_version: str = ""
    sample: int = 0
    applied: bool = False

    def applies_to(self, model_version: str) -> bool:
        """Whether this measurement may still be used against `model_version`."""
        return self.applied and bool(model_version) and model_version == self.model_version


def personal_factor(
    observations, model_version: str, cfg: CalibrationConfig
) -> PersonalCalibration:
    """The ratio your listings clear at, bracketed from both sides."""
    usable = [o for o in observations if o.ratio is not None]
    sold = [o for o in usable if o.sold]
    unsold = [o for o in usable if not o.sold]

    if len(usable) < cfg.min_sold:
        return PersonalCalibration(
            source=f"not applied: {len(usable)} usable episodes, {cfg.min_sold} needed",
            model_version=model_version,
            sample=len(usable),
        )
    if not sold or not unsold:
        return PersonalCalibration(
            source=(
                "not applied: needs evidence on both sides -- sales bracket the "
                "clearing ratio only from below and fix no upper bound"
            ),
            model_version=model_version,
            sample=len(usable),
        )

    lower = float(np.median([o.ratio for o in sold]))
    upper = float(np.median([o.ratio for o in unsold]))
    if upper <= lower:
        return PersonalCalibration(
            source=(
                "not applied: your stale listings are priced below your sold ones, "
                "so price ratio does not explain what clears"
            ),
            model_version=model_version,
            sample=len(usable),
        )

    measured = (lower + upper) / 2.0

    if not SANE_FACTOR_RANGE[0] <= measured <= SANE_FACTOR_RANGE[1]:
        return PersonalCalibration(
            source=(
                f"not applied: measured {measured:.2f}x, which is model "
                f"miscalibration rather than your selling -- retrain rather than "
                f"scaling every price by this"
            ),
            model_version=model_version,
            sample=len(usable),
        )

    low, high = cfg.clamp
    factor = min(max(measured, low), high)
    clamped = abs(factor - measured) > 1e-9
    return PersonalCalibration(
        factor=round(factor, 4),
        source=(
            f"measured {measured:.2f}x from {len(sold)} sold and {len(unsold)} unsold "
            f"episodes against {model_version}"
            + (f", clamped to {factor:.2f}" if clamped else "")
        ),
        model_version=model_version,
        sample=len(usable),
        applied=True,
    )
