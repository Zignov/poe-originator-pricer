"""Exalt state drawn onto the stash grid.

Three states, and only three: worth the orb, not worth it, and nothing at all for
a map that cannot take one. A map that is full, corrupted, or whose affixes could
not be read must be visually indistinguishable from a normal tile -- an outline
there would be an invitation to spend currency on something that cannot accept it.
"""
from __future__ import annotations

import pytest

from src.pricing.exalt import ExaltVerdict
from src.ui.stash_grid import EXALT_NO_CLASS, EXALT_YES_CLASS, exalt_class


def _verdict(available: bool, net: float | None = None) -> ExaltVerdict:
    return ExaltVerdict(available=available, net_gain_chaos=net)


def test_a_profitable_exalt_gets_the_solid_ring():
    assert exalt_class(_verdict(True, net=25.0)) == EXALT_YES_CLASS


def test_an_unprofitable_exalt_gets_the_dashed_ring():
    assert exalt_class(_verdict(True, net=-3.0)) == EXALT_NO_CLASS


def test_breaking_exactly_even_is_not_worth_it():
    assert exalt_class(_verdict(True, net=0.0)) == EXALT_NO_CLASS


def test_a_map_that_cannot_be_exalted_gets_no_ring():
    """Full, corrupted, or unreadable -- all indistinguishable from a plain tile."""
    assert exalt_class(_verdict(False)) == ""


def test_a_missing_verdict_gets_no_ring():
    assert exalt_class(None) == ""


@pytest.mark.parametrize("net", [0.01, 1.0, 1000.0])
def test_any_positive_net_counts_as_worth_it(net):
    assert exalt_class(_verdict(True, net=net)) == EXALT_YES_CLASS
