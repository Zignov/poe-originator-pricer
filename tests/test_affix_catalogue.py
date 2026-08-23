"""Fitting the per-affix contribution table.

The catalogue exists because map reward properties are *sums* of what each rolled
affix contributes -- verified out-of-sample on real data, where quantity predicts
at R2 0.999 with a mean absolute error of 0.03. That makes an exalt simulation
exact arithmetic rather than a guess.

Not every feature behaves that way, and the gate here is the part that matters:
more_divination_cards scored a *negative* held-out R2 on the real dataset, so it
is a memory reward rather than an affix roll. A catalogue that modelled it would
invent value out of nothing on every exalt estimate.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.ml.affix_catalogue import (
    ADDITIVITY_R2_GATE,
    AffixCatalogue,
    fit_catalogue,
)
from src.parsing.affixes import AffixSet, extract_affixes

# (name, tier, quantity contribution)
POOL = [
    ("Freezing", "P0", 10.0),
    ("Stalwart", "P1", 6.0),
    ("Volatile", "P2", 4.0),
    ("of Ice", "S0", 12.0),
    ("of Endurance", "S1", 8.0),
    ("of Defiance", "S2", 5.0),
]


def _mod(name: str, tier: str) -> dict:
    return {"description": f"{name} does something", "mods": [{"name": name, "tier": tier}]}


def _affix_set(indices) -> AffixSet:
    return extract_affixes({"explicitMods": [_mod(POOL[i][0], POOL[i][1]) for i in indices]})


def _sample(seed: int = 0, n: int = 400):
    """Listings whose quantity is exactly additive and whose rarity is noise."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        size = int(rng.integers(3, 7))
        picks = sorted(rng.choice(len(POOL), size=size, replace=False).tolist())
        affixes = _affix_set(picks)
        out.append(
            (
                affixes,
                {
                    "quantity": sum(POOL[i][2] for i in picks),
                    "rarity": float(rng.integers(0, 200)),  # unrelated to affixes
                    "pack_size": 0.0,
                    "more_currency": 0.0,
                    "more_maps": 0.0,
                    "more_scarabs": 0.0,
                    "more_divination_cards": 0.0,
                },
            )
        )
    return out


@pytest.fixture
def catalogue() -> AffixCatalogue:
    return fit_catalogue(_sample(), league="Allflame", map_tier=16)


def test_an_additive_feature_passes_the_gate(catalogue):
    assert "quantity" in catalogue.modelled_features
    assert catalogue.fit_quality["quantity"]["r2"] >= ADDITIVITY_R2_GATE


def test_a_feature_unrelated_to_affixes_is_refused(catalogue):
    """The more_divination_cards case, reproduced deterministically."""
    assert "rarity" not in catalogue.modelled_features
    assert catalogue.fit_quality["rarity"]["r2"] < ADDITIVITY_R2_GATE


def test_recovered_contributions_match_the_truth(catalogue):
    by_key = {e.key: e for e in catalogue.entries}
    for name, tier, truth in POOL:
        assert by_key[(name, tier)].contributions["quantity"] == pytest.approx(truth, abs=0.05)


def test_every_affix_in_the_sample_is_catalogued(catalogue):
    assert {e.key for e in catalogue.entries} == {(n, t) for n, t, _ in POOL}


def test_frequency_is_recorded_so_candidates_can_be_weighted(catalogue):
    assert all(e.frequency > 0 for e in catalogue.entries)
    assert sum(e.frequency for e in catalogue.entries) > 0


def test_applying_an_affix_only_touches_modelled_features(catalogue):
    entry = next(e for e in catalogue.entries if e.key == ("Freezing", "P0"))
    before = {name: 50.0 for name in catalogue.fit_quality}
    after = catalogue.apply(before, entry)
    assert after["quantity"] == pytest.approx(60.0)
    assert after["rarity"] == 50.0, "a refused feature must pass through untouched"
    assert before["quantity"] == 50.0, "the input must not be mutated"


# ---------------------------------------------------------------------------
def test_candidates_exclude_affixes_already_on_the_map(catalogue):
    present = _affix_set([0, 3])
    keys = {e.key for e in catalogue.candidates(present)}
    assert ("Freezing", "P0") not in keys
    assert ("of Ice", "S0") not in keys
    assert ("Stalwart", "P1") in keys


def test_candidates_respect_a_full_prefix_block():
    """Three prefixes rolled means only suffixes can come out of an exalt."""
    cat = fit_catalogue(_sample(), league="Allflame", map_tier=16)
    present = _affix_set([0, 1, 2])  # all three prefixes
    slots = {e.slot for e in cat.candidates(present)}
    assert slots == {"suffix"}


def test_a_full_map_has_no_candidates(catalogue):
    assert catalogue.candidates(_affix_set([0, 1, 2, 3, 4, 5])) == ()


def test_an_unknown_affix_set_has_no_candidates(catalogue):
    assert catalogue.candidates(extract_affixes({})) == ()


# ---------------------------------------------------------------------------
def test_a_catalogue_survives_a_save_and_load(tmp_path, catalogue):
    path = tmp_path / "affix_catalogue.json"
    catalogue.save(path)
    back = AffixCatalogue.load(path)
    assert back.modelled_features == catalogue.modelled_features
    assert {e.key for e in back.entries} == {e.key for e in catalogue.entries}
    assert back.sample_size == catalogue.sample_size
    assert back.league == "Allflame"


def test_loading_a_missing_catalogue_returns_none(tmp_path):
    assert AffixCatalogue.load(tmp_path / "nope.json") is None


def test_too_few_listings_produces_no_catalogue():
    assert fit_catalogue(_sample(n=5), league="Allflame", map_tier=16) is None
