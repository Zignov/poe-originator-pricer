"""The repair script's guards, which are the only reason it is safe to run.

`scripts/reparse_listings.py` rewrites stored feature columns from each
listing's saved payload. That is the right way to recover from a parser that
has learned a renamed reward -- the number was in the database all along, in
`raw_data`, simply unread -- and it is also a script that overwrites real data
in place, so what it *refuses* to do matters more than what it does.

The first version of it refused nothing, and a dry run reported it would
"repair" 15,266 Allflame listings. Those rows came from a CSV import: their
payload is `{"csv_row": ...}` with no `properties` array at all, their features
came from the CSV's own columns, and re-parsing them yields seven zeroes. It
would have destroyed every one of them. These pin both guards that came out of
that.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.database.repository import Repository
from src.schema import FEATURE_NAMES

SCRIPT = Path("scripts/reparse_listings.py")

NOW = "2026-11-22T00:00:00+00:00"

#: A Covens trade payload: markup in the names, the new quantity type id.
COVENS_ITEM = {
    "properties": [
        {"name": "[AtlasDropScaling|Atlas Drops]", "type": 112, "values": [["+84%", 1]]},
        {"name": "Item Rarity", "type": 3, "values": [["+134%", 1]]},
        {"name": "More [Thread|Threads]", "type": 89, "values": [["+35%", 1]]},
    ],
    "implicitMods": ["Area is Influenced by the Originator's Memories"],
}

#: What a CSV import stores. No properties, and none to be had.
CSV_PAYLOAD = {"csv_row": {"quantity": "90", "rarity": "140"}}


def _db(tmp_path: Path, rows: list[dict]) -> Path:
    """A database with `rows` written straight in, bypassing the collectors."""
    path = tmp_path / "repair.db"
    repo = Repository(path)
    cols = ", ".join(FEATURE_NAMES)
    marks = ", ".join("?" for _ in FEATURE_NAMES)
    for row in rows:
        repo.conn.execute(
            f"INSERT INTO originator_listings (listing_id, dedupe_key, captured_at, "
            f"first_seen, last_seen, league, map_tier, price_chaos, source, raw_data, {cols}) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {marks})",
            [
                row["id"], row["id"], NOW, NOW, NOW, row["league"],
                16, 100.0, row.get("source", "official_trade_api"),
                json.dumps(row["payload"]),
                *[row.get("features", {}).get(n, 0) for n in FEATURE_NAMES],
            ],
        )
    repo.conn.commit()
    repo.close()
    return path


def _run(db: Path, *args: str) -> str:
    """Run the script against `db` with a config pointed at it."""
    config = db.parent / "config.yaml"
    config.write_text(
        f"league: Covens\ndatabase:\n  path: {db.as_posix()}\n", encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), *args],
        capture_output=True, text=True, cwd=Path.cwd(),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _features(db: Path, listing_id: str) -> dict[str, int]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT {', '.join(FEATURE_NAMES)} FROM originator_listings WHERE listing_id = ?",
        (listing_id,),
    ).fetchone()
    conn.close()
    return {name: row[name] for name in FEATURE_NAMES}


@pytest.fixture(autouse=True)
def _script_exists():
    if not SCRIPT.exists():
        pytest.skip("repair script not in this checkout")


# -- the repair it is for ---------------------------------------------------
def test_a_listing_collected_before_the_rename_is_repaired(tmp_path):
    """quantity 0 -> 84, read back out of the payload that always held it."""
    db = _db(tmp_path, [{"id": "a", "league": "Covens", "payload": COVENS_ITEM,
                         "features": {"rarity": 134, "more_scarabs": 35}}])
    out = _run(db, "--league", "Covens")
    assert "changed 1" in out
    assert _features(db, "a")["quantity"] == 84


def test_a_dry_run_writes_nothing(tmp_path):
    db = _db(tmp_path, [{"id": "a", "league": "Covens", "payload": COVENS_ITEM}])
    out = _run(db, "--league", "Covens", "--dry-run")
    assert "would change 1" in out
    assert _features(db, "a")["quantity"] == 0, "a dry run modified the database"


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    db = _db(tmp_path, [{"id": "a", "league": "Covens", "payload": COVENS_ITEM}])
    _run(db, "--league", "Covens")
    assert "changed 0" in _run(db, "--league", "Covens")


# -- the guard that matters -------------------------------------------------
def test_a_csv_imported_listing_is_never_touched(tmp_path):
    """The 15,266-row mistake, pinned.

    Its payload has no `properties`, so a re-parse yields zeroes -- and those
    zeroes are a statement about the payload, not about the item. The features
    came from the CSV's own columns and are correct.
    """
    full = {name: 50 for name in FEATURE_NAMES}
    db = _db(tmp_path, [{"id": "csv", "league": "Covens", "payload": CSV_PAYLOAD,
                         "source": "manual_csv", "features": full}])
    out = _run(db, "--league", "Covens")
    assert "changed 0" in out
    assert "no properties in their payload" in out
    assert _features(db, "csv") == full, "a CSV-imported listing was overwritten"


def test_an_empty_properties_array_counts_as_no_payload(tmp_path):
    full = {name: 50 for name in FEATURE_NAMES}
    db = _db(tmp_path, [{"id": "e", "league": "Covens", "payload": {"properties": []},
                         "features": full}])
    _run(db, "--league", "Covens")
    assert _features(db, "e") == full


def test_clearing_a_feature_that_has_a_value_is_refused(tmp_path):
    """The second guard, for a payload that *does* have properties but is
    missing one the stored row has.

    Information that exists in only one place is not discarded on the strength
    of a payload that merely fails to mention it.
    """
    db = _db(tmp_path, [{
        "id": "p", "league": "Covens",
        "payload": {"properties": [
            {"name": "Item Rarity", "type": 3, "values": [["+134%", 1]]}
        ]},
        "features": {"rarity": 100, "more_currency": 47},
    }])
    out = _run(db, "--league", "Covens")
    assert "REFUSED" in out
    assert _features(db, "p")["more_currency"] == 47, "a stored value was cleared"


def test_force_applies_the_clearing_it_otherwise_refuses(tmp_path):
    """The escape hatch still exists; it just is not the default."""
    db = _db(tmp_path, [{
        "id": "p", "league": "Covens",
        "payload": {"properties": [
            {"name": "Item Rarity", "type": 3, "values": [["+134%", 1]]}
        ]},
        "features": {"rarity": 100, "more_currency": 47},
    }])
    _run(db, "--league", "Covens", "--force")
    assert _features(db, "p")["more_currency"] == 0
    assert _features(db, "p")["rarity"] == 134


def test_an_unreadable_payload_is_left_alone(tmp_path):
    full = {name: 50 for name in FEATURE_NAMES}
    db = tmp_path / "x.db"
    repo = Repository(db)
    cols = ", ".join(FEATURE_NAMES)
    marks = ", ".join("?" for _ in FEATURE_NAMES)
    repo.conn.execute(
        f"INSERT INTO originator_listings (listing_id, dedupe_key, captured_at, "
        f"first_seen, last_seen, league, map_tier, price_chaos, source, raw_data, {cols}) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,{marks})",
        ["bad", "bad", NOW, NOW, NOW, "Covens", 16, 100.0,
         "official_trade_api", "{not json", *[50] * len(FEATURE_NAMES)],
    )
    repo.conn.commit()
    repo.close()
    out = _run(db, "--league", "Covens")
    assert "could not be re-parsed" in out
    assert _features(db, "bad") == full


# -- scoping ----------------------------------------------------------------
def test_another_league_is_not_touched_without_asking_for_it(tmp_path):
    db = _db(tmp_path, [
        {"id": "a", "league": "Covens", "payload": COVENS_ITEM},
        {"id": "b", "league": "Allflame", "payload": COVENS_ITEM},
    ])
    _run(db, "--league", "Covens")
    assert _features(db, "a")["quantity"] == 84
    assert _features(db, "b")["quantity"] == 0, "a different league was repaired too"


def test_all_leagues_does_what_it_says(tmp_path):
    db = _db(tmp_path, [
        {"id": "a", "league": "Covens", "payload": COVENS_ITEM},
        {"id": "b", "league": "Allflame", "payload": COVENS_ITEM},
    ])
    _run(db, "--all-leagues")
    assert _features(db, "b")["quantity"] == 84


def test_an_unrecognised_reward_is_reported_so_the_fix_is_obvious(tmp_path):
    """A repair run is exactly when someone is looking for this."""
    db = _db(tmp_path, [{
        "id": "u", "league": "Covens",
        "payload": {"properties": [
            {"name": "[Future|Atlas Shards]", "type": 999, "values": [["+60%", 1]]}
        ]},
    }])
    out = _run(db, "--league", "Covens")
    assert "STILL UNRECOGNISED" in out
    assert "Atlas Shards" in out
    assert "PROPERTY_TO_FEATURE" in out, "it should name where to add it"
