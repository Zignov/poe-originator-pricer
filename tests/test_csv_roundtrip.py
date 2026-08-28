"""Export -> import round-trip for the market dataset.

The Market Data page used to export the *display* frame -- the same columns the
on-screen table shows -- and offer it as a dataset export. It could not be
re-imported (the importer wants price_amount / price_currency, the view wrote
price / currency), and worse, its `price` column was unit-converted for display
while `currency` held the listing's original currency. Renaming the columns
would therefore have multiplied every divine-priced listing by the divine rate
instead of raising an error.

These tests pin the round-trip itself, which is the only thing that catches that
class of drift.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.database.repository import Repository
from src.market.collectors.manual_import import (
    CSV_TEMPLATE_COLUMNS,
    ManualCsvCollector,
    listing_rows_to_csv,
)
from src.parsing.item_parser import classify, parse_item_text
from src.pricing.currency import CurrencyConverter
from src.schema import FEATURE_NAMES, MarketListing
from tests.conftest import T16_UNCORRUPTED_RARE

DIVINE_RATE = 220.0


@pytest.fixture
def repo(tmp_path) -> Repository:
    r = Repository(tmp_path / "roundtrip.db")
    yield r
    r.close()


def _listing(**kwargs) -> MarketListing:
    item = classify(parse_item_text(T16_UNCORRUPTED_RARE))
    defaults = dict(
        item=item,
        league="Allflame",
        price_amount=120.0,
        price_currency="chaos",
        price_type="~price",
        price_chaos=120.0,
        seller="Exile#1234",
        source="test",
        indexed_at=datetime(2026, 8, 22, 18, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return MarketListing(**defaults)


def _reimport(csv_text: str) -> list[MarketListing]:
    collector = ManualCsvCollector(CurrencyConverter(DIVINE_RATE), "Allflame", 16)
    return list(collector.collect(text=csv_text))


def test_exported_csv_has_the_columns_the_importer_requires(repo):
    repo.upsert_listings([_listing(listing_id="a")])
    header = listing_rows_to_csv(repo.listing_rows()).splitlines()[0]
    assert header.split(",") == list(CSV_TEMPLATE_COLUMNS)


def test_a_chaos_priced_listing_survives_the_round_trip(repo):
    repo.upsert_listings([_listing(listing_id="a", price_amount=120.0, price_chaos=120.0)])
    back = _reimport(listing_rows_to_csv(repo.listing_rows()))
    assert len(back) == 1
    assert back[0].price_chaos == pytest.approx(120.0)


def test_a_divine_priced_listing_is_not_multiplied_by_the_divine_rate(repo):
    """The regression that a rename-only fix would have introduced silently."""
    repo.upsert_listings(
        [_listing(listing_id="d", price_amount=1.0, price_currency="divine", price_chaos=DIVINE_RATE)]
    )
    back = _reimport(listing_rows_to_csv(repo.listing_rows()))
    assert back[0].price_currency == "divine"
    assert back[0].price_amount == pytest.approx(1.0)
    assert back[0].price_chaos == pytest.approx(DIVINE_RATE)


def test_the_reward_features_survive_the_round_trip(repo):
    repo.upsert_listings([_listing(listing_id="a")])
    original = repo.listing_rows()[0]
    back = _reimport(listing_rows_to_csv(repo.listing_rows()))[0]
    assert back.item.features() == {name: original[name] for name in FEATURE_NAMES}


def test_indexed_at_survives_so_recency_weighting_still_works(repo):
    when = datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc)
    repo.upsert_listings([_listing(listing_id="a", indexed_at=when)])
    back = _reimport(listing_rows_to_csv(repo.listing_rows()))[0]
    assert back.indexed_at is not None
    assert back.indexed_at.replace(tzinfo=timezone.utc) == when


def test_an_empty_dataset_still_exports_a_usable_header(repo):
    csv_text = listing_rows_to_csv(repo.listing_rows())
    assert csv_text.splitlines()[0].split(",") == list(CSV_TEMPLATE_COLUMNS)
    assert _reimport(csv_text) == []


def test_the_anonymised_export_drops_the_account_name_and_the_listing_id(repo):
    """The shipped sample must not carry account names, or live ids that would
    upsert over somebody's real rows."""
    repo.upsert_listings([_listing(listing_id="live-id", seller="Exile#1234")])
    back = _reimport(listing_rows_to_csv(repo.listing_rows(), anonymise=True))[0]
    assert back.listing_id is None
    assert "Exile" not in (back.seller or "")
    assert back.price_chaos == pytest.approx(120.0)


def test_the_anonymised_export_still_says_which_rows_shared_an_account(repo):
    """Blanking the column outright cost more than it protected.

    `grouped_split` groups on the seller to keep one account's maps off both
    sides of the train/test line. When the whole sample shared the empty string
    it became one group, and seeding a fresh install then retraining produced
    train 2743 / valid 3 / test 5. The label is meaningless outside the file;
    what survives is only the fact that two rows came from one account.
    """
    repo.upsert_listings(
        [
            _listing(listing_id="a", seller="Exile#1234"),
            _listing(listing_id="b", seller="Exile#1234"),
            _listing(listing_id="c", seller="Someone#9999"),
        ]
    )
    back = _reimport(listing_rows_to_csv(repo.listing_rows(), anonymise=True))
    labels = [row.seller for row in back]
    assert all(label and "#" not in label for label in labels)
    assert len(set(labels)) == 2
    # The two rows that shared an account still share a label.
    assert max(labels.count(label) for label in set(labels)) == 2


def test_the_normal_export_keeps_seller_and_listing_id(repo):
    repo.upsert_listings([_listing(listing_id="live-id", seller="Exile#1234")])
    back = _reimport(listing_rows_to_csv(repo.listing_rows()))[0]
    assert back.seller == "Exile#1234"
    assert back.listing_id == "live-id"
