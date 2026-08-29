"""Migration 3 creates the tracker tables with the columns the tracker needs."""
from __future__ import annotations

from src.database.models import init_db

TRACKED_COLUMNS = {
    "id", "account", "league", "trade_listing_id", "fingerprint",
    "name", "base_type", "map_tier", "item_level", "corrupted",
    "quantity", "rarity", "pack_size", "more_currency", "more_maps",
    "more_scarabs", "more_divination_cards",
    "price_amount", "price_currency", "price_chaos", "price_type",
    "stash_name", "stash_x", "stash_y",
    "indexed_at", "priced_at", "first_seen", "last_seen",
    "gone_first_missing", "status",
    "predicted_chaos", "suggested_chaos", "interval_low_chaos",
    "interval_high_chaos", "model_version",
    "ambiguous", "exclude_from_learning", "raw_data",
}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_tracked_listings_has_every_column(tmp_path):
    conn = init_db(tmp_path / "t.db")
    assert _columns(conn, "tracked_listings") == TRACKED_COLUMNS


def test_events_and_runs_tables_exist(tmp_path):
    conn = init_db(tmp_path / "t.db")
    assert {
        "id", "listing_id", "at", "event", "old_price_chaos", "new_price_chaos"
    } == _columns(conn, "tracked_listing_events")
    assert {
        "id", "started_at", "finished_at", "league", "account",
        "listings_seen", "total_reported", "complete", "error",
    } == _columns(conn, "listing_poll_runs")


def test_status_defaults_to_live(tmp_path):
    conn = init_db(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO tracked_listings (account, league, fingerprint, first_seen, "
        "last_seen, priced_at) VALUES ('me', 'Allflame', 'fp', 'now', 'now', 'now')"
    )
    row = conn.execute("SELECT status, ambiguous FROM tracked_listings").fetchone()
    assert row[0] == "live"
    assert row[1] == 0


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    init_db(path).close()
    conn = init_db(path)
    assert "tracked_listings" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
