"""SQLite schema and forward-only migrations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Each entry is one migration, applied in order. Never edit a released entry --
# append a new one instead.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS originator_listings (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id            TEXT,
            dedupe_key            TEXT NOT NULL UNIQUE,
            captured_at           TEXT NOT NULL,
            first_seen            TEXT NOT NULL,
            last_seen             TEXT NOT NULL,
            indexed_at            TEXT,
            league                TEXT NOT NULL,

            name                  TEXT,
            base_type             TEXT,
            map_tier              INTEGER,
            item_level            INTEGER,
            quality               INTEGER DEFAULT 0,
            corrupted             INTEGER DEFAULT 0,
            item_rarity_frame     TEXT,

            quantity              INTEGER DEFAULT 0,
            rarity                INTEGER DEFAULT 0,
            pack_size             INTEGER DEFAULT 0,
            more_currency         INTEGER DEFAULT 0,
            more_maps             INTEGER DEFAULT 0,
            more_scarabs          INTEGER DEFAULT 0,
            more_divination_cards INTEGER DEFAULT 0,

            price_amount          REAL,
            price_currency        TEXT,
            price_type            TEXT,
            price_chaos           REAL,

            seller                TEXT,
            source                TEXT,
            is_outlier            INTEGER DEFAULT 0,
            outlier_reason        TEXT DEFAULT '',
            raw_data              TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_listings_league_time
            ON originator_listings (league, indexed_at);
        CREATE INDEX IF NOT EXISTS idx_listings_outlier
            ON originator_listings (is_outlier);

        CREATE TABLE IF NOT EXISTS currency_rates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            league          TEXT NOT NULL,
            divine_to_chaos REAL NOT NULL,
            source          TEXT DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS stash_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT NOT NULL,
            league       TEXT NOT NULL,
            account      TEXT,
            tab_ids      TEXT,
            tab_names    TEXT,
            source       TEXT,
            item_count   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS stash_items (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id           INTEGER NOT NULL REFERENCES stash_snapshots(id) ON DELETE CASCADE,
            stash_item_id         TEXT,
            stash_tab_id          TEXT,
            stash_tab_name        TEXT,
            x                     INTEGER,
            y                     INTEGER,
            width                 INTEGER DEFAULT 1,
            height                INTEGER DEFAULT 1,

            name                  TEXT,
            base_type             TEXT,
            map_tier              INTEGER,
            item_level            INTEGER,
            quality               INTEGER DEFAULT 0,
            corrupted             INTEGER DEFAULT 0,
            item_rarity_frame     TEXT,
            is_originator         INTEGER DEFAULT 0,

            quantity              INTEGER DEFAULT 0,
            rarity                INTEGER DEFAULT 0,
            pack_size             INTEGER DEFAULT 0,
            more_currency         INTEGER DEFAULT 0,
            more_maps             INTEGER DEFAULT 0,
            more_scarabs          INTEGER DEFAULT 0,
            more_divination_cards INTEGER DEFAULT 0,

            supported             INTEGER DEFAULT 0,
            unsupported_reason    TEXT DEFAULT '',
            raw_modifiers         TEXT,
            raw_item_data         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_stash_items_snapshot
            ON stash_items (snapshot_id);

        CREATE TABLE IF NOT EXISTS model_runs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at        TEXT NOT NULL,
            model_version     TEXT NOT NULL,
            backend           TEXT NOT NULL,
            target            TEXT NOT NULL,
            dataset_size      INTEGER NOT NULL,
            feature_list      TEXT NOT NULL,
            metrics           TEXT NOT NULL,
            market_date_start TEXT,
            market_date_end   TEXT,
            artifact_path     TEXT,
            is_active         INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT NOT NULL,
            snapshot_id         INTEGER,
            item_fingerprint    TEXT NOT NULL,
            features            TEXT NOT NULL,
            predicted_chaos     REAL,
            suggested_chaos     REAL,
            interval_low_chaos  REAL,
            interval_high_chaos REAL,
            confidence          TEXT,
            model_version       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_predictions_fingerprint
            ON predictions (item_fingerprint);
        """,
    ),
    (
        2,
        """
        -- Small key/value store for fetched facts that are not listings: the
        -- Exalted Orb price, and whatever else needs to outlive a session
        -- without deserving a table of its own. The orb price previously lived
        -- in session state, so it reverted to the config default on restart and
        -- the refresh button looked like it did nothing.
        CREATE TABLE IF NOT EXISTS app_settings (
            key        TEXT PRIMARY KEY,
            value      REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        3,
        """
        -- The account's own listings, tracked over time. One row per map, not
        -- per price: a re-price mutates priced_at here and appends an event to
        -- tracked_listing_events, so a map that wore three prices is one row and
        -- three episodes.
        --
        -- There is deliberately no unique index. Identity is a judgement --
        -- same trade id, or same reward fingerprint, or a twin that cannot be
        -- told apart from its sibling -- and it belongs in a tested pure
        -- function (src/tracking/reconcile.py), not in a constraint that would
        -- refuse to insert the second of two genuinely identical live maps.
        CREATE TABLE IF NOT EXISTS tracked_listings (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            account               TEXT NOT NULL,
            league                TEXT NOT NULL,
            trade_listing_id      TEXT,
            fingerprint           TEXT NOT NULL,

            name                  TEXT,
            base_type             TEXT,
            map_tier              INTEGER,
            item_level            INTEGER,
            corrupted             INTEGER DEFAULT 0,

            quantity              INTEGER DEFAULT 0,
            rarity                INTEGER DEFAULT 0,
            pack_size             INTEGER DEFAULT 0,
            more_currency         INTEGER DEFAULT 0,
            more_maps             INTEGER DEFAULT 0,
            more_scarabs          INTEGER DEFAULT 0,
            more_divination_cards INTEGER DEFAULT 0,

            price_amount          REAL,
            price_currency        TEXT,
            price_chaos           REAL,
            price_type            TEXT,

            -- Where the map physically is, straight from listing.stash in the
            -- fetch result (src/market/bulk.py already reads these three). This
            -- is what lets the overlay draw on a listed map without a separate
            -- stash read, and without guessing which of two similar maps is
            -- which.
            stash_name            TEXT,
            stash_x               INTEGER,
            stash_y               INTEGER,

            -- indexed_at is GGG's own listing.indexed. priced_at is when the
            -- current price started, which is what the clock runs from: a map
            -- dropped from 180c to 120c and sold ten minutes later is a
            -- ten-minute sale at 120c, not a three-day sale.
            indexed_at            TEXT,
            priced_at             TEXT NOT NULL,
            first_seen            TEXT NOT NULL,
            last_seen             TEXT NOT NULL,
            -- The upper bound of the sale interval: the first poll that did not
            -- see it. With last_seen this brackets the sale instead of
            -- pretending to time it.
            gone_first_missing    TEXT,
            status                TEXT NOT NULL DEFAULT 'live',

            predicted_chaos       REAL,
            suggested_chaos       REAL,
            interval_low_chaos    REAL,
            interval_high_chaos   REAL,
            model_version         TEXT,

            ambiguous             INTEGER DEFAULT 0,
            exclude_from_learning INTEGER DEFAULT 0,
            raw_data              TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tracked_identity
            ON tracked_listings (account, league, fingerprint, status);
        CREATE INDEX IF NOT EXISTS idx_tracked_status
            ON tracked_listings (league, status, last_seen);

        CREATE TABLE IF NOT EXISTS tracked_listing_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id      INTEGER NOT NULL
                            REFERENCES tracked_listings(id) ON DELETE CASCADE,
            at              TEXT NOT NULL,
            event           TEXT NOT NULL,
            old_price_chaos REAL,
            new_price_chaos REAL
        );

        CREATE INDEX IF NOT EXISTS idx_tracked_events_listing
            ON tracked_listing_events (listing_id, at);

        -- Every pass, including the ones that failed or came back partial.
        -- Without this an app closed for eight hours and a poll that quietly
        -- errored look identical, and both look like a quiet market.
        CREATE TABLE IF NOT EXISTS listing_poll_runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at     TEXT NOT NULL,
            finished_at    TEXT,
            league         TEXT NOT NULL,
            account        TEXT NOT NULL,
            listings_seen  INTEGER DEFAULT 0,
            total_reported INTEGER DEFAULT 0,
            complete       INTEGER DEFAULT 0,
            error          TEXT DEFAULT ''
        );
        """,
    ),
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because Streamlit runs each rerun of a session's
    # script on a fresh thread while still handing back the same cached
    # Repository. Streamlit serializes reruns within a session, so the connection
    # is never used concurrently -- it just moves between threads.
    conn = sqlite3.connect(path, detect_types=0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations. Returns the resulting schema version."""
    version = current_version(conn)
    for target, sql in MIGRATIONS:
        if target <= version:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
        conn.commit()
        version = target
    return version


def init_db(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    migrate(conn)
    return conn
