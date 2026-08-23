"""Duplicate handling and outlier flagging."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.database.repository import Repository
from src.market import outliers
from src.market.collectors.base import dedupe
from src.parsing.item_parser import classify, parse_item_text
from src.schema import MarketListing, NormalizedMap
from tests.conftest import T16_CORRUPTED, T16_UNCORRUPTED_RARE


@pytest.fixture
def repo(tmp_path) -> Repository:
    r = Repository(tmp_path / "test.db")
    yield r
    r.close()


def _listing(**kwargs) -> MarketListing:
    item = kwargs.pop("item", None) or classify(parse_item_text(T16_UNCORRUPTED_RARE))
    defaults = dict(
        item=item,
        league="Allflame",
        price_amount=1.0,
        price_currency="divine",
        price_type="~price",
        # Inside market.plausible_price_chaos, so the basic bounds check does not
        # short-circuit the test that actually wants the peer or age path.
        price_chaos=220.0,
        seller="Exile#1234",
        source="test",
        indexed_at=datetime(2026, 8, 22, 18, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return MarketListing(**defaults)


def test_a_stable_listing_id_is_used_when_present():
    a = _listing(listing_id="abc")
    b = _listing(listing_id="abc", price_amount=99.0)
    assert a.dedupe_key() == b.dedupe_key() == "abc"


def test_fingerprint_is_built_when_there_is_no_listing_id():
    a = _listing()
    b = _listing()
    assert a.listing_id is None
    assert a.dedupe_key() == b.dedupe_key()


def test_different_items_get_different_fingerprints():
    other = classify(parse_item_text(T16_CORRUPTED), require_uncorrupted=False)
    assert _listing().dedupe_key() != _listing(item=other).dedupe_key()


def test_a_different_price_from_the_same_seller_is_a_different_observation():
    assert _listing().dedupe_key() != _listing(price_amount=12.0).dedupe_key()


def test_dedupe_within_a_collection_pass():
    assert len(dedupe([_listing(listing_id="a"), _listing(listing_id="a"), _listing(listing_id="b")])) == 2


def test_reinserting_the_same_listing_updates_instead_of_duplicating(repo):
    first = repo.upsert_listings([_listing(listing_id="abc")])
    assert first == {"inserted": 1, "updated": 0}
    second = repo.upsert_listings([_listing(listing_id="abc")])
    assert second == {"inserted": 0, "updated": 1}
    assert repo.count_listings("Allflame")["total"] == 1


def test_first_seen_is_kept_and_last_seen_moves(repo):
    repo.upsert_listings([_listing(listing_id="abc")])
    row = repo.listing_rows()[0]
    first_seen = row["first_seen"]
    repo.upsert_listings([_listing(listing_id="abc", captured_at=datetime.now(timezone.utc))])
    row = repo.listing_rows()[0]
    assert row["first_seen"] == first_seen
    assert row["last_seen"] >= first_seen


# ---------------------------------------------------------------------------
def _flag(repo, **kwargs) -> str:
    repo.upsert_listings([_listing(**kwargs)])
    rows = repo.listing_rows(include_outliers=True)
    decisions = outliers.evaluate(rows)
    return next(reason for _, _, reason in decisions)


def test_zero_price_is_flagged(repo):
    assert "zero" in _flag(repo, listing_id="z", price_chaos=0.0)


def test_missing_price_is_flagged(repo):
    assert "no usable price" in _flag(repo, listing_id="n", price_chaos=None)


def test_a_map_with_no_rewards_is_flagged(repo):
    blank = NormalizedMap(map_tier=16, is_originator=True, base_type="Map (Tier 16)")
    assert "no reward values" in _flag(repo, listing_id="b", item=blank, price_chaos=5_000_000.0)


def test_a_mirror_priced_listing_is_flagged(repo):
    assert "mirror" in _flag(repo, listing_id="m", price_currency="mirror", price_chaos=6_000_000.0)


def test_a_stale_listing_is_flagged(repo):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    repo.upsert_listings([_listing(listing_id="old", indexed_at=old)])
    decisions = outliers.evaluate(repo.listing_rows(include_outliers=True), max_age_days=7)
    assert "older than" in decisions[0][2]


def test_a_price_above_the_plausible_ceiling_is_flagged(repo):
    # 3 divine is the top of the real uncorrupted T16 market; well past it is
    # parked inventory, not a price.
    assert "above the plausible range" in _flag(repo, listing_id="parked", price_chaos=8_000.0)


def test_an_extreme_price_among_peers_is_flagged(repo):
    # The peer test needs MIN_ROWS_FOR_PEERS usable rows before it will run at
    # all, so the peer group here has to be a realistic size.
    for index in range(outliers.MIN_ROWS_FOR_PEERS + 5):
        repo.upsert_listings([_listing(listing_id=f"n{index}", price_chaos=200.0)])
    # Inside the absolute bounds, so only the peer comparison can catch it.
    repo.upsert_listings([_listing(listing_id="troll", price_chaos=900.0)])
    decisions = {
        row_id: reason
        for row_id, _, reason in outliers.evaluate(repo.listing_rows(include_outliers=True))
    }
    troll_row = next(
        r for r in repo.listing_rows(include_outliers=True) if r["listing_id"] == "troll"
    )
    assert "the price of comparable maps" in decisions[troll_row["id"]]


def test_a_normal_listing_is_not_flagged(repo):
    for index in range(outliers.MIN_ROWS_FOR_PEERS + 5):
        repo.upsert_listings([_listing(listing_id=f"n{index}", price_chaos=200.0 + index)])
    decisions = outliers.evaluate(repo.listing_rows(include_outliers=True))
    assert not any(flag for _, flag, _ in decisions)


def test_flagging_marks_rows_without_deleting_them(repo):
    repo.upsert_listings([_listing(listing_id="z", price_chaos=0.0)])
    rows = repo.listing_rows(include_outliers=True)
    repo.flag_outliers(outliers.evaluate(rows))
    assert repo.count_listings("Allflame")["total"] == 1
    assert repo.count_listings("Allflame")["outliers"] == 1
    assert repo.listing_rows(include_outliers=False) == []


def test_recomputing_prices_after_a_rate_change(repo):
    repo.upsert_listings([_listing(listing_id="r", price_amount=10.0, price_currency="divine")])
    repo.recompute_price_chaos(300.0)
    assert repo.listing_rows(include_outliers=True)[0]["price_chaos"] == 3000.0
