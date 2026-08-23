"""Is this map worth an Exalted Orb?

An Exalted Orb adds one random affix to an uncorrupted rare with a free slot.
Because every affix in the catalogue contributes positive quantity, the gamble
is never whether the map gets *worse* -- it is whether the gain clears the price
of the orb, and how wide the spread of outcomes is underneath the average.

The calculation enumerates rather than samples. The pool of affixes that could
roll is small and fully known, so every outcome is priced with the same model
that priced the map, weighted by how often that affix actually appears in the
collected market. That gives an exact expectation, a real spread, and the name
of the best and worst thing that can happen -- none of which a single averaged
number would tell you.

Two constraints do real work. An affix already on the map cannot roll again, and
a full prefix block means only suffixes remain, which matters because prefixes
and suffixes do not contribute equally.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol, Sequence

import numpy as np

from src.ml.affix_catalogue import AffixCatalogue, AffixEntry
from src.parsing.affixes import AffixSet
from src.schema import NormalizedMap

#: Percentiles reported either side of the expectation.
LOW_PERCENTILE = 10.0
HIGH_PERCENTILE = 90.0


class SupportsPredict(Protocol):
    def predict_chaos(
        self, items: Sequence[NormalizedMap], divine_to_chaos: float | None = ...
    ) -> Any: ...


@dataclass(frozen=True)
class ExaltOutcome:
    """One affix the orb could produce, and what the map would then be worth."""

    affix: AffixEntry
    item: NormalizedMap
    probability: float
    value_chaos: float
    gain_chaos: float


@dataclass
class ExaltVerdict:
    available: bool
    reason: str = ""
    affixes: AffixSet | None = None
    current_chaos: float | None = None
    cost_chaos: float = 0.0
    expected_value_chaos: float | None = None
    expected_gain_chaos: float | None = None
    net_gain_chaos: float | None = None
    low_gain_chaos: float | None = None
    high_gain_chaos: float | None = None
    chance_of_profit: float | None = None
    best: ExaltOutcome | None = None
    worst: ExaltOutcome | None = None
    outcomes: tuple[ExaltOutcome, ...] = ()

    @property
    def worth_it(self) -> bool:
        return bool(self.available and (self.net_gain_chaos or 0.0) > 0)

    @property
    def affix_label(self) -> str:
        return self.affixes.label() if self.affixes else "affixes unknown"

    def slot_label(self) -> str:
        """Which slots are open, e.g. '1 open suffix'."""
        if self.affixes is None or not self.affixes.known:
            return ""
        parts = []
        if self.affixes.open_prefixes:
            parts.append(f"{self.affixes.open_prefixes} open prefix")
        if self.affixes.open_suffixes:
            parts.append(f"{self.affixes.open_suffixes} open suffix")
        return ", ".join(parts) or "no open slots"


def evaluate_exalt(
    item: NormalizedMap,
    affixes: AffixSet,
    catalogue: AffixCatalogue | None,
    predictor: SupportsPredict | None,
    cost_chaos: float,
    divine_to_chaos: float | None = None,
) -> ExaltVerdict:
    """Expected value of adding one random affix, against the price of the orb."""
    if catalogue is None:
        return ExaltVerdict(False, "no affix catalogue -- train a model first", affixes)
    if predictor is None:
        return ExaltVerdict(False, "no trained model to price the outcome with", affixes)
    if affixes is None or not affixes.known:
        reason = (affixes.reason if affixes else "") or "affix structure unavailable"
        return ExaltVerdict(False, reason, affixes)
    if affixes.corrupted:
        return ExaltVerdict(False, "corrupted maps cannot be modified", affixes)
    if affixes.is_full:
        return ExaltVerdict(
            False, f"already has all {affixes.max_affixes} affixes -- no open slot", affixes
        )

    candidates = catalogue.candidates(affixes)
    if not candidates:
        return ExaltVerdict(
            False, "no affix in the catalogue can roll into the open slot", affixes
        )

    base_features = item.features()
    simulated = [
        replace(item, **{k: round(v) for k, v in catalogue.apply(base_features, entry).items()})
        for entry in candidates
    ]

    values = np.asarray(predictor.predict_chaos([item, *simulated], divine_to_chaos), dtype=float)
    current = float(values[0])
    predicted = values[1:]

    frequencies = np.array([max(e.frequency, 0) for e in candidates], dtype=float)
    if frequencies.sum() <= 0:
        # No frequency information: every candidate is equally likely.
        frequencies = np.ones(len(candidates))
    probabilities = frequencies / frequencies.sum()

    gains = predicted - current
    outcomes = tuple(
        ExaltOutcome(
            affix=entry,
            item=sim,
            probability=float(p),
            value_chaos=float(v),
            gain_chaos=float(g),
        )
        for entry, sim, p, v, g in zip(candidates, simulated, probabilities, predicted, gains)
    )

    expected_gain = float((probabilities * gains).sum())
    ordered = sorted(outcomes, key=lambda o: o.gain_chaos)

    return ExaltVerdict(
        available=True,
        affixes=affixes,
        current_chaos=current,
        cost_chaos=float(cost_chaos),
        expected_value_chaos=current + expected_gain,
        expected_gain_chaos=expected_gain,
        net_gain_chaos=expected_gain - float(cost_chaos),
        low_gain_chaos=_weighted_percentile(gains, probabilities, LOW_PERCENTILE),
        high_gain_chaos=_weighted_percentile(gains, probabilities, HIGH_PERCENTILE),
        chance_of_profit=float(probabilities[gains > float(cost_chaos)].sum()),
        best=ordered[-1],
        worst=ordered[0],
        outcomes=outcomes,
    )


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, percentile: float) -> float:
    """Percentile of a discrete distribution, weighted by probability."""
    order = np.argsort(values)
    sorted_values = values[order]
    cumulative = np.cumsum(weights[order])
    cumulative /= cumulative[-1]
    index = int(np.searchsorted(cumulative, percentile / 100.0))
    return float(sorted_values[min(index, len(sorted_values) - 1)])
