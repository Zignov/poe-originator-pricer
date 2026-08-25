"""Above ~200c the market has four prices, not a continuum.

Measured on 4,102 live listings: above 200 chaos, 71% are priced in divine and
**100% of those are a whole number of divine** -- 1d (260 listings), 2d (139),
3d (67), 4d (12). Below 200c it is 100% chaos, clustered on 10/20/30/40/50/100.

So a suggested price of "just under 340c" is not a more precise version of
"2 divine" -- it is a price nobody types, sitting in a gap the market does not
use. These tests pin listing_amount() and note() to what a seller would actually
put in the box, which is what both already promise in their docstrings.
"""
from __future__ import annotations

import pytest

from src.pricing.display import DIVINE_QUANTISED_ABOVE_CHAOS, PriceFormatter

DIV = 200.0  # a round rate keeps the arithmetic in these tests readable


def fmt(unit="chaos"):
    return PriceFormatter(unit=unit, divine_to_chaos=DIV)


def test_a_price_in_the_divine_band_is_quoted_in_whole_divine():
    amount, currency = fmt().listing_amount(430.0)  # 2.15 divine
    assert (amount, currency) == (2.0, "divine")


def test_it_rounds_to_the_nearest_divine_rather_than_down():
    amount, _ = fmt().listing_amount(580.0)  # 2.9 divine
    assert amount == 3.0


def test_the_note_pasted_into_the_game_says_divine_too():
    assert fmt().note(430.0) == "~price 2 divine"


def test_below_the_band_nothing_changes():
    """The cheap end genuinely trades in chaos, on its own round numbers."""
    amount, currency = fmt().listing_amount(85.0)
    assert currency == "chaos"
    assert amount == 85.0


def test_the_band_starts_where_the_market_does():
    assert DIVINE_QUANTISED_ABOVE_CHAOS == 200.0


def test_a_price_just_over_the_line_is_never_rounded_to_zero_divine():
    """210c is one divine, not 'free'. Rounding 1.05 to the nearest whole is
    right; rounding 0.6 of a divine to 0 would not be."""
    amount, currency = fmt().listing_amount(210.0)
    assert amount == 1.0 and currency == "divine"


def test_the_displayed_table_number_is_left_alone():
    """format() feeds sortable columns and charts. Quantising there would make
    every expensive map show the same handful of values and destroy the
    ordering the table exists for -- it is only the *listing* price that snaps.
    """
    assert fmt().format(430.0) == "430c"


def test_it_survives_a_divine_display_unit():
    amount, currency = fmt(unit="divine").listing_amount(430.0)
    assert (amount, currency) == (2.0, "divine")


def test_round_trip_stays_inside_the_band():
    """Whatever it suggests must convert back to a price in the same band, or
    the app would suggest something it would then refuse to call sellable."""
    f = fmt()
    amount, currency = f.listing_amount(430.0)
    assert f.to_chaos(amount, currency) >= DIVINE_QUANTISED_ABOVE_CHAOS


def test_it_stops_where_the_evidence_stops():
    """Every whole-divine ask in the sample was 1-4 divine. Above that there is
    no sample -- that is corrupted-map territory, outside this model's scope --
    so 9.8 divine stays 9.8 rather than being rounded on a pattern nobody
    measured."""
    amount, currency = fmt(unit="divine").listing_amount(DIV * 9.8)
    assert currency == "divine"
    assert amount == pytest.approx(9.8)
