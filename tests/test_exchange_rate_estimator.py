"""How a chaos/divine rate is derived from one hour of the Currency Exchange.

This was the midpoint of `lowest_ratio` and `highest_ratio`, which is a
reasonable-looking estimator and a bad one: those two numbers are the single
best and single worst trade of the hour, so one person executing one silly
order moves it as much as all the real business combined.

Measured on the live Covens chaos/divine market, 2026-11-22:

    hour        midpoint   volume-weighted
    08:00          206.0             209.1
    07:00          125.0             211.0     <- someone traded at 33:1
    06:00          164.0             211.2     <- someone traded at 111:1
    05:00          209.5             213.0
    04:00          208.5             212.8
    03:00          207.5             212.0

The real market sat flat at 209-213 all morning. A rate fetched during the
07:00 hour stored **125**, which then scaled every divine-denominated price in
the app by 0.6 until someone noticed -- and the only way to notice was to know
what a divine costs.
"""
from __future__ import annotations

import pytest

from src.market.currency_exchange import CHAOS, ITEM_IDS, parse_markets

DIVINE = ITEM_IDS["divine"]


def _market(low, high, chaos_volume, divine_volume, league="Covens"):
    return {
        "league": league,
        "market_pair": [CHAOS, DIVINE],
        "lowest_ratio": {CHAOS: low, DIVINE: 1},
        "highest_ratio": {CHAOS: high, DIVINE: 1},
        "volume_traded": {CHAOS: chaos_volume, DIVINE: divine_volume},
    }


def _rate(market):
    quotes = parse_markets([market], "Covens")
    assert "divine" in quotes, "no divine quote came out of the market"
    return quotes["divine"].chaos


# -- the bug ----------------------------------------------------------------
def test_one_outlier_trade_does_not_move_the_rate():
    """The 07:00 hour, verbatim. The midpoint said 125; the market said 211."""
    rate = _rate(_market(low=33, high=217, chaos_volume=14_300_000, divine_volume=67_800))
    assert rate == pytest.approx(211.0, abs=1.0)
    assert rate > 200, "an outlier low ratio dragged the rate down again"


@pytest.mark.parametrize(
    "low,high,chaos_volume,divine_volume,expected",
    [
        (200, 212, 14_177_697, 67_813, 209.1),
        (33, 217, 14_300_000, 67_800, 211.0),
        (111, 217, 14_250_000, 67_500, 211.1),
        (200, 219, 14_200_000, 66_700, 212.9),
        (199, 218, 14_180_000, 66_650, 212.8),
        (200, 215, 14_100_000, 66_500, 212.0),
    ],
)
def test_every_measured_hour_lands_in_the_real_range(
    low, high, chaos_volume, divine_volume, expected
):
    """Six consecutive live hours, including the two that broke the old one."""
    rate = _rate(_market(low, high, chaos_volume, divine_volume))
    assert rate == pytest.approx(expected, abs=1.5)
    assert 195 <= rate <= 225, f"{rate} is outside what the market actually did"


def test_the_rate_is_volume_weighted_not_a_midpoint():
    """Stated directly, so the intent survives a refactor.

    Bounds that would midpoint to 1000 but whose volume says 200 must give 200.
    """
    rate = _rate(_market(low=1, high=1999, chaos_volume=2_000_000, divine_volume=10_000))
    assert rate == pytest.approx(200.0)


# -- the fallback -----------------------------------------------------------
def test_a_market_with_no_volume_still_produces_a_rate():
    """Fragile is better than absent for a market that genuinely traded."""
    rate = _rate(_market(low=200, high=212, chaos_volume=0, divine_volume=0))
    assert rate == pytest.approx(206.0)


@pytest.mark.parametrize(
    "volume",
    [
        {CHAOS: None, DIVINE: 100},
        {CHAOS: 100, DIVINE: None},
        {CHAOS: "lots", DIVINE: 100},
        {CHAOS: 100, DIVINE: 0},
        {},
    ],
)
def test_unusable_volume_falls_back_rather_than_crashing(volume):
    market = _market(200, 212, 0, 0)
    market["volume_traded"] = volume
    assert _rate(market) == pytest.approx(206.0)


def test_a_market_with_neither_volume_nor_bounds_is_skipped():
    """Skipped, not defaulted. A fabricated rate is worse than a missing one."""
    market = _market(0, 0, 0, 0)
    assert "divine" not in parse_markets([market], "Covens")


# -- the quote keeps its evidence -------------------------------------------
def test_the_quote_reports_the_volume_it_was_derived_from():
    """So a caller can tell a deep hour from one trade in a thin market."""
    quotes = parse_markets([_market(200, 212, 14_177_697, 67_813)], "Covens")
    assert quotes["divine"].volume == pytest.approx(14_177_697)
    assert quotes["divine"].source


def test_another_leagues_market_is_not_answered_with_this_one():
    """League is matched before anything else; a Standard market is not Covens."""
    standard = _market(200, 212, 14_000_000, 67_000, league="Standard")
    assert parse_markets([standard], "Covens") == {}
