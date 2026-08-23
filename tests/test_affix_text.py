"""Recovering affixes from modifier text, the way a stash payload arrives.

GGG's stash endpoints return each modifier as a description and nothing else.
Verified against a live account: 321 modifier lines across 45 rare maps, none
carrying an affix name or tier. So the structured reader cannot be used there,
and the text has to be resolved against what the market taught us.

The hard part is that one affix can print several lines, so counting lines
over-counts. The map's own Item Quantity settles it: quantity is exactly the sum
of per-affix contributions, so two lines being one affix or two implies
different totals, and the reported total picks the right reading.
"""
from __future__ import annotations

import pytest

from src.parsing.affix_text import normalise, resolve_affixes

# "Hastening" prints three lines; the rest print one each.
TEMPLATES = {
    "#% increased Monster Movement Speed": ["Hastening"],
    "#% increased Monster Attack Speed": ["Hastening"],
    "#% increased Monster Cast Speed": ["Hastening"],
    "#% more Monster Life": ["Fecund"],
    "Monsters have +# to Maximum Power Charges": ["of Power"],
    "Monsters gain a Power Charge on Hit": ["of Power"],
    "Players are Cursed with Vulnerability": ["of Curses", "of Vulnerability"],
}
AFFIX_TEMPLATES = {
    "Hastening": [
        "#% increased Monster Movement Speed",
        "#% increased Monster Attack Speed",
        "#% increased Monster Cast Speed",
    ],
    "Fecund": ["#% more Monster Life"],
    "of Power": [
        "Monsters have +# to Maximum Power Charges",
        "Monsters gain a Power Charge on Hit",
    ],
    "of Curses": ["Players are Cursed with Vulnerability"],
    "of Vulnerability": ["Players are Cursed with Vulnerability"],
}
SLOTS = {
    "Hastening": "prefix",
    "Fecund": "prefix",
    "of Power": "suffix",
    "of Curses": "suffix",
    "of Vulnerability": "suffix",
}
QUANTITIES = {
    "Hastening": 12.0,
    "Fecund": 19.0,
    "of Power": 8.0,
    "of Curses": 5.0,
    "of Vulnerability": 5.0,
}


def _resolve(lines, quantity=None, corrupted=False):
    return resolve_affixes(
        lines, quantity, TEMPLATES, AFFIX_TEMPLATES, SLOTS, QUANTITIES, corrupted
    )


def test_rolled_numbers_are_stripped_before_lookup():
    assert normalise("26% increased Monster Movement Speed") == (
        "#% increased Monster Movement Speed"
    )
    assert normalise("Players have -20% to Suppression") == (
        "Players have #% to Suppression"
    ), "the sign is part of the rolled number"


def test_three_lines_from_one_affix_count_as_one():
    """The real case: counting lines would say three affixes, not one."""
    result = _resolve(
        [
            "26% increased Monster Movement Speed",
            "36% increased Monster Attack Speed",
            "38% increased Monster Cast Speed",
        ],
        quantity=12.0,
    )
    assert result.known
    assert result.count == 1
    assert result.prefix_count == 1


def test_separate_affixes_are_not_merged():
    result = _resolve(["92% more Monster Life", "Monsters gain a Power Charge on Hit"], 27.0)
    assert result.count == 2
    assert result.prefix_count == 1
    assert result.suffix_count == 1


def test_quantity_decides_between_one_affix_and_two():
    """Two lines that one affix could print, or two affixes could.

    Both readings explain the text. Only the reported quantity separates them,
    which is the whole reason the totals are consulted.
    """
    lines = [
        "Monsters have +1 to Maximum Power Charges",
        "Monsters gain a Power Charge on Hit",
    ]
    assert _resolve(lines, quantity=8.0).count == 1, "one 'of Power' contributes 8"
    assert _resolve(lines, quantity=16.0).count == 2, "two would contribute 16"


def test_the_count_is_still_returned_without_a_quantity():
    result = _resolve(["92% more Monster Life"], quantity=None)
    assert result.known
    assert result.count == 1


def test_a_modifier_never_seen_before_is_unknown_not_guessed():
    result = _resolve(["Monsters ride invisible bicycles"], 10.0)
    assert not result.known
    assert "not seen in the collected market" in result.reason


def test_an_empty_modifier_list_is_unknown():
    assert not _resolve([], 10.0).known


# ---------------------------------------------------------------------------
def test_the_prefix_suffix_cap_resolves_an_ambiguous_line():
    """A line either kind could print is decided by the three-of-each limit."""
    lines = [
        "92% more Monster Life",
        "26% increased Monster Movement Speed",
        "Players are Cursed with Vulnerability",
    ]
    result = _resolve(lines, quantity=36.0)
    assert result.known
    assert result.count == 3
    assert result.prefix_count <= 3
    assert result.suffix_count <= 3


def test_a_reading_needing_four_prefixes_is_refused_when_uncorrupted():
    names = ["alpha", "beta", "gamma", "delta"]
    templates = {f"line {n}": [n] for n in names}
    affix_templates = {n: [f"line {n}"] for n in names}
    slots = {n: "prefix" for n in names}
    quantities = {n: 1.0 for n in names}
    result = resolve_affixes(
        [f"line {n}" for n in names], 4.0, templates, affix_templates, slots, quantities
    )
    assert not result.known
    assert "prefix/suffix split" in result.reason


def test_corruption_allows_the_fourth_prefix():
    names = ["alpha", "beta", "gamma", "delta"]
    templates = {f"line {n}": [n] for n in names}
    affix_templates = {n: [f"line {n}"] for n in names}
    slots = {n: "prefix" for n in names}
    quantities = {n: 1.0 for n in names}
    result = resolve_affixes(
        [f"line {n}" for n in names],
        4.0,
        templates,
        affix_templates,
        slots,
        quantities,
        corrupted=True,
    )
    assert result.known
    assert result.prefix_count == 4
    assert not result.can_be_exalted, "corrupted maps still cannot be exalted"


@pytest.mark.parametrize("quantity", [None, 0.0, -5.0])
def test_an_odd_quantity_does_not_crash_the_reader(quantity):
    result = _resolve(["92% more Monster Life"], quantity)
    assert result.known or result.reason
