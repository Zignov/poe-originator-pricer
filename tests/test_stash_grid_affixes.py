"""Free affix slots drawn onto the stash grid.

One white dot per open slot, and the whole difficulty is in what *no* dots
means. It means two different things -- "this map is full" and "this payload
carries no affix structure" -- and only one of them is a statement about the
map. Both draw nothing, which is safe in this direction: neither invites an
Exalted Orb. The collapse that would cost someone currency is the other one,
drawing a confident zero for a map nobody has counted, and that is what
`free_affix_slots` exists to prevent.

The badge shares its geometry with the desktop overlay's, so the tile that has
two dots in the browser has two dots over the game. What is tested here is the
web half: the fragment, where it sits in the cell, and the tooltip that says
which kind of nothing an empty corner is.
"""
from __future__ import annotations

import pytest

from src.overlay.state import free_affix_slots
from src.parsing.affixes import Affix, AffixSet
from src.ui.stash_grid import FREE_AFFIX_MAX_DOTS, free_affix_badge


def _dot_count(html: str) -> int:
    return html.count("border-radius:50%")


def test_a_map_with_two_free_slots_gets_two_dots():
    assert _dot_count(free_affix_badge(2)) == 2


@pytest.mark.parametrize("free", range(1, FREE_AFFIX_MAX_DOTS + 1))
def test_one_dot_per_open_slot_all_the_way_up(free):
    assert _dot_count(free_affix_badge(free)) == free


def test_a_full_map_gets_no_dots():
    """Zero dots for zero slots -- and no empty badge element either."""
    assert free_affix_badge(0) == ""


def test_an_unreadable_affix_set_gets_no_dots_rather_than_zero():
    """`None` is "we could not read the affixes", which is not a claim about slots."""
    assert free_affix_badge(None) == ""


def test_the_badge_stops_at_six_rather_than_running_under_the_price():
    """A corrupted map can have eight slots and can take an orb in none of them."""
    assert _dot_count(free_affix_badge(8)) == FREE_AFFIX_MAX_DOTS


def test_the_badge_sits_in_the_top_right_corner_clear_of_the_other_two_marks():
    """The ring owns the border, the meter the bottom edge, the price the middle.

    4px in on both sides, the same clearance the confidence meter uses, which is
    what keeps the badge off the 2px exalt outline drawn 2px in.
    """
    html = free_affix_badge(3)
    assert "position:absolute" in html
    assert "top:4px" in html and "right:4px" in html
    assert "bottom" not in html and "left:" not in html


def test_the_badge_is_the_same_white_on_every_value_band():
    """It is structure, not a value judgement: two slots is two slots on any tile."""
    assert "background:#fff" in free_affix_badge(1)


def test_the_badge_never_eats_a_hover_meant_for_the_tile():
    """The tile's own tooltip is where the rest of the map's numbers live."""
    assert "pointer-events:none" in free_affix_badge(4)


def test_the_badge_carries_the_class_the_grid_is_read_by():
    assert 'class="free-affix"' in free_affix_badge(2)


# -- the count itself, read the way the grid reads it ------------------------
def _affix_set(prefixes: int, suffixes: int, known: bool = True) -> AffixSet:
    return AffixSet(
        affixes=tuple(
            [Affix(name=f"P{i}", tier=f"P{i}", slot="prefix") for i in range(prefixes)]
            + [Affix(name=f"S{i}", tier=f"S{i}", slot="suffix") for i in range(suffixes)]
        ),
        known=known,
    )


def test_the_grid_and_the_overlay_count_the_same_slots():
    """One definition, two renderers -- the same rule the tile price follows."""
    affixes = _affix_set(2, 1)
    assert free_affix_slots(affixes) == 3
    assert _dot_count(free_affix_badge(free_affix_slots(affixes))) == 3


def test_a_map_the_catalogue_could_not_read_draws_nothing_through_the_whole_path():
    assert free_affix_badge(free_affix_slots(AffixSet(known=False))) == ""
