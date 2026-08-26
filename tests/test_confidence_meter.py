"""The confidence meter: one definition of colour and length, three renderers.

The meter is drawn by the tkinter overlay, by the web grid's tiles and by the
detail panels, and its whole value is that a short bar means the same thing in
all three. That claim lives in `confidence_fill`, so it is tested here rather
than three times over in each renderer's tests.
"""
from __future__ import annotations

import pytest

from src.ui.palette import (
    CONFIDENCE_COLOURS,
    CONFIDENCE_LEVEL_FRACTION,
    MIN_CONFIDENCE_FILL,
    confidence_fill,
)


def test_the_length_is_the_score():
    assert confidence_fill("Medium", 0.42) == (CONFIDENCE_COLOURS["Medium"], 0.42)


@pytest.mark.parametrize("level", ["High", "Medium", "Low"])
def test_each_level_has_its_own_colour(level):
    colour, _ = confidence_fill(level, 0.5)
    assert colour == CONFIDENCE_COLOURS[level]
    # Three colours, not two: the meter has to be readable by colour as well as
    # by length, for a tab where every map happens to score about the same.
    assert len({CONFIDENCE_COLOURS[k] for k in ("High", "Medium", "Low")}) == 3


def test_an_unscored_map_gets_no_meter_at_all():
    """Not an empty bar. "Never scored" and "scored badly" are different claims."""
    assert confidence_fill("") is None
    assert confidence_fill("", 0.0) is None


def test_a_zero_score_still_draws_something():
    """Otherwise the worst possible map looks like one that was not priced."""
    _, fraction = confidence_fill("Low", 0.0)
    assert fraction == MIN_CONFIDENCE_FILL


def test_the_bar_never_overflows_its_track():
    _, fraction = confidence_fill("High", 1.4)
    assert fraction == 1.0


def test_a_label_without_a_score_falls_back_to_the_middle_of_its_band():
    """An overlay state file written before the score was carried still draws."""
    for level, expected in CONFIDENCE_LEVEL_FRACTION.items():
        assert confidence_fill(level) == (CONFIDENCE_COLOURS[level], expected)


def test_the_fallbacks_are_ordered_the_way_the_levels_are():
    high = CONFIDENCE_LEVEL_FRACTION["High"]
    medium = CONFIDENCE_LEVEL_FRACTION["Medium"]
    low = CONFIDENCE_LEVEL_FRACTION["Low"]
    assert low < medium < high <= 1.0


def test_an_unknown_level_is_drawn_rather_than_dropped():
    """A future level name should look uncertain, not vanish off the tile."""
    colour, fraction = confidence_fill("Unheard-of")
    assert colour == CONFIDENCE_COLOURS[""]
    assert 0.0 < fraction <= 1.0
