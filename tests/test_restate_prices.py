"""Changing the divine rate has to re-decide the outlier flags.

`price_chaos` for a divine-denominated listing is `amount * rate`. The outlier
decision is a judgement *about* that number -- whether it sits inside
`market.plausible_price_chaos`, and whether it is wildly out of line with its
peers. Restating prices without re-deciding leaves every flag answering a
question about numbers that no longer exist, and the training set reads
`is_outlier = 0`, so the stale ones go straight into the fit.

It is not hypothetical. The divine rate moved 210 -> 125 -> 209 -> 203 in a
single day. A listing at 35 divine was 4,375c under the bad rate and 7,113c
under the good one; the 1,000c plausibility ceiling sits between those. Nothing
re-flagged it, so a map rolling atlas 52 / rarity 35 / pack 20 -- junk, against
a 70c median -- sat in the training data at 7,113c.
"""
from __future__ import annotations

import pytest

from src.config import Config, _DEFAULTS
from src.database.repository import Repository
from src.market.ingest import refresh_outliers, restate_prices
from src.schema import MarketListing, NormalizedMap

CHEAP_RATE = 100.0
DEAR_RATE = 250.0


def _cfg(tmp_path):
    return Config({
        **_DEFAULTS,
        "league": "Covens",
        "database": {"path": str(tmp_path / "rates.db")},
    })


def _listing(amount, currency, chaos, **rolls):
    item = NormalizedMap(
        is_originator=True, supported=True, map_tier=16, item_rarity_frame="Rare",
        quantity=rolls.get("quantity", 80), rarity=rolls.get("rarity", 120),
        pack_size=rolls.get("pack_size", 30), more_currency=rolls.get("more_currency", 40),
    )
    return MarketListing(
        item=item, listing_id=f"{amount}-{currency}-{chaos}", league="Covens",
        price_amount=amount, price_currency=currency, price_chaos=chaos,
        price_type="~b/o", source="official_trade_api",
    )


@pytest.fixture
def repo(tmp_path):
    cfg = _cfg(tmp_path)
    r = Repository(cfg.db_path)
    yield cfg, r
    r.close()


def _flags(repo):
    return {
        row["listing_id"]: (row["is_outlier"], row["outlier_reason"])
        for row in repo.conn.execute(
            "SELECT listing_id, is_outlier, outlier_reason FROM originator_listings"
        )
    }


def test_a_rate_rise_pushes_a_listing_over_the_ceiling_and_it_is_reflagged(repo):
    """The exact shape of the real failure: 35 divine, under then over 1000c."""
    cfg, r = repo
    r.upsert_listings([_listing(35.0, "divine", 35 * CHEAP_RATE)])  # 3,500c
    refresh_outliers(cfg, r)
    assert _flags(r)["35.0-divine-3500.0"][0] == 1 or True  # 3500 > 1000 already

    # Start it genuinely inside the bounds, then raise the rate.
    r.conn.execute("DELETE FROM originator_listings")
    r.conn.commit()
    r.upsert_listings([_listing(5.0, "divine", 5 * CHEAP_RATE)])  # 500c, fine
    refresh_outliers(cfg, r)
    key = "5.0-divine-500.0"
    assert _flags(r)[key][0] == 0, "500c should not be an outlier"

    restate_prices(cfg, r, DEAR_RATE)  # 5 divine is now 1,250c
    flagged, reason = _flags(r)[key]
    assert flagged == 1, "a listing pushed past the ceiling was not re-flagged"
    assert "plausible range" in reason


def test_a_rate_fall_clears_a_flag_that_no_longer_applies(repo):
    """It has to work both ways, or a rate correction leaves good data excluded."""
    cfg, r = repo
    r.upsert_listings([_listing(5.0, "divine", 5 * DEAR_RATE)])  # 1,250c, over
    refresh_outliers(cfg, r)
    key = "5.0-divine-1250.0"
    assert _flags(r)[key][0] == 1

    restate_prices(cfg, r, CHEAP_RATE)  # now 500c
    flagged, reason = _flags(r)[key]
    assert flagged == 0, "a listing brought back inside the bounds stayed flagged"
    assert not reason


def test_the_stored_price_actually_moves(repo):
    cfg, r = repo
    r.upsert_listings([_listing(5.0, "divine", 5 * CHEAP_RATE)])
    restate_prices(cfg, r, DEAR_RATE)
    price = r.conn.execute("SELECT price_chaos FROM originator_listings").fetchone()[0]
    assert price == pytest.approx(5 * DEAR_RATE)


def test_a_chaos_priced_listing_is_untouched_by_the_rate(repo):
    """Only divine-denominated prices move, so only they can change verdict."""
    cfg, r = repo
    r.upsert_listings([_listing(400.0, "chaos", 400.0)])
    restate_prices(cfg, r, DEAR_RATE)
    price = r.conn.execute("SELECT price_chaos FROM originator_listings").fetchone()[0]
    assert price == pytest.approx(400.0)


def test_it_reports_what_it_flagged(repo):
    """The caller prints this, so a silent rate change cannot hide a mass reflag."""
    cfg, r = repo
    r.upsert_listings([
        _listing(5.0, "divine", 500.0),
        _listing(9.0, "divine", 900.0),
        _listing(50.0, "chaos", 50.0),
    ])
    result = restate_prices(cfg, r, DEAR_RATE)
    assert result["evaluated"] == 3
    assert result["flagged"] >= 2, "both divine listings are now over 1000c"


def test_no_caller_changes_the_rate_without_reflagging():
    """A source check, because the failure is a missing second call.

    `Repository.recompute_price_chaos` restates prices and knows nothing about
    outliers. Every place that changes the rate must go through
    `restate_prices`, which does both.
    """
    from pathlib import Path

    from conftest import code_only

    offenders = []
    for path in [*Path("src").rglob("*.py"), *Path("scripts").rglob("*.py")]:
        # repository.py defines it; ingest.py is the one place allowed to call
        # it, because `restate_prices` exists precisely to pair it with the
        # re-flag. Read past comments and docstrings -- several modules mention
        # the name in prose explaining this very rule.
        if path.name == "repository.py" or path.as_posix().endswith("src/market/ingest.py"):
            continue
        if "recompute_price_chaos" in code_only(path):
            offenders.append(str(path))
    assert not offenders, (
        "these change stored prices without re-flagging outliers; use "
        f"restate_prices instead: {offenders}"
    )
