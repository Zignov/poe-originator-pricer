"""Price parsing and currency normalization."""
from __future__ import annotations

import pytest

from src.parsing.price_parser import (
    format_price_note,
    normalize_currency,
    parse_api_price,
    parse_price_note,
)
from src.pricing.currency import CurrencyConverter


def test_parses_a_price_note():
    assert parse_price_note("~price 9.8 divine") == {
        "price_type": "~price",
        "price_amount": 9.8,
        "price_currency": "divine",
    }


def test_parses_a_buyout_note():
    parsed = parse_price_note("~b/o 250 chaos")
    assert parsed["price_type"] == "~b/o"
    assert parsed["price_amount"] == 250.0
    assert parsed["price_currency"] == "chaos"


def test_parses_a_shorthand_currency():
    assert parse_price_note("~price 3 div")["price_currency"] == "divine"
    assert parse_price_note("~b/o 12 c")["price_currency"] == "chaos"


def test_ignores_a_tab_name_that_is_not_a_price():
    assert parse_price_note("Originator Maps") is None
    assert parse_price_note(None) is None


def test_parses_an_api_price_object():
    parsed = parse_api_price({"price": {"type": "~b/o", "amount": 2, "currency": "mirror"}})
    assert parsed == {"price_type": "~b/o", "price_amount": 2.0, "price_currency": "mirror"}


def test_api_price_handles_an_unpriced_listing():
    assert parse_api_price({})["price_amount"] is None


def test_currency_aliases():
    assert normalize_currency("Divine Orb") == "divine"
    assert normalize_currency("chaos") == "chaos"
    assert normalize_currency("EX") == "exalted"


# ---------------------------------------------------------------------------
def test_divine_to_chaos_conversion():
    converter = CurrencyConverter(220.0)
    assert converter.to_chaos(9.8, "divine") == pytest.approx(2156.0)
    assert converter.to_chaos(150, "chaos") == 150.0


def test_chaos_back_to_divine_round_trips():
    converter = CurrencyConverter(214.5)
    chaos = converter.to_chaos(9.8, "divine")
    assert converter.chaos_to_divine(chaos) == pytest.approx(9.8)


def test_rate_is_not_hardcoded():
    assert CurrencyConverter(100.0).to_chaos(1, "divine") == 100.0
    assert CurrencyConverter(300.0).to_chaos(1, "divine") == 300.0


def test_mirror_converts_via_divine_so_outliers_are_numerically_comparable():
    converter = CurrencyConverter(200.0)
    assert converter.to_chaos(1, "mirror") > converter.to_chaos(100, "divine")


def test_unknown_currency_returns_none_rather_than_guessing():
    assert CurrencyConverter(220.0).to_chaos(5, "hypothetical-orb") is None


def test_zero_or_negative_rate_is_rejected():
    with pytest.raises(ValueError):
        CurrencyConverter(0)


def test_note_formatting_matches_the_game_format():
    assert format_price_note(9.8) == "~price 9.8 divine"
    assert format_price_note(120.4) == "~price 120 divine"
    assert format_price_note(0.5) == "~price 0.5 divine"
