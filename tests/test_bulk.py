"""Bulk-lot scanning: the buying path, not the training path.

Everything here is the inverse of how market data is collected, on purpose.
Collection collapses by account and caps per seller so bulk dumps cannot teach
the model that rewards do not matter; a buyer wants precisely those dumps. The
one rule that must not bend is that nothing found here is ever stored, so the
protection stays intact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.market.bulk import BulkListing, BulkLot, scan_bulk_lots
from src.pricing.currency import CurrencyConverter

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _result(listing_id: str, seller: str, amount: float, minutes_old: int = 1, tab: str = "Sale"):
    stamp = (NOW - timedelta(minutes=minutes_old)).isoformat().replace("+00:00", "Z")
    return {
        "id": listing_id,
        "listing": {
            "account": {"name": seller},
            "price": {"amount": amount, "currency": "chaos", "type": "~b/o"},
            "indexed": stamp,
            "stash": {"name": tab, "x": 1, "y": 2},
        },
    }


class FakeCollector:
    """Stands in for the trade API, recording the query it was handed."""

    def __init__(self, results, total=500):
        self.converter = CurrencyConverter(220.0)
        self._results = results
        self._total = total
        self.query = None
        self.fetched: list[list[str]] = []

    def build_query(self, **kwargs):
        self.query = kwargs
        return kwargs

    def search(self, query):
        return "qid", [r["id"] for r in self._results], self._total

    def fetch(self, ids, query_id):
        self.fetched.append(list(ids))
        return [r for r in self._results if r["id"] in ids]


def _scan(results, **kw):
    collector = FakeCollector(results)
    return collector, scan_bulk_lots(collector, **kw)


# ---------------------------------------------------------------------------
def test_the_query_is_the_inverse_of_the_training_collector():
    """Uncollapsed, newest-first, price-capped -- all three deliberately opposite."""
    collector, _ = _scan([_result("a", "Exile#1", 5.0)], price_max=15.0, min_lot=1)
    assert collector.query["collapse"] is False
    assert collector.query["sort_by"] == "indexed"
    assert collector.query["sort"] == "desc"
    assert collector.query["price_max"] == 15.0


def test_listings_are_grouped_into_lots_by_seller():
    results = [_result(str(i), "Bulk#1", 4.0) for i in range(6)] + [
        _result("x", "Solo#2", 9.0)
    ]
    _, scan = _scan(results, min_lot=1)
    assert {lot.seller for lot in scan.lots} == {"Bulk#1", "Solo#2"}
    assert next(lot for lot in scan.lots if lot.seller == "Bulk#1").count == 6


def test_a_seller_below_the_minimum_lot_is_dropped():
    results = [_result(str(i), "Bulk#1", 4.0) for i in range(6)] + [
        _result("x", "Solo#2", 9.0)
    ]
    _, scan = _scan(results, min_lot=5)
    assert [lot.seller for lot in scan.lots] == ["Bulk#1"]


def test_the_biggest_lot_comes_first():
    results = (
        [_result(f"a{i}", "Small#1", 4.0) for i in range(5)]
        + [_result(f"b{i}", "Big#2", 6.0) for i in range(12)]
    )
    _, scan = _scan(results, min_lot=1)
    assert scan.lots[0].seller == "Big#2"


def test_a_flat_priced_lot_is_flagged():
    """One price across a whole lot is the signature of a tab dump."""
    _, scan = _scan([_result(str(i), "Bulk#1", 2.0) for i in range(8)], min_lot=1)
    assert scan.lots[0].flat_priced


def test_a_lot_with_varied_prices_is_not_flagged_flat():
    results = [_result(str(i), "Bulk#1", 2.0 + i) for i in range(8)]
    _, scan = _scan(results, min_lot=1)
    assert not scan.lots[0].flat_priced


def test_lot_totals_and_spread_are_reported():
    results = [_result(str(i), "Bulk#1", p) for i, p in enumerate([4.0, 6.0, 8.0])]
    _, scan = _scan(results, min_lot=1)
    lot = scan.lots[0]
    assert lot.total_chaos == pytest.approx(18.0)
    assert lot.median_chaos == pytest.approx(6.0)
    assert lot.min_chaos == pytest.approx(4.0)
    assert lot.max_chaos == pytest.approx(8.0)


def test_divine_priced_listings_are_converted_to_chaos():
    result = _result("d", "Bulk#1", 1.0)
    result["listing"]["price"]["currency"] = "divine"
    _, scan = _scan([result], min_lot=1)
    assert scan.lots[0].total_chaos == pytest.approx(220.0)


def test_stash_tabs_are_collected_so_you_know_where_to_look():
    results = [
        _result("a", "Bulk#1", 4.0, tab="Maps A"),
        _result("b", "Bulk#1", 4.0, tab="Maps B"),
        _result("c", "Bulk#1", 4.0, tab="Maps A"),
    ]
    _, scan = _scan(results, min_lot=1)
    assert scan.lots[0].stash_tabs() == ["Maps A", "Maps B"]


# ---------------------------------------------------------------------------
def test_a_single_seller_can_be_rechecked_directly():
    """The cheap re-check: one search against one account, not the whole market."""
    collector, _ = _scan([_result("a", "Bulk#1", 4.0)], min_lot=1, seller="Bulk#1")
    assert collector.query["account"] == "Bulk#1"


def test_a_normal_scan_does_not_filter_by_account():
    collector, _ = _scan([_result("a", "Bulk#1", 4.0)], min_lot=1)
    assert collector.query.get("account") is None


def test_listing_age_is_reported_so_staleness_is_visible():
    _, scan = _scan([_result("a", "Bulk#1", 4.0, minutes_old=3)], min_lot=1)
    age = scan.lots[0].newest_age_minutes(NOW)
    assert age == pytest.approx(3.0, abs=0.1)


def test_a_search_failure_is_reported_rather_than_raised():
    from src.market.collectors.official_trade_api import TradeApiError

    class Failing(FakeCollector):
        def search(self, query):
            raise TradeApiError("rate limited by GGG on search")

    scan = scan_bulk_lots(Failing([]))
    assert scan.error
    assert scan.lots == []


def test_an_empty_market_produces_an_empty_scan():
    _, scan = _scan([], min_lot=1)
    assert scan.lots == []
    assert scan.listings_seen == 0
