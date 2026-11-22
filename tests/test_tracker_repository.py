"""Persisting what the reconciler decided."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.database.repository import Repository
from src.schema import MarketListing, NormalizedMap
from src.tracking.reconcile import reconcile

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _listing(price=120.0, listing_id="abc", quantity=90):
    item = NormalizedMap(
        name="Glyph Solace", base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=quantity, rarity=140,
        pack_size=30, more_currency=47,
    )
    return MarketListing(
        item=item, listing_id=listing_id, league="Allflame", price_amount=price,
        price_currency="chaos", price_chaos=price, price_type="~b/o", seller="me",
    )


def _repo(tmp_path):
    return Repository(tmp_path / "t.db")


def test_appearance_is_written_and_read_back(tmp_path):
    repo = _repo(tmp_path)
    result = reconcile([], [_listing()], now=T0, complete=True)
    repo.apply_reconciliation("me", "Allflame", result)

    rows = repo.live_tracked_rows("me", "Allflame")
    assert len(rows) == 1
    assert rows[0].price_chaos == 120.0
    assert rows[0].priced_at == T0
    assert rows[0].id is not None


def test_reprice_updates_the_row_and_appends_an_event(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_reconciliation(
        "me", "Allflame", reconcile([], [_listing(180.0)], T0, True)
    )

    later = T0 + timedelta(hours=2)
    rows = repo.live_tracked_rows("me", "Allflame")
    repo.apply_reconciliation(
        "me", "Allflame", reconcile(rows, [_listing(120.0)], later, True)
    )

    rows = repo.live_tracked_rows("me", "Allflame")
    assert len(rows) == 1, "a re-price is one row, not two"
    assert rows[0].price_chaos == 120.0
    assert rows[0].priced_at == later

    events = repo.tracked_events([rows[0].id])
    assert [e["event"] for e in events] == ["appeared", "repriced"]
    assert events[1]["old_price_chaos"] == 180.0


def test_gone_row_leaves_the_live_set_and_keeps_its_bracket(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_reconciliation("me", "Allflame", reconcile([], [_listing()], T0, True))
    rows = repo.live_tracked_rows("me", "Allflame")

    later = T0 + timedelta(hours=9)
    repo.apply_reconciliation("me", "Allflame", reconcile(rows, [], later, True))

    assert repo.live_tracked_rows("me", "Allflame") == []
    gone = repo.recent_gone_rows("me", "Allflame", since=T0 - timedelta(days=1))
    assert len(gone) == 1
    assert gone[0].last_seen == T0
    assert gone[0].gone_first_missing == later


def test_the_stash_position_is_persisted_for_the_overlay(tmp_path):
    repo = _repo(tmp_path)
    listing = _listing()
    listing.raw_data = {"listing": {"stash": {"name": "Sell 3", "x": 4, "y": 7}}}
    repo.apply_reconciliation("me", "Allflame", reconcile([], [listing], T0, True))
    row = repo.tracked_rows_for_page("me", "Allflame")[0]
    assert (row["stash_name"], row["stash_x"], row["stash_y"]) == ("Sell 3", 4, 7)


def test_poll_runs_are_recorded_including_partial_ones(tmp_path):
    repo = _repo(tmp_path)
    repo.record_poll_run(
        started_at=T0, finished_at=T0 + timedelta(seconds=4), league="Allflame",
        account="me", listings_seen=100, total_reported=250, complete=False, error="",
    )
    run = repo.last_poll_run("me", "Allflame")
    assert run["complete"] == 0
    assert run["total_reported"] == 250


def test_exclude_from_learning_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_reconciliation("me", "Allflame", reconcile([], [_listing()], T0, True))
    row_id = repo.live_tracked_rows("me", "Allflame")[0].id
    repo.set_exclude_from_learning(row_id, True)
    page = repo.tracked_rows_for_page("me", "Allflame")
    assert page[0]["exclude_from_learning"] == 1


def test_predictions_are_stored_against_the_listing(tmp_path):
    repo = _repo(tmp_path)
    repo.apply_reconciliation("me", "Allflame", reconcile([], [_listing()], T0, True))
    row_id = repo.live_tracked_rows("me", "Allflame")[0].id
    repo.save_tracked_predictions(
        [
            {
                "id": row_id,
                "predicted_chaos": 140.0,
                "suggested_chaos": 138.6,
                "interval_low_chaos": 110.0,
                "interval_high_chaos": 170.0,
                "model_version": "catboost-2026-08-24",
            }
        ]
    )
    row = repo.tracked_rows_for_page("me", "Allflame")[0]
    assert row["predicted_chaos"] == 140.0
    assert row["model_version"] == "catboost-2026-08-24"


# ---------------------------------------------------------------------------
# GGG's own listing timestamp
# ---------------------------------------------------------------------------
def _indexed(listing, at):
    from dataclasses import replace

    return replace(listing, indexed_at=at)


def test_the_api_listing_timestamp_round_trips(tmp_path):
    repo = _repo(tmp_path)
    listed = T0 - timedelta(hours=62)
    repo.apply_reconciliation(
        "me", "Allflame", reconcile([], [_indexed(_listing(), listed)], T0, True)
    )

    rows = repo.live_tracked_rows("me", "Allflame")
    assert rows[0].indexed_at == listed
    assert rows[0].priced_at == listed, "the age is GGG's, not the poll's"

    page = repo.tracked_rows_for_page("me", "Allflame")
    assert page[0]["indexed_at"] == listed.isoformat()


def test_the_timestamp_is_refreshed_on_every_pass_not_only_on_the_insert(tmp_path):
    """Write-once left every row stamped with whatever the first poll saw, and
    rows from before it was carried permanently NULL."""
    repo = _repo(tmp_path)
    repo.apply_reconciliation("me", "Allflame", reconcile([], [_listing()], T0, True))
    assert repo.live_tracked_rows("me", "Allflame")[0].indexed_at is None

    listed = T0 - timedelta(hours=30)
    later = T0 + timedelta(minutes=15)
    rows = repo.live_tracked_rows("me", "Allflame")
    repo.apply_reconciliation(
        "me", "Allflame", reconcile(rows, [_indexed(_listing(), listed)], later, True)
    )
    assert repo.live_tracked_rows("me", "Allflame")[0].indexed_at == listed


def test_a_pass_without_a_timestamp_does_not_blank_the_one_we_have(tmp_path):
    repo = _repo(tmp_path)
    listed = T0 - timedelta(hours=62)
    repo.apply_reconciliation(
        "me", "Allflame", reconcile([], [_indexed(_listing(), listed)], T0, True)
    )
    rows = repo.live_tracked_rows("me", "Allflame")
    repo.apply_reconciliation(
        "me", "Allflame", reconcile(rows, [_listing()], T0 + timedelta(minutes=15), True)
    )
    assert repo.live_tracked_rows("me", "Allflame")[0].indexed_at == listed
