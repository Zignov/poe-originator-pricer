"""Affix structure extraction.

A map's affix count is not its number of modifier lines. One affix can print two
lines -- "of Endurance" grants both a maximum-charge line and a gain-on-hit line
-- so counting lines reports 7 affixes on a map that has 6 and is actually full.
Every case here comes from real 3.29 trade-API payloads.
"""
from __future__ import annotations

import pytest

from src.parsing.affixes import MAX_PREFIXES, MAX_SUFFIXES, extract_affixes


def _mod(description: str, name: str, tier: str) -> dict:
    return {
        "description": description,
        "domain": "explicit",
        "mods": [{"name": name, "tier": tier, "magnitudes": [{"min": "1", "max": "1"}]}],
    }


def _item(*mods: dict, corrupted: bool = False) -> dict:
    """An item object shaped like the trade and stash APIs return."""
    return {
        "explicitMods": list(mods),
        "corrupted": corrupted,
        "extended": {"hashes": {"explicit": [[f"explicit.stat_{i}", [i]] for i in range(len(mods))]}},
    }


FULL_SIX = _item(
    _mod("Monsters deal 97% extra Physical Damage as Cold", "Freezing", "P0"),
    _mod("Monsters have +50% Chance to Block Attack Damage", "Stalwart", "P1"),
    _mod("Rare Monsters have Volatile Cores", "Volatile", "P1"),
    _mod("Area has patches of Chilled Ground", "of Ice", "S0"),
    _mod("Monsters have +1 to Maximum Endurance Charges", "of Endurance", "S1"),
    _mod("Debuffs on Monsters expire 100% faster", "of Defiance", "S1"),
)


def test_a_full_map_reports_six_affixes():
    result = extract_affixes(FULL_SIX)
    assert result.known
    assert result.count == 6
    assert result.is_full


def test_one_affix_printing_two_lines_is_counted_once():
    """The real case: seven mod lines, six affixes, map is full."""
    seven_lines = _item(
        _mod("Monsters deal 97% extra Physical Damage as Cold", "Freezing", "P0"),
        _mod("Monsters have +50% Chance to Block Attack Damage", "Stalwart", "P1"),
        _mod("Rare Monsters have Volatile Cores", "Volatile", "P1"),
        _mod("Area has patches of Chilled Ground", "of Ice", "S0"),
        _mod("Monsters have +1 to Maximum Endurance Charges", "of Endurance", "S1"),
        _mod("Monsters gain an Endurance Charge when hit", "of Endurance", "S1"),
        _mod("Debuffs on Monsters expire 100% faster", "of Defiance", "S1"),
    )
    assert len(seven_lines["explicitMods"]) == 7
    result = extract_affixes(seven_lines)
    assert result.count == 6
    assert result.is_full


def test_prefixes_and_suffixes_are_split_by_tier_letter():
    result = extract_affixes(FULL_SIX)
    assert result.prefix_count == 3
    assert result.suffix_count == 3
    assert {a.name for a in result.prefixes} == {"Freezing", "Stalwart", "Volatile"}
    assert {a.name for a in result.suffixes} == {"of Ice", "of Endurance", "of Defiance"}


def test_open_slots_are_reported_per_kind():
    partial = _item(
        _mod("Monsters deal 97% extra Physical Damage as Cold", "Freezing", "P0"),
        _mod("Area has patches of Chilled Ground", "of Ice", "S0"),
        _mod("Debuffs on Monsters expire 100% faster", "of Defiance", "S1"),
    )
    result = extract_affixes(partial)
    assert result.count == 3
    assert not result.is_full
    assert result.open_prefixes == MAX_PREFIXES - 1
    assert result.open_suffixes == MAX_SUFFIXES - 2


def test_an_item_with_no_structured_block_is_unknown_not_zero():
    """Clipboard-derived items carry mod text but no tiers. Never guess from it."""
    result = extract_affixes({"explicitMods": ["23% increased Monster Damage"]})
    assert not result.known
    assert result.count is None


def test_an_item_with_no_mods_at_all_is_unknown():
    assert not extract_affixes({}).known


def test_an_impossible_split_is_reported_as_unknown():
    """Better to show nothing than to confidently show a wrong affix count."""
    impossible = _item(
        _mod("a", "One", "P0"),
        _mod("b", "Two", "P1"),
        _mod("c", "Three", "P2"),
        _mod("d", "Four", "P3"),
    )
    result = extract_affixes(impossible)
    assert not result.known
    assert result.count is None


def test_a_corrupted_map_is_flagged_as_unmodifiable():
    result = extract_affixes(_item(_mod("a", "One", "P0"), corrupted=True))
    assert result.known
    assert result.corrupted
    assert not result.can_be_exalted


def test_a_full_uncorrupted_map_cannot_be_exalted_either():
    assert not extract_affixes(FULL_SIX).can_be_exalted


def test_an_uncorrupted_map_with_an_open_slot_can_be_exalted():
    result = extract_affixes(_item(_mod("a", "One", "P0")))
    assert result.can_be_exalted


def test_affixes_are_identified_by_name_and_tier_together():
    """Two tiers of the same named affix are different affixes to the catalogue."""
    result = extract_affixes(
        _item(_mod("a", "Savage", "P0"), _mod("b", "Savage", "P1"))
    )
    assert result.count == 2
    assert {(a.name, a.tier) for a in result.affixes} == {("Savage", "P0"), ("Savage", "P1")}


@pytest.mark.parametrize("tier", ["", None, "X9"])
def test_a_mod_with_no_recognisable_slot_makes_the_result_unknown(tier):
    result = extract_affixes(_item(_mod("a", "Mystery", tier)))
    assert not result.known


# ---------------------------------------------------------------------------
# Corruption raises the ceiling. A Vaal Orb can push a rare map past the normal
# three-and-three, and corrupted 8-mod Originator Maps are the high-value end of
# this market -- so treating 4+4 as impossible would blank the affix count on
# exactly the maps worth the most. Taken from a real payload in data/demo_stash.json.
# ---------------------------------------------------------------------------
CORRUPTED_EIGHT = _item(
    _mod("92% more Monster Life", "Fecund", "P1"),
    _mod("Monsters gain Extra Energy Shield", "Buffered", "P0"),
    _mod("Extra Chaos Damage", "Profane", "P1"),
    _mod("Players have less Flask effect", "Diluted", "P1"),
    _mod("Monsters have +1 Maximum Frenzy Charges", "of Frenzy", "S1"),
    _mod("Monsters have +1 Maximum Power Charges", "of Power", "S1"),
    _mod("Reduced Leech recovery", "of Congealment", "S1"),
    _mod("Players have less Area of Effect", "of Impotence", "S0"),
    corrupted=True,
)


def test_a_corrupted_eight_mod_map_is_read_not_refused():
    result = extract_affixes(CORRUPTED_EIGHT)
    assert result.known, result.reason
    assert result.count == 8
    assert result.prefix_count == 4
    assert result.suffix_count == 4


def test_a_corrupted_map_reports_its_own_ceiling():
    result = extract_affixes(CORRUPTED_EIGHT)
    assert result.max_affixes == 8
    assert result.label() == "8/8 affixes"
    assert result.is_full


def test_an_uncorrupted_map_keeps_the_three_and_three_ceiling():
    result = extract_affixes(FULL_SIX)
    assert result.max_affixes == 6
    assert result.label() == "6/6 affixes"


def test_four_prefixes_on_an_uncorrupted_map_is_still_impossible():
    impossible = _item(
        _mod("a", "One", "P0"), _mod("b", "Two", "P1"),
        _mod("c", "Three", "P2"), _mod("d", "Four", "P3"),
    )
    assert not extract_affixes(impossible).known


def test_five_prefixes_is_impossible_even_when_corrupted():
    impossible = _item(
        _mod("a", "One", "P0"), _mod("b", "Two", "P1"), _mod("c", "Three", "P2"),
        _mod("d", "Four", "P3"), _mod("e", "Five", "P4"),
        corrupted=True,
    )
    assert not extract_affixes(impossible).known


def test_a_corrupted_map_with_a_free_slot_still_cannot_be_exalted():
    """Room is not the constraint -- corruption is."""
    result = extract_affixes(_item(_mod("a", "One", "P0"), corrupted=True))
    assert result.known
    assert result.open_slots > 0
    assert not result.can_be_exalted
