"""The exalt-worthy judgement.

Every affix in the catalogue contributes positive quantity, so exalting a map
never destroys reward value. The question is only whether the expected gain
clears the price of the orb -- and how wide the spread of outcomes is, because
an average that hides a coin flip is not a recommendation.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.ml.affix_catalogue import AffixCatalogue, AffixEntry
from src.parsing.affixes import extract_affixes
from src.pricing.exalt import evaluate_exalt
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


def _evaluate(item, affixes, cost=10.0):
    return evaluate_exalt(item, affixes, CATALOGUE, StubPredictor(), cost_chaos=cost)


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


def test_a_gain_below_the_orb_price_is_not_worth_it():
    """Cheap map, one low-value suffix slot, expensive orb."""
    verdict = _evaluate(
        _map(quantity=10),
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
