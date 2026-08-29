"""Is this map worth an Exalted Orb?

An Exalted Orb adds one random affix to an uncorrupted rare with a free slot.
No affix in the catalogue subtracts reward -- the fit that produces them is
non-negative by construction -- so the gamble is never whether the map gets
*worse*. It is whether the gain clears what the whole exercise costs, and
whether the model can tell that gain from its own error bar.

The next orb is always enumerated, never sampled. That pool is small and fully
known, so every outcome is priced by the same model that priced the map,
weighted by how often the affix appears in the collected market -- an exact
expectation, a real spread, and the name of the best and worst thing that can
happen. Filling *every* open slot is sampled instead, because two open slots is
some four thousand combinations and three is a hundred and twenty thousand, and
a stash tab is sixty maps. The sample is seeded per item, so a verdict about
spending currency does not flicker between yes and no on a page refresh.

Two constraints do real work. An affix already on the map cannot roll again, and
a full prefix block means only suffixes remain, which matters because prefixes
and suffixes do not contribute equally.

Why the answer is not simply "is the expectation bigger than the orb"
---------------------------------------------------------------------
It was, and it said yes to every map it was ever shown -- 92 of 92 on the
collected market, which is not a recommendation, it is a stuck needle.

Four things were missing, and `ExaltPolicy` is all four.

**The orbs, plural.** A map with two open slots is a two-orb decision, and
charging one orb for it prices a plan nobody carries out.

**The trade.** Every gain here has to be realised by listing the map, waiting,
answering a whisper and meeting someone. That cost is flat, it is not small
next to a cheap map, and pretending it is zero is what made "20c map, two orbs,
now a 25c map" score as a win.

**The model's own uncertainty.** This is the one that matters. The gain is a
*difference between two predictions of the same model*, and on the collected
market 91 of 92 of those differences were smaller than the model's own 80%
conformal band on the very same map. Acting on a difference the instrument
cannot resolve is not a gamble, it is noise with a recommendation attached.
So the *net* gain -- what survives paying for the orbs and the trade -- has to
clear that whole band. Stated plainly: keep more than the model admits it
cannot measure. That single bar is what takes 92 of 92 down to 28.

**The floor.** Under 30c the model is not fitted at all: `decision_bands` holds
those listings out of training, and its docstring is blunt that the number down
there "is not a cheap price, it is the absence of one". Doing arithmetic on
differences of that number produced confident advice about maps the model has
nothing to say about, so those now get no verdict rather than a cheerful one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol, Sequence

import numpy as np

from src.ml.affix_catalogue import AffixCatalogue, AffixEntry
from src.ml.decision_bands import TRAINING_FLOOR_CHAOS
from src.parsing.affixes import AffixSet
from src.schema import NormalizedMap

#: Percentiles reported either side of the expectation.
LOW_PERCENTILE = 10.0
HIGH_PERCENTILE = 90.0

#: Why a verdict that *could* be given came out as no. "" means it passed.
REJECT_NONE = ""
REJECT_NEGATIVE_EV = "negative_ev"
REJECT_THIN_MARGIN = "thin_margin"
REJECT_LOW_CHANCE = "low_chance"
REJECT_INSIDE_NOISE = "inside_noise"


@dataclass(frozen=True)
class ExaltPolicy:
    """The bars an exalt has to clear, and the reasons each one exists.

    Defaults measured against the collected market rather than picked: under
    them, 92 of 92 "worth exalting" verdicts become 28. The result is stable at
    28 for effort costs of 3c, 5c and 8c, which says the resolution gate is
    doing the work and the numbers are not tuned to one lucky value.
    """

    #: Charge an orb for every open slot, not just the next one.
    charge_every_open_slot: bool = True
    #: The flat cost of turning a gain into currency: list it, wait, answer a
    #: whisper, meet someone. Flat because a trade takes the same few minutes
    #: whatever the map is worth, which is exactly why it bites hardest on the
    #: cheap maps this was saying yes to.
    effort_chaos: float = 5.0
    #: Net gain as a share of what the map is already worth. A 30c map has to
    #: gain more than 2.4c to be worth handling twice.
    min_net_gain_fraction: float = 0.08
    #: Share of outcomes that must actually beat the cost. Does not currently
    #: bind on the collected market -- kept as a guard for other leagues and
    #: thinner models, not claimed as load-bearing.
    min_chance_of_profit: float = 0.40
    #: The net gain must clear this much of the model's own 80% upside band.
    #: Measured on the net rather than the gross, because the question is
    #: whether what you actually keep is bigger than what the model admits it
    #: cannot measure -- and at 1.0 that is a sentence with no fudge factor in
    #: it. On the collected market this is the gate that does the work: it is
    #: what turns 92-of-92 into 28.
    min_resolution_ratio: float = 1.0
    #: No verdict at all below this. Imported from decision_bands rather than
    #: restated, so the exalt floor and the training floor cannot drift apart.
    require_training_floor: bool = True
    #: Sampled fills when the whole open block is too large to enumerate.
    samples: int = 200
    #: Enumerate exactly while the combination count is no larger than this.
    exact_outcome_budget: int = 2000
    seed: int = 7

    @classmethod
    def from_config(cls, cfg) -> "ExaltPolicy":
        get = cfg.get
        base = cls()
        return cls(
            charge_every_open_slot=bool(
                get("exalt.charge_every_open_slot", base.charge_every_open_slot)
            ),
            effort_chaos=float(get("exalt.effort_chaos", base.effort_chaos)),
            min_net_gain_fraction=float(
                get("exalt.min_net_gain_fraction", base.min_net_gain_fraction)
            ),
            min_chance_of_profit=float(
                get("exalt.min_chance_of_profit", base.min_chance_of_profit)
            ),
            min_resolution_ratio=float(
                get("exalt.min_resolution_ratio", base.min_resolution_ratio)
            ),
            require_training_floor=bool(
                get("exalt.require_training_floor", base.require_training_floor)
            ),
            samples=int(get("exalt.samples", base.samples)),
        )

    def required_gain_chaos(self, current_chaos: float | None) -> float:
        """The bar this map's net gain has to clear."""
        return self.min_net_gain_fraction * float(current_chaos or 0.0)

    def rejection_for(
        self,
        *,
        net_gain_chaos: float,
        chance_of_profit: float | None,
        current_chaos: float | None,
        expected_gain_chaos: float | None = None,
        resolution_chaos: float | None = None,
    ) -> str:
        """The first bar this verdict fails, or REJECT_NONE.

        Ordered most decisive first, so the sentence the page prints is the one
        that actually settles it rather than whichever happened to be checked
        last.
        """
        if net_gain_chaos <= 0:
            return REJECT_NEGATIVE_EV
        if net_gain_chaos < self.required_gain_chaos(current_chaos):
            return REJECT_THIN_MARGIN
        # `None` means a verdict built by hand with no distribution behind it.
        # Silently failing it would turn "not measured" into "measured badly".
        if chance_of_profit is not None and chance_of_profit < self.min_chance_of_profit:
            return REJECT_LOW_CHANCE
        # Deliberately the net, not the gross: the gross gain is what the model
        # claims the orbs add, the net is what survives paying for them, and it
        # is the second one that has to be distinguishable from zero.
        if resolution_chaos and net_gain_chaos < self.min_resolution_ratio * resolution_chaos:
            return REJECT_INSIDE_NOISE
        return REJECT_NONE


DEFAULT_POLICY = ExaltPolicy()


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
    policy: ExaltPolicy = DEFAULT_POLICY
    #: How many orbs this verdict is about -- the open slots, capped by how
    #: many affixes could actually still roll into them.
    orbs: int = 1
    #: The price of one orb. `cost_chaos` is the whole programme: orbs + effort.
    orb_cost_chaos: float = 0.0
    effort_chaos: float = 0.0
    #: The model's own 80% upside band on this map, in chaos. The yardstick the
    #: gain is measured against, carried so the page can print it.
    resolution_chaos: float | None = None
    #: True when the full fill was sampled rather than enumerated.
    sampled: bool = False
    sample_count: int = 0

    @property
    def rejection(self) -> str:
        """Which bar this failed, or "" -- including "" when it passed."""
        if not self.available:
            return REJECT_NONE  # nothing was judged, so nothing was rejected
        return self.policy.rejection_for(
            net_gain_chaos=self.net_gain_chaos or 0.0,
            chance_of_profit=self.chance_of_profit,
            current_chaos=self.current_chaos,
            expected_gain_chaos=self.expected_gain_chaos,
            resolution_chaos=self.resolution_chaos,
        )

    @property
    def worth_it(self) -> bool:
        """Derived, never stored.

        The web grid, the overlay ring and the stat-rail counter all used to
        re-implement this from `net_gain_chaos`, which meant four places to
        change and four chances for the tile, the ring and the count to
        disagree about the same map.
        """
        return bool(self.available and not self.rejection)

    @property
    def required_gain_chaos(self) -> float:
        return self.policy.required_gain_chaos(self.current_chaos)

    def cost_label(self) -> str:
        """"2 orbs at 1.2c, plus 5c to trade it" -- what is being spent."""
        orbs = f"{self.orbs} orb{'s' if self.orbs != 1 else ''}"
        parts = [f"{orbs} at {self.orb_cost_chaos:.4g}c"]
        if self.effort_chaos:
            parts.append(f"{self.effort_chaos:.4g}c to list and trade it")
        return ", plus ".join(parts)

    def headline(self) -> tuple[str, str]:
        """(verdict, why) for this map, as the page should say it.

        Kept here rather than in the page so that the words and the arithmetic
        cannot drift apart, and so the overlay can put the same sentence in a
        tooltip without importing Streamlit.
        """
        if not self.available:
            return "No verdict.", self.reason
        net = self.net_gain_chaos or 0.0
        gain = self.expected_gain_chaos or 0.0
        rejection = self.rejection
        if not rejection:
            return (
                "Worth exalting.",
                f"Filling {self.orbs} open slot{'s' if self.orbs != 1 else ''} costs "
                f"{self.cost_chaos:.4g}c ({self.cost_label()}) and is expected to add "
                f"{gain:.4g}c -- a net {net:+.4g}c.",
            )
        if rejection == REJECT_NEGATIVE_EV:
            return (
                "Not worth the orbs.",
                f"Expected to add {gain:.4g}c against {self.cost_chaos:.4g}c to do it "
                f"({self.cost_label()}). It costs more than it adds.",
            )
        if rejection == REJECT_THIN_MARGIN:
            return (
                "Too thin to bother.",
                f"A net {net:+.4g}c on a {self.current_chaos:.4g}c map, against a "
                f"{self.required_gain_chaos:.4g}c bar -- "
                f"{self.policy.min_net_gain_fraction:.0%} of what it is already worth.",
            )
        if rejection == REJECT_LOW_CHANCE:
            return (
                "A gamble, not an upgrade.",
                f"The average says {net:+.4g}c, but only "
                f"{(self.chance_of_profit or 0):.0%} of the outcomes actually beat "
                "what it costs.",
            )
        return (
            "Inside the model's own error.",
            f"A net {net:+.4g}c, against a {self.resolution_chaos:.4g}c band the model "
            f"cannot pin this map's price down to. It cannot tell that gain from zero, "
            "so neither can this.",
        )

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
    policy: ExaltPolicy = DEFAULT_POLICY,
) -> ExaltVerdict:
    """Is filling this map's open slots worth what it costs to do and to sell?

    `cost_chaos` is the price of *one* orb; the verdict charges for as many as
    the map has open slots, plus one flat effort cost for the trade.
    """
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

    # Free to check, and it has to happen before anything is priced. The
    # catalogue only models the features its additivity fit could explain, and
    # an affix that moves none of them produces a byte-identical simulated map.
    # Those used to come back as a confident "gain: 0c, not worth it"; the
    # honest answer is that there is nothing here to price.
    modelled = tuple(getattr(catalogue, "modelled_features", ()) or ())
    if modelled and not any(
        entry.contributions.get(name, 0.0) for entry in candidates for name in modelled
    ):
        return ExaltVerdict(
            False,
            "the catalogue models "
            + ", ".join(modelled)
            + ", and nothing that could roll here changes any of them -- there is "
            "no difference to price",
            affixes,
        )

    base_features = item.features()
    simulated = [
        replace(item, **{k: round(v) for k, v in catalogue.apply(base_features, entry).items()})
        for entry in candidates
    ]

    # How many orbs this really is. Capped by the pool: three open suffixes and
    # two suffixes left in the catalogue is a two-orb decision, not three.
    open_slots = max(1, affixes.open_slots) if affixes.open_slots else 1
    orbs = min(open_slots, len(candidates)) if policy.charge_every_open_slot else 1
    fills = (
        _sample_fills(item, affixes, catalogue, candidates, orbs, policy)
        if orbs > 1
        else []
    )

    values = np.asarray(
        predictor.predict_chaos([item, *simulated, *fills], divine_to_chaos), dtype=float
    )
    current = float(values[0])
    predicted = values[1 : 1 + len(simulated)]
    fill_values = values[1 + len(simulated):]

    # Under the training floor the model returns roughly the same number for
    # every map, because listings below it are held out of the fit entirely.
    # `decision_bands` says it outright: that number is not a cheap price, it
    # is the absence of one -- and a difference of two of them is nothing.
    if policy.require_training_floor and current < TRAINING_FLOOR_CHAOS:
        return ExaltVerdict(
            False,
            f"this map prices at {current:.0f}c, under the {TRAINING_FLOOR_CHAOS:g}c "
            "floor the model is trained above. Down here it returns much the same "
            "number for every map, so an exalt verdict would be arithmetic on noise",
            affixes,
            current_chaos=current,
            policy=policy,
        )

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
    ordered = sorted(outcomes, key=lambda o: o.gain_chaos)

    orb_cost = float(cost_chaos)
    total_cost = orbs * orb_cost + float(policy.effort_chaos)

    # The next orb is always the exact enumeration above. The whole fill is the
    # sample, when there is more than one slot to fill.
    if len(fill_values):
        outcome_gains = np.asarray(fill_values, dtype=float) - current
        weights = np.full(len(outcome_gains), 1.0 / len(outcome_gains))
    else:
        outcome_gains, weights = gains, probabilities

    expected_gain = float((weights * outcome_gains).sum())
    resolution = _resolution_chaos(predictor, current)

    return ExaltVerdict(
        available=True,
        affixes=affixes,
        current_chaos=current,
        cost_chaos=total_cost,
        orb_cost_chaos=orb_cost,
        effort_chaos=float(policy.effort_chaos),
        orbs=orbs,
        expected_value_chaos=current + expected_gain,
        expected_gain_chaos=expected_gain,
        net_gain_chaos=expected_gain - total_cost,
        low_gain_chaos=_weighted_percentile(outcome_gains, weights, LOW_PERCENTILE),
        high_gain_chaos=_weighted_percentile(outcome_gains, weights, HIGH_PERCENTILE),
        chance_of_profit=float(weights[outcome_gains > total_cost].sum()),
        best=ordered[-1],
        worst=ordered[0],
        outcomes=outcomes,
        policy=policy,
        resolution_chaos=resolution,
        sampled=bool(len(fill_values)),
        sample_count=int(len(fill_values)),
    )


def _resolution_chaos(predictor: Any, current_chaos: float) -> float | None:
    """How much of this map's price the model cannot pin down, upward.

    The 80% conformal band's upper half, in chaos. It is the only uncertainty
    measure the predictor exposes, and it is about the *level* rather than
    about a difference -- which is why the policy asks the gain to clear half
    of it rather than all of it. A predictor without conformal metadata (the
    stubs in the tests) returns None, and the gate then does not apply.
    """
    ratios = getattr(predictor, "interval_ratios", None)
    if ratios is None:
        return None
    try:
        _, high = ratios(current_chaos)
    except Exception:  # noqa: BLE001 - an absent band is not an error here
        return None
    return max(0.0, (float(high) - 1.0) * float(current_chaos))


def _sample_fills(item, affixes, catalogue, candidates, orbs, policy) -> list:
    """Maps with every open slot filled, drawn rather than enumerated.

    Enumerating is out of reach: ~90 legal affixes is four thousand unordered
    pairs and a hundred and twenty thousand triples, per map, on a page that
    prices sixty maps at once.

    Order does not matter. `catalogue.apply` only ever adds to the modelled
    features, so a set of draws lands on the same map whichever sequence it
    arrives in -- which is what makes sampling the multiset legitimate rather
    than an approximation of a sequence. The rounding is done once at the end
    for the same reason: rounding per orb accumulates drift.

    Seeded from the item's own features, so the same map always gets the same
    answer. A recommendation about spending currency that changes when the page
    refreshes is worse than one that is slightly coarse.
    """
    rng = np.random.default_rng(
        abs(hash((policy.seed, tuple(sorted(item.features().items())), orbs))) % (2**32)
    )
    weights = np.array([max(e.frequency, 0) for e in candidates], dtype=float)
    if weights.sum() <= 0:
        weights = np.ones(len(candidates))

    prefixes_left = affixes.open_prefixes
    suffixes_left = affixes.open_suffixes
    base = item.features()
    fills = []
    for _ in range(max(1, policy.samples)):
        available = list(range(len(candidates)))
        room = {"prefix": prefixes_left, "suffix": suffixes_left}
        features = dict(base)
        drawn = 0
        while drawn < orbs and available:
            pool = [i for i in available if room.get(candidates[i].slot, 0) > 0]
            if not pool:
                break
            w = weights[pool]
            index = int(rng.choice(pool, p=w / w.sum())) if w.sum() > 0 else int(rng.choice(pool))
            entry = candidates[index]
            features = catalogue.apply(features, entry)
            room[entry.slot] = room.get(entry.slot, 0) - 1
            available.remove(index)
            drawn += 1
        fills.append(replace(item, **{k: round(v) for k, v in features.items()}))
    return fills


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, percentile: float) -> float:
    """Percentile of a discrete distribution, weighted by probability."""
    order = np.argsort(values)
    sorted_values = values[order]
    cumulative = np.cumsum(weights[order])
    cumulative /= cumulative[-1]
    index = int(np.searchsorted(cumulative, percentile / 100.0))
    return float(sorted_values[min(index, len(sorted_values) - 1)])
