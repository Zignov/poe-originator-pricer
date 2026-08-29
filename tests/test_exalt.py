"""The exalt-worthy judgement.

No affix in the catalogue subtracts reward, so exalting never destroys value.
That makes the interesting question not "could this go wrong" but "is the gain
real, and is it worth the trouble" -- which is four separate questions, and the
old rule asked only the first and easiest of them.

Two policies run through this file. `SINGLE_ORB` keeps the mechanics tests
honest about one orb at a time, which is where the enumeration, the frequency
weighting and the slot constraints live. `DEFAULT_POLICY` is what ships, and
the tests at the bottom are about the bars it adds.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.ml.affix_catalogue import AffixCatalogue, AffixEntry
from src.parsing.affixes import extract_affixes
from src.pricing.exalt import (
    REJECT_INSIDE_NOISE,
    REJECT_LOW_CHANCE,
    REJECT_NEGATIVE_EV,
    REJECT_THIN_MARGIN,
    ExaltPolicy,
    evaluate_exalt,
)
from src.schema import FEATURE_NAMES, NormalizedMap


def _mod(name: str, tier: str) -> dict:
    return {"description": f"{name}", "mods": [{"name": name, "tier": tier}]}


def _entry(name: str, tier: str, slot: str, quantity: float, frequency: int = 100) -> AffixEntry:
    return AffixEntry(
        name=name, tier=tier, slot=slot, frequency=frequency,
        contributions={"quantity": quantity},
    )


CATALOGUE = AffixCatalogue(
    entries=(
        _entry("Fecund", "P1", "prefix", 19.0),
        _entry("Diluted", "P1", "prefix", 10.0),
        _entry("of Power", "S1", "suffix", 8.0),
        _entry("of Defiance", "S1", "suffix", 2.0),
        _entry("of Haste", "S2", "suffix", 4.0),
    ),
    modelled_features=("quantity",),
    fit_quality={"quantity": {"r2": 1.0, "mae": 0.0, "modelled": True}},
    sample_size=1000,
    league="Allflame",
    map_tier=16,
)


class StubPredictor:
    """Price is 2 chaos per point of quantity, so gains are exactly predictable."""

    def predict_chaos(self, items, divine_to_chaos=None):
        return np.array([2.0 * item.quantity for item in items], dtype=float)


def _map(quantity: int = 50, **kwargs) -> NormalizedMap:
    return NormalizedMap(
        base_type="Map (Tier 16)", map_tier=16, is_originator=True, supported=True,
        quantity=quantity, **kwargs,
    )


def _affixes(*pairs, corrupted: bool = False):
    return extract_affixes(
        {"explicitMods": [_mod(n, t) for n, t in pairs], "corrupted": corrupted}
    )


ONE_OPEN_SUFFIX = (("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2"),
                   ("of Power", "S1"), ("of Defiance", "S1"))


#: One orb, no trade cost, no floor: the mechanics, isolated from the policy.
#: The gates get their own tests rather than being smuggled into every other
#: assertion in the file.
SINGLE_ORB = ExaltPolicy(
    charge_every_open_slot=False,
    effort_chaos=0.0,
    min_net_gain_fraction=0.0,
    min_chance_of_profit=0.0,
    min_resolution_ratio=0.0,
    require_training_floor=False,
)


def _evaluate(item, affixes, cost=10.0, policy=SINGLE_ORB, predictor=None):
    return evaluate_exalt(
        item, affixes, CATALOGUE, predictor or StubPredictor(),
        cost_chaos=cost, policy=policy,
    )


# ---------------------------------------------------------------------------
def test_a_full_map_is_refused_with_a_reason():
    full = _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2"),
                    ("of Power", "S1"), ("of Defiance", "S1"), ("of Haste", "S2"))
    verdict = _evaluate(_map(), full)
    assert not verdict.available
    assert "no open" in verdict.reason.lower() or "full" in verdict.reason.lower()


def test_a_corrupted_map_is_refused():
    verdict = _evaluate(_map(corrupted=True), _affixes(("Fecund", "P1"), corrupted=True))
    assert not verdict.available
    assert "corrupt" in verdict.reason.lower()


def test_an_unknown_affix_set_is_refused_rather_than_guessed():
    verdict = _evaluate(_map(), extract_affixes({}))
    assert not verdict.available
    assert verdict.expected_gain_chaos is None


# ---------------------------------------------------------------------------
def test_only_suffixes_are_considered_when_prefixes_are_full():
    """Three prefixes rolled means an exalt cannot produce a prefix."""
    verdict = _evaluate(_map(), _affixes(*ONE_OPEN_SUFFIX))
    assert verdict.available
    assert {o.affix.slot for o in verdict.outcomes} == {"suffix"}


def test_an_affix_already_on_the_map_cannot_roll_again():
    verdict = _evaluate(_map(), _affixes(*ONE_OPEN_SUFFIX))
    assert ("of Power", "S1") not in {o.affix.key for o in verdict.outcomes}
    assert ("of Defiance", "S1") not in {o.affix.key for o in verdict.outcomes}


def test_the_expected_gain_is_the_frequency_weighted_average():
    """Two prefixes open, +19 and +10 quantity, equal frequency, 2c per point."""
    partial = _affixes(("of Power", "S1"))
    verdict = _evaluate(_map(quantity=50), partial)
    prefix_gains = {o.affix.name: o.gain_chaos for o in verdict.outcomes if o.affix.slot == "prefix"}
    assert prefix_gains["Fecund"] == pytest.approx(38.0)
    assert prefix_gains["Diluted"] == pytest.approx(20.0)
    expected = sum(o.probability * o.gain_chaos for o in verdict.outcomes)
    assert verdict.expected_gain_chaos == pytest.approx(expected)


def test_probabilities_are_proportional_to_observed_frequency():
    catalogue = AffixCatalogue(
        entries=(_entry("Common", "S1", "suffix", 10.0, frequency=300),
                 _entry("Rare", "S2", "suffix", 10.0, frequency=100)),
        modelled_features=("quantity",), fit_quality={}, sample_size=400,
    )
    verdict = evaluate_exalt(
        _map(), _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2")),
        catalogue, StubPredictor(), cost_chaos=1.0,
    )
    by_name = {o.affix.name: o.probability for o in verdict.outcomes}
    assert by_name["Common"] == pytest.approx(0.75)
    assert by_name["Rare"] == pytest.approx(0.25)
    assert sum(by_name.values()) == pytest.approx(1.0)


def test_the_orb_cost_is_netted_off():
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=10.0)
    assert verdict.net_gain_chaos == pytest.approx(verdict.expected_gain_chaos - 10.0)


def test_every_open_slot_is_charged_for_not_just_the_first():
    """A map with three slots open is a three-orb decision, and pricing it as a
    one-orb decision recommends a plan nobody actually carries out."""
    # Deep enough that the pool is not what limits the plan -- that cap has its
    # own test below, and conflating the two hides whichever one breaks.
    catalogue = AffixCatalogue(
        entries=tuple(
            _entry(f"of S{i}", f"S{i}", "suffix", 4.0) for i in range(1, 5)
        ) + (_entry("Deep", "P9", "prefix", 6.0),),
        modelled_features=("quantity",), fit_quality={}, sample_size=400,
    )
    three_open = _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("of Power", "S1"))
    policy = ExaltPolicy(effort_chaos=0.0, min_net_gain_fraction=0.0,
                         min_chance_of_profit=0.0, min_resolution_ratio=0.0,
                         require_training_floor=False)
    verdict = evaluate_exalt(
        _map(), three_open, catalogue, StubPredictor(), cost_chaos=2.0, policy=policy
    )
    assert verdict.orbs == 3, "one open prefix and two open suffixes"
    assert verdict.cost_chaos == pytest.approx(6.0)


def test_the_orbs_are_capped_by_what_could_actually_still_roll():
    """Three open suffixes against two remaining suffixes is a two-orb plan."""
    catalogue = AffixCatalogue(
        entries=(_entry("of A", "S1", "suffix", 5.0), _entry("of B", "S2", "suffix", 5.0)),
        modelled_features=("quantity",), fit_quality={}, sample_size=400,
    )
    policy = ExaltPolicy(effort_chaos=0.0, min_net_gain_fraction=0.0,
                         min_chance_of_profit=0.0, min_resolution_ratio=0.0,
                         require_training_floor=False)
    verdict = evaluate_exalt(
        _map(), _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2")),
        catalogue, StubPredictor(), cost_chaos=3.0, policy=policy,
    )
    assert verdict.orbs == 2
    assert verdict.cost_chaos == pytest.approx(6.0)


def test_the_cost_of_the_trade_is_charged_once_however_many_orbs():
    """Listing, waiting and meeting someone costs the same whatever you rolled."""
    policy = ExaltPolicy(effort_chaos=5.0, min_net_gain_fraction=0.0,
                         min_chance_of_profit=0.0, min_resolution_ratio=0.0,
                         require_training_floor=False)
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=1.0, policy=policy)
    assert verdict.cost_chaos == pytest.approx(verdict.orbs * 1.0 + 5.0)
    assert verdict.effort_chaos == pytest.approx(5.0)


def test_a_gain_below_the_orb_price_is_not_worth_it():
    """One low-value suffix slot against an absurd orb. Quantity is high enough
    to clear the training floor, because a map under it gets no verdict at all
    now and this test is about the arithmetic, not the floor."""
    verdict = _evaluate(
        _map(quantity=50),
        _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2"), ("of Power", "S1")),
        cost=500.0,
    )
    assert verdict.available
    assert not verdict.worth_it
    assert verdict.net_gain_chaos < 0


def test_a_gain_above_the_orb_price_is_worth_it():
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=5.0)
    assert verdict.worth_it
    assert verdict.net_gain_chaos > 0


# ---------------------------------------------------------------------------
def test_best_and_worst_outcomes_are_named():
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")))
    assert verdict.best.affix.name == "Fecund"
    assert verdict.worst.affix.name == "of Defiance"
    assert verdict.best.gain_chaos > verdict.worst.gain_chaos


def test_the_range_brackets_the_expectation():
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")))
    assert verdict.low_gain_chaos <= verdict.expected_gain_chaos <= verdict.high_gain_chaos


def test_chance_of_profit_is_reported():
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=25.0)
    # Only Fecund (+38c) beats a 25c orb; Diluted (+20c) and the suffixes do not.
    assert 0.0 < verdict.chance_of_profit < 1.0


def test_no_reward_value_is_invented_for_refused_features():
    """A catalogue that does not model a feature must leave it alone."""
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")))
    for outcome in verdict.outcomes:
        assert outcome.item.more_divination_cards == 0


def test_the_original_item_is_never_mutated():
    item = _map(quantity=50)
    _evaluate(item, _affixes(("of Power", "S1")))
    assert item.quantity == 50
    assert item.features() == {n: getattr(item, n) for n in FEATURE_NAMES}


# ---------------------------------------------------------------------------
# The bars, and the two refusals
# ---------------------------------------------------------------------------
class BandedPredictor(StubPredictor):
    """A predictor that admits how much it cannot pin down.

    The real one carries split-conformal bands; this reports a fixed upside
    ratio so the resolution gate can be aimed exactly.
    """

    def __init__(self, high: float = 2.0):
        self.high = high

    def interval_ratios(self, predicted_chaos):
        return 0.6, self.high


def test_a_map_below_the_training_floor_is_refused_rather_than_guessed_at():
    """Under 30c the model returns much the same number for every map, because
    those listings are held out of the fit. A difference of two of them is not
    a small gain, it is nothing at all."""
    verdict = _evaluate(
        _map(quantity=10),  # 2c per point -> 20c, under the floor
        _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2")),
        policy=ExaltPolicy(),
    )
    assert not verdict.available
    assert "floor" in verdict.reason
    assert verdict.current_chaos == pytest.approx(20.0)


def test_the_training_floor_refusal_is_not_the_same_answer_as_not_worth_it():
    """"We will not judge this" and "we judged it and the answer is no" are
    different answers and must not render as the same one."""
    refused = _evaluate(_map(quantity=10), _affixes(("Fecund", "P1")), policy=ExaltPolicy())
    assert refused.headline()[0] == "No verdict."
    assert refused.rejection == ""  # nothing was rejected; nothing was judged


def test_a_catalogue_that_cannot_move_a_modelled_feature_refuses_to_price_it():
    """An affix contributing nothing the model reads produces an identical map.
    That used to come back as a confident 'gain 0c, not worth it'."""
    inert = AffixCatalogue(
        entries=(_entry("Inert", "S1", "suffix", 0.0), _entry("AlsoInert", "S2", "suffix", 0.0)),
        modelled_features=("quantity",), fit_quality={}, sample_size=400,
    )
    verdict = evaluate_exalt(
        _map(), _affixes(("Fecund", "P1"), ("Diluted", "P1"), ("Savage", "P2")),
        inert, StubPredictor(), cost_chaos=1.0,
    )
    assert not verdict.available
    assert "no difference to price" in verdict.reason


def test_a_gain_too_thin_against_the_map_is_rejected_as_not_worth_handling():
    """The user's case: a 20c map, two orbs, and a 25c map at the end of it."""
    policy = ExaltPolicy(effort_chaos=0.0, min_chance_of_profit=0.0,
                         min_resolution_ratio=0.0, require_training_floor=False,
                         min_net_gain_fraction=0.08, charge_every_open_slot=False)
    verdict = _evaluate(_map(quantity=500), _affixes(("of Power", "S1")),
                        cost=1.0, policy=policy)
    # A 1000c map. One orb adds ~17c of quantity value, against an 80c bar:
    # a real gain, and still not worth handling the map twice for.
    assert verdict.required_gain_chaos == pytest.approx(80.0)
    assert verdict.net_gain_chaos > 0, "the old rule said yes to exactly this"
    assert not verdict.worth_it
    assert verdict.rejection == REJECT_THIN_MARGIN


def test_a_coin_flip_is_rejected_however_good_the_average_looks():
    policy = ExaltPolicy(effort_chaos=0.0, min_net_gain_fraction=0.0,
                         min_resolution_ratio=0.0, require_training_floor=False,
                         min_chance_of_profit=0.9, charge_every_open_slot=False)
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=10.0, policy=policy)
    assert verdict.net_gain_chaos > 0, "the average alone would have said yes"
    assert 0.0 < verdict.chance_of_profit < 0.9
    assert verdict.rejection == REJECT_LOW_CHANCE


def test_a_gain_the_model_cannot_tell_from_zero_is_rejected():
    """The gate that does the work. On the collected market 91 of 92 predicted
    gains were smaller than the model's own band on the very same map."""
    policy = ExaltPolicy(effort_chaos=0.0, min_net_gain_fraction=0.0,
                         min_chance_of_profit=0.0, require_training_floor=False,
                         charge_every_open_slot=False, min_resolution_ratio=1.0)
    # A 100c map whose band is +100c; no single affix here gains anything close.
    verdict = _evaluate(_map(quantity=50), _affixes(("of Power", "S1")), cost=1.0,
                        policy=policy, predictor=BandedPredictor(high=2.0))
    assert verdict.resolution_chaos == pytest.approx(100.0)
    assert verdict.net_gain_chaos < 100.0
    assert verdict.rejection == REJECT_INSIDE_NOISE
    assert not verdict.worth_it


def test_a_gain_that_clears_the_band_survives_the_noise_gate():
    policy = ExaltPolicy(effort_chaos=0.0, min_net_gain_fraction=0.0,
                         min_chance_of_profit=0.0, require_training_floor=False,
                         charge_every_open_slot=False, min_resolution_ratio=1.0)
    # A band of only 1% of the price: anything real clears it.
    verdict = _evaluate(_map(quantity=50), _affixes(("of Power", "S1")), cost=1.0,
                        policy=policy, predictor=BandedPredictor(high=1.01))
    assert verdict.worth_it
    assert verdict.rejection == ""


def test_a_predictor_with_no_band_simply_does_not_apply_the_gate():
    """A stub, or a model trained before conformal calibration existed. The
    gate has to be absent rather than silently rejecting everything."""
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=1.0,
                        policy=ExaltPolicy(require_training_floor=False))
    assert verdict.resolution_chaos is None
    assert verdict.rejection != REJECT_INSIDE_NOISE


def test_the_first_bar_it_fails_is_the_one_reported():
    """The page prints one sentence; it has to be the decisive one."""
    policy = ExaltPolicy(effort_chaos=1000.0, min_net_gain_fraction=0.9,
                         min_chance_of_profit=0.99, require_training_floor=False)
    verdict = _evaluate(_map(), _affixes(("of Power", "S1")), cost=1.0, policy=policy)
    assert verdict.rejection == REJECT_NEGATIVE_EV, "cost dwarfs everything else"


def test_the_same_map_always_gets_the_same_sampled_verdict():
    """A recommendation about spending currency that changes on a page refresh
    is worse than one that is slightly coarse."""
    affixes = _affixes(("Fecund", "P1"))
    first = _evaluate(_map(), affixes, policy=ExaltPolicy(require_training_floor=False))
    second = _evaluate(_map(), affixes, policy=ExaltPolicy(require_training_floor=False))
    assert first.sampled and second.sampled
    assert first.expected_gain_chaos == pytest.approx(second.expected_gain_chaos)
    assert first.worth_it == second.worth_it


def test_the_next_orb_table_stays_exact_even_when_the_whole_fill_is_sampled():
    """The expander is what you read to decide; it must not be a sample."""
    verdict = _evaluate(_map(), _affixes(("Fecund", "P1")),
                        policy=ExaltPolicy(require_training_floor=False))
    assert verdict.sampled
    assert sum(o.probability for o in verdict.outcomes) == pytest.approx(1.0)
    assert {o.affix.name for o in verdict.outcomes} == {
        "Diluted", "of Power", "of Defiance", "of Haste"
    }


def test_a_sampled_fill_never_puts_more_affixes_on_than_there_are_slots():
    verdict = _evaluate(_map(), _affixes(("Fecund", "P1"), ("Diluted", "P1")),
                        policy=ExaltPolicy(require_training_floor=False))
    # One prefix and three suffixes open, four candidates left: at most four,
    # and the quantity added cannot exceed the four richest contributions.
    assert verdict.orbs <= 4


def test_every_rejection_reason_produces_a_sentence_naming_its_own_bar():
    """A verdict that says no without saying why is a dead end."""
    cases = {
        REJECT_NEGATIVE_EV: dict(net_gain_chaos=-1.0, expected_gain_chaos=1.0),
        REJECT_THIN_MARGIN: dict(net_gain_chaos=1.0, expected_gain_chaos=8.0),
        REJECT_LOW_CHANCE: dict(net_gain_chaos=20.0, expected_gain_chaos=27.0,
                                chance_of_profit=0.1),
        REJECT_INSIDE_NOISE: dict(net_gain_chaos=20.0, expected_gain_chaos=27.0,
                                  chance_of_profit=0.9, resolution_chaos=100.0),
    }
    from src.pricing.exalt import ExaltVerdict

    for expected, fields in cases.items():
        verdict = ExaltVerdict(
            available=True, current_chaos=100.0, cost_chaos=7.5,
            orb_cost_chaos=1.25, effort_chaos=5.0, orbs=2,
            chance_of_profit=fields.pop("chance_of_profit", 1.0), **fields,
        )
        assert verdict.rejection == expected
        headline, why = verdict.headline()
        assert headline and why and headline != "No verdict."
        assert not verdict.worth_it
