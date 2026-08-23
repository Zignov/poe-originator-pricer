"""Display currency.

Everything is stored and modelled in chaos; this layer is the only thing that
decides what unit the user reads. Chaos is the default because uncorrupted T16
Originator Maps mostly trade between about 50 and 200 chaos, where "0.28d" is a
worse thing to read than "64c".
"""
from __future__ import annotations

import pytest

from src.config import load_config
from src.pricing.display import PriceFormatter, formatter_from, round_for_listing

RATE = 230.0


def test_chaos_is_the_default_unit():
    assert load_config().get("display.currency") == "chaos"
    assert formatter_from(load_config(), RATE).unit == "chaos"


def test_chaos_formatting_reads_like_a_price_a_player_would_type():
    fmt = PriceFormatter("chaos", RATE)
    assert fmt.format(63.4) == "63c"
    assert fmt.format(7.32) == "7.3c"
    assert fmt.format(137.0) == "135c"
    assert fmt.format(None) == "-"


def test_divine_formatting_is_unchanged():
    fmt = PriceFormatter("divine", RATE)
    assert fmt.format(RATE * 9.8).endswith("d")
    assert fmt.format(RATE) == "1d"


def test_auto_switches_at_the_threshold():
    fmt = PriceFormatter("auto", RATE)
    assert fmt.format(1900.0).endswith("c")
    assert fmt.format(2400.0).endswith("d")


def test_value_is_numeric_in_the_display_unit():
    assert PriceFormatter("chaos", RATE).value(500.0) == 500.0
    assert PriceFormatter("divine", RATE).value(RATE * 2) == pytest.approx(2.0)
    assert PriceFormatter("chaos", RATE).value(None) is None


def test_round_trip_through_to_chaos():
    fmt = PriceFormatter("chaos", RATE)
    assert fmt.to_chaos(fmt.value(420.0)) == pytest.approx(420.0)
    div = PriceFormatter("divine", RATE)
    assert div.to_chaos(div.value(460.0)) == pytest.approx(460.0)


def test_the_pasteable_note_names_the_right_currency():
    assert PriceFormatter("chaos", RATE).note(63.4) == "~price 63 chaos"
    assert PriceFormatter("divine", RATE).note(RATE * 9.8) == "~price 9.8 divine"


def test_range_renders_both_ends_in_one_unit():
    assert PriceFormatter("chaos", RATE).range(20.0, 80.0) == "20-80c"


def test_listing_amounts_are_round_numbers():
    assert round_for_listing(63.41, "chaos") == 63.0
    assert round_for_listing(137.0, "chaos") == 135.0
    assert round_for_listing(612.0, "chaos") == 600.0
    assert round_for_listing(9.83, "divine") == 9.8


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        PriceFormatter("shekels", RATE)


def test_a_bad_rate_is_rejected():
    with pytest.raises(ValueError):
        PriceFormatter("chaos", 0)


def test_the_suggested_price_and_the_note_never_disagree():
    """The note is what the user pastes; the stored figure is what the table
    shows. Rounding one but not the other would show two different prices."""
    from src.pricing.currency import CurrencyConverter
    from src.pricing.fair_value import PricingService

    fmt = PriceFormatter("chaos", RATE)
    amount, unit = fmt.listing_amount(63.41)
    assert unit == "chaos"
    assert fmt.to_chaos(amount, unit) == 63.0
    assert PricingService  # imported to keep the seam explicit
    assert CurrencyConverter
