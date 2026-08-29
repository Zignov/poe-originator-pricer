"""Reading your own listings: the query shape, and refusing to guess when truncated."""
from __future__ import annotations

from typing import Any

import pytest

from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.market.my_listings import fetch_my_listings


class _Converter:
    def to_chaos(self, amount, currency):
        return amount


def _result(listing_id: str, price: float) -> dict[str, Any]:
    return {
        "id": listing_id,
        "listing": {
            "indexed": "2026-08-28T10:00:00Z",
            "account": {"name": "me"},
            "price": {"type": "~b/o", "amount": price, "currency": "chaos"},
            "stash": {"name": "Sell 3", "x": 4, "y": 7},
        },
        "item": {
            "name": "Glyph Solace",
            "typeLine": "Terror Solace",
            "ilvl": 84,
            "frameType": 2,
            "league": "Allflame",
            "identified": True,
            "properties": [
                {"name": "Map Tier", "values": [["16", 0]]},
                {"name": "Item Quantity", "values": [["+90%", 1]]},
                {"name": "Item Rarity", "values": [["+140%", 1]]},
                {"name": "Monster Pack Size", "values": [["+30%", 1]]},
            ],
            "implicitMods": ["Area is Influenced by the Originator's Memories"],
        },
    }


class _FakeCollector(OfficialTradeApiCollector):
    def __init__(self, ids, total, results):
        super().__init__(league="Allflame", user_agent="test", converter=_Converter())
        self._ids, self._total, self._results = ids, total, results
        self.queries: list[dict] = []

    def search(self, query):
        self.queries.append(query)
        return "qid", list(self._ids), self._total

    def fetch(self, ids, query_id):
        return [self._results[i] for i in ids]


def test_query_carries_the_account_filter_and_is_uncollapsed():
    c = _FakeCollector(["a"], 1, {"a": _result("a", 120.0)})
    fetch_my_listings(c, account="me")
    trade = c.queries[0]["query"]["filters"]["trade_filters"]["filters"]
    assert trade["account"] == {"input": "me"}
    assert "collapse" not in trade, (
        "collapse groups by account -- on, an account search returns one row and "
        "every other map looks sold. In this codebase off means the key is "
        "omitted: build_query emits it only when on, bulk.py relies on that, and "
        "test_live_search_link pins the same convention."
    )
    assert "sale_type" not in trade, "omitted means buyout-or-fixed-price"
    assert c.queries[0]["query"]["status"] == {"option": "any"}


def test_complete_snapshot_returns_every_listing():
    c = _FakeCollector(
        ["a", "b"], 2, {"a": _result("a", 120.0), "b": _result("b", 90.0)}
    )
    snap = fetch_my_listings(c, account="me")
    assert snap.complete is True
    assert snap.total_reported == 2
    assert sorted(listing.listing_id for listing in snap.listings) == ["a", "b"]


def test_the_stash_position_rides_along_for_the_overlay():
    c = _FakeCollector(["a"], 1, {"a": _result("a", 120.0)})
    snap = fetch_my_listings(c, account="me")
    stash = snap.listings[0].raw_data["listing"]["stash"]
    assert (stash["name"], stash["x"], stash["y"]) == ("Sell 3", 4, 7)


def test_truncated_result_is_marked_incomplete():
    ids = [f"i{n}" for n in range(100)]
    c = _FakeCollector(ids, 250, {i: _result(i, 100.0) for i in ids})
    snap = fetch_my_listings(c, account="me", max_ids=100)
    assert snap.complete is False, "250 listings cannot be enumerated by one search"
    assert snap.total_reported == 250


def test_empty_account_name_refuses_rather_than_polling():
    c = _FakeCollector([], 0, {})
    with pytest.raises(ValueError, match="account"):
        fetch_my_listings(c, account="")
