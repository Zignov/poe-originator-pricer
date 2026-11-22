"""Which trade filter the quantity window is sent as.

One line, and it silently broke the entire market-comparison mode for a league.

GGG's `/api/trade/data/filters` lists both of these today:

    map_iiq      Increased Item Quantity
    map_bonus    Atlas Drops

The reward this project stores as `quantity` is displayed as `Atlas Drops` on
current maps, and those maps carry no `Item Quantity` property at all -- so a
`map_iiq` window excludes every single one. Measured live on 2026-11-22, the
same query with the same +/-10 window around one map's roll:

    map_iiq   83-103  ->      0 listings   (21 searches, every one empty)
    map_bonus 83-103  ->  10000 listings

What made it expensive to find is that it does not look like a bug. Every map
simply reported "no live listings matched, even at the widest tolerance",
which is a sentence the mode is supposed to be able to say -- it is what a
genuinely unusual map looks like. Seven out of seven saying it is the tell.

Note this is the *opposite* of the item property situation, where Atlas Drops
took over the quantity slot outright and the old name is now just an alias. On
the trade site both filters exist and mean different things, so it cannot be
fixed with an alias table.
"""
from __future__ import annotations

import pytest

from src.market.collectors.official_trade_api import (
    QUANTITY_FILTER,
    OfficialTradeApiCollector,
)


@pytest.fixture
def collector():
    return OfficialTradeApiCollector(
        league="Covens", user_agent="test", converter=None, map_tier=16,
        require_uncorrupted=True,
    )


def _map_filters(query):
    return query["query"]["filters"]["map_filters"]["filters"]


def test_the_quantity_window_is_sent_as_map_bonus(collector):
    filters = _map_filters(collector.build_query(quantity=(83, 103)))
    assert QUANTITY_FILTER in filters
    assert filters[QUANTITY_FILTER] == {"min": 83, "max": 103}


def test_map_iiq_is_not_sent(collector):
    """It matches nothing on a current map and excludes every real comparable."""
    assert "map_iiq" not in _map_filters(collector.build_query(quantity=(83, 103)))


def test_the_filter_constant_is_the_measured_one():
    assert QUANTITY_FILTER == "map_bonus"


def test_the_other_two_windows_are_unchanged(collector):
    """Only the quantity filter moved; rarity and pack size were never renamed."""
    filters = _map_filters(
        collector.build_query(map_rarity=(142, 162), pack_size=(24, 44))
    )
    assert filters["map_iir"] == {"min": 142, "max": 162}
    assert filters["map_packsize"] == {"min": 24, "max": 44}


def test_an_open_ended_window_sends_only_the_bound_it_has(collector):
    assert _map_filters(collector.build_query(quantity=(50, None)))[QUANTITY_FILTER] == {
        "min": 50
    }
    assert _map_filters(collector.build_query(quantity=(None, 50)))[QUANTITY_FILTER] == {
        "max": 50
    }


def test_no_window_sends_no_quantity_filter(collector):
    """A slice that does not stratify on quantity must not constrain it."""
    assert QUANTITY_FILTER not in _map_filters(collector.build_query())


def test_the_tier_filter_is_always_present(collector):
    """The one constraint every query carries, regardless of the rest."""
    assert _map_filters(collector.build_query())["map_tier"] == {"min": 16, "max": 16}


def test_the_live_comparables_window_reaches_the_query(collector):
    """End to end through the function that actually builds the search.

    `comparable_query` is the only caller that passes a quantity window, so if
    the value stopped arriving in the query nothing else in the suite would
    notice. Driven through the real collector's `build_query` rather than a
    stub, so the filter key is the one a live search would carry.
    """
    from src.market.live_comparables import comparable_query
    from src.schema import NormalizedMap

    item = NormalizedMap(
        is_originator=True, supported=True, map_tier=16,
        quantity=84, rarity=134, pack_size=32, more_currency=47,
    )
    filters = _map_filters(comparable_query(collector.build_query, item, 1.0))

    assert QUANTITY_FILTER in filters, "the quantity window never reached the query"
    assert "map_iiq" not in filters
    low, high = filters[QUANTITY_FILTER]["min"], filters[QUANTITY_FILTER]["max"]
    assert low <= 84 <= high, f"the map's own roll of 84 is outside {low}-{high}"
