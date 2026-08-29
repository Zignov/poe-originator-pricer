"""A dataset exported and re-imported has to still know when it was collected."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.database.repository import Repository
from src.market.collectors.manual_import import (
    CSV_TEMPLATE_COLUMNS,
    ManualCsvCollector,
    listing_rows_to_csv,
)
from src.ml.validation import is_snapshot
from src.pricing.currency import CurrencyConverter
from src.schema import FEATURE_NAMES, MarketListing, NormalizedMap

import pandas as pd


def _listing(captured: datetime, price: float = 120.0, seller: str = "Exile#1") -> MarketListing:
    item = NormalizedMap(
        base_type="Map (Tier 16)", map_tier=16, item_level=84, corrupted=False,
        is_originator=True, quantity=90, rarity=70, pack_size=50, more_currency=40,
        more_maps=0, more_scarabs=60, more_divination_cards=30,
    )
    return MarketListing(
        item=item, listing_id=None, captured_at=captured, indexed_at=captured,
        league="Allflame", price_amount=price, price_currency="chaos",
        price_type="~price", price_chaos=price, seller=seller, source="official_trade_api",
    )


@pytest.fixture
def repo(tmp_path):
    with Repository(tmp_path / "t.db") as r:
        yield r


def _reimport(csv_text: str) -> list[MarketListing]:
    collector = ManualCsvCollector(converter=CurrencyConverter(200.0), league="Allflame")
    return list(collector.collect(text=csv_text))


def test_captured_at_survives_the_round_trip(repo):
    when = datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)
    repo.upsert_listings([_listing(when)])
    back = _reimport(listing_rows_to_csv(repo.listing_rows(), anonymise=True))[0]
    assert back.captured_at == when


def test_a_csv_without_the_column_still_imports(repo):
    """Everything past the feature and price columns is optional, and stays so."""
    header = ",".join(c for c in CSV_TEMPLATE_COLUMNS if c != "captured_at")
    row = ",".join(["50"] * len(FEATURE_NAMES)) + ",40,chaos,16,84,false,,,2026-08-12T09:30:00+00:00"
    before = datetime.now(timezone.utc)
    back = _reimport(f"{header}\n{row}\n")[0]
    assert back.captured_at >= before


def test_weeks_of_collections_do_not_re_import_as_one_snapshot(repo):
    """The reason this column is worth a schema change.

    `is_snapshot` measures the span of captured_at, and its answer picks the
    validation split -- chronological for a real collection history, seller
    grouped for a single sitting. Losing the column stamped every row with the
    moment of import, so three weeks of collections arrived looking like one
    snapshot and were validated as one.
    """
    start = datetime(2026, 8, 6, tzinfo=timezone.utc)
    repo.upsert_listings(
        [_listing(start + timedelta(days=d), seller=f"Exile#{d}") for d in range(21)]
    )
    frame = pd.DataFrame(
        [{"captured_at": row.captured_at} for row in _reimport(
            listing_rows_to_csv(repo.listing_rows(), anonymise=True)
        )]
    )
    assert not is_snapshot(frame)


def test_the_template_row_matches_the_template_header():
    from src.market.collectors.manual_import import csv_template

    header, example = csv_template().splitlines()[:2]
    assert len(header.split(",")) == len(example.split(","))
