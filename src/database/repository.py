"""Everything that touches SQLite lives here."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.database.models import init_db
from src.schema import FEATURE_NAMES, MarketListing, NormalizedMap


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


#: What the pricing and scoring paths read: everything except `raw_data`, which
#: none of them look at and all of them would pay ~5KB a row to fetch. Keep in
#: step with `rows_to_frame`, `find_comparables` and `outliers.evaluate`, which
#: are the readers this list exists for.
PRICING_COLUMNS: tuple[str, ...] = (
    "id",
    "listing_id",
    "captured_at",
    "first_seen",
    "indexed_at",
    "league",
    "name",
    "base_type",
    "map_tier",
    "item_level",
    "quality",
    "corrupted",
    *FEATURE_NAMES,
    "price_amount",
    "price_currency",
    "price_type",
    "price_chaos",
    "seller",
    "source",
    "is_outlier",
    "outlier_reason",
)


class Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection = init_db(self.db_path)

    @classmethod
    def read_only(cls, db_path: str | Path) -> "Repository":
        """Open an existing database without migrating it or writing to it.

        For the jobs that only read: exporting a dataset, inspecting one, or
        reading a database that another process has open. The normal
        constructor runs `init_db`, which creates the file if it is missing and
        applies migrations -- neither of which belongs in a script whose whole
        purpose is to look. SQLite enforces the read-only part itself, so a
        stray write raises instead of quietly landing in someone's live data.
        """
        path = Path(db_path)
        if not path.exists():
            raise FileNotFoundError(path)
        repo = cls.__new__(cls)
        repo.db_path = path
        repo.conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        repo.conn.row_factory = sqlite3.Row
        return repo

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Market listings
    # ------------------------------------------------------------------
    def upsert_listings(self, listings: Iterable[MarketListing]) -> dict[str, int]:
        """Insert new listings, refresh last_seen on ones already stored.

        Returns {"inserted": n, "updated": n}.
        """
        inserted = updated = 0
        cur = self.conn.cursor()
        for listing in listings:
            key = listing.dedupe_key()
            now = _iso(listing.captured_at)
            existing = cur.execute(
                "SELECT id FROM originator_listings WHERE dedupe_key = ?", (key,)
            ).fetchone()
            if existing:
                cur.execute(
                    "UPDATE originator_listings SET last_seen = ?, price_amount = ?, "
                    "price_currency = ?, price_chaos = ?, price_type = ? WHERE id = ?",
                    (
                        now,
                        listing.price_amount,
                        listing.price_currency,
                        listing.price_chaos,
                        listing.price_type,
                        existing["id"],
                    ),
                )
                updated += 1
                continue

            item = listing.item
            cur.execute(
                """
                INSERT INTO originator_listings (
                    listing_id, dedupe_key, captured_at, first_seen, last_seen,
                    indexed_at, league, name, base_type, map_tier, item_level,
                    quality, corrupted, item_rarity_frame,
                    quantity, rarity, pack_size, more_currency, more_maps,
                    more_scarabs, more_divination_cards,
                    price_amount, price_currency, price_type, price_chaos,
                    seller, source, is_outlier, outlier_reason, raw_data
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    listing.listing_id,
                    key,
                    now,
                    now,
                    now,
                    _iso(listing.indexed_at),
                    listing.league,
                    item.name,
                    item.base_type,
                    item.map_tier,
                    item.item_level,
                    item.quality,
                    int(item.corrupted),
                    item.item_rarity_frame,
                    item.quantity,
                    item.rarity,
                    item.pack_size,
                    item.more_currency,
                    item.more_maps,
                    item.more_scarabs,
                    item.more_divination_cards,
                    listing.price_amount,
                    listing.price_currency,
                    listing.price_type,
                    listing.price_chaos,
                    listing.seller,
                    listing.source,
                    int(listing.is_outlier),
                    listing.outlier_reason,
                    json.dumps(listing.raw_data, ensure_ascii=False),
                ),
            )
            inserted += 1
        self.conn.commit()
        return {"inserted": inserted, "updated": updated}

    def delete_listings_by_source(self, source: str, league: str | None = None) -> int:
        """Remove every listing that arrived from one named source.

        Narrow on purpose. The only caller is the first-run seed replacing its
        own bundled sample with a newer one (`src/market/seed.py`), and the
        source label is what keeps that from reaching a single row the user
        collected or imported themselves.
        """
        sql = "DELETE FROM originator_listings WHERE source = ?"
        params: list[Any] = [source]
        if league:
            sql += " AND league = ?"
            params.append(league)
        removed = self.conn.execute(sql, params).rowcount
        self.conn.commit()
        return removed

    def listing_rows(
        self,
        league: str | None = None,
        include_outliers: bool = False,
        max_age_days: float | None = None,
        map_tier: int | None = None,
        require_uncorrupted: bool | None = None,
        columns: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Listings matching the scope, oldest first.

        `columns` narrows the SELECT. It is not a micro-optimisation: `raw_data`
        holds the whole trade payload and averages ~5KB a row, so `SELECT *`
        over a 7k-row table costs ~1.5s of pure deserialisation -- eighty times
        the 19ms the same query takes without it. Every caller that only wants
        to score or compare listings should pass `PRICING_COLUMNS`; the ones
        that re-export or re-parse the payload are the only ones that need `*`.
        """
        selected = "*" if columns is None else ", ".join(columns)
        sql = f"SELECT {selected} FROM originator_listings WHERE 1=1"
        params: list[Any] = []
        if league:
            sql += " AND league = ?"
            params.append(league)
        if not include_outliers:
            sql += " AND is_outlier = 0"
        if map_tier is not None:
            sql += " AND map_tier = ?"
            params.append(map_tier)
        if require_uncorrupted:
            sql += " AND corrupted = 0"
        if max_age_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            sql += " AND COALESCE(indexed_at, captured_at) >= ?"
            params.append(_iso(cutoff))
        sql += " ORDER BY COALESCE(indexed_at, captured_at) ASC"
        return self.conn.execute(sql, params).fetchall()

    def known_map_names(self, league: str | None = None) -> list[str]:
        """Every rare map name this app has ever seen, listed or held.

        The highlight pattern needs this because the tab it is built against is
        never guaranteed to be the tab being searched. The app only knows the
        maps it imported; the stash holds whatever is actually in there, and a
        fragment that is unambiguous among the imported maps will happily light
        up one that was not.

        The rare name pool is small enough to make this practical -- a hundred
        or so first words against fifty last ones -- so checking a fragment
        against every name ever seen is close to checking it against every name
        that could exist.
        """
        names: set[str] = set()
        sql = (
            "SELECT DISTINCT name FROM originator_listings "
            "WHERE name IS NOT NULL AND name != ''"
        )
        params: list[Any] = []
        if league:
            sql += " AND league = ?"
            params.append(league)
        for row in self.conn.execute(sql, params):
            if row[0]:
                names.add(str(row[0]).strip())
        for row in self.conn.execute(
            "SELECT DISTINCT name FROM stash_items WHERE name IS NOT NULL AND name != ''"
        ):
            if row[0]:
                names.add(str(row[0]).strip())
        names.discard("")
        return sorted(names)

    def count_listings(self, league: str | None = None) -> dict[str, int]:
        where = "WHERE (? IS NULL OR league = ?)"
        params: list[Any] = [league, league]
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM originator_listings {where}", params
        ).fetchone()["c"]
        outliers = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM originator_listings {where} AND is_outlier = 1",
            params,
        ).fetchone()["c"]
        today = self.conn.execute(
            "SELECT COUNT(*) AS c FROM originator_listings WHERE captured_at >= ?",
            (_iso(datetime.now(timezone.utc) - timedelta(days=1)),),
        ).fetchone()["c"]
        return {"total": total, "outliers": outliers, "last_24h": today}

    def usable_prices(self, league: str | None = None) -> list[float]:
        """Chaos prices of every row a training run would consider.

        Deliberately just the prices: the caller decides which of them get
        fitted, so the dashboard and the trainer can answer "how many rows is
        this model built on" with the same rule instead of two that drift.
        """
        rows = self.conn.execute(
            "SELECT price_chaos FROM originator_listings "
            "WHERE is_outlier = 0 AND price_chaos IS NOT NULL AND price_chaos > 0 "
            "AND (? IS NULL OR league = ?)",
            (league, league),
        ).fetchall()
        return [float(row["price_chaos"]) for row in rows]

    def latest_capture(self) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(captured_at) AS m FROM originator_listings"
        ).fetchone()
        return _parse_iso(row["m"] if row else None)

    # -- small persisted settings ---------------------------------------
    def set_setting(self, key: str, value: float) -> None:
        """Persist a fetched fact that should outlive the session."""
        self.conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, float(value), _iso(datetime.now(timezone.utc))),
        )
        self.conn.commit()

    def get_setting(self, key: str, default: float | None = None) -> float | None:
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return float(row["value"]) if row else default

    def get_setting_time(self, key: str) -> datetime | None:
        """When a setting was last written.

        A fetched rate that is three days stale and one fetched a minute ago are
        worth different amounts of trust, and the value alone cannot say which
        it is -- the divine rate beside it has always shown its timestamp.
        """
        row = self.conn.execute(
            "SELECT updated_at FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return _parse_iso(row["updated_at"]) if row and row["updated_at"] else None

    def flag_outliers(self, flags: Sequence[tuple[int, bool, str]]) -> int:
        cur = self.conn.cursor()
        for listing_id, is_outlier, reason in flags:
            cur.execute(
                "UPDATE originator_listings SET is_outlier = ?, outlier_reason = ? WHERE id = ?",
                (int(is_outlier), reason, listing_id),
            )
        self.conn.commit()
        return len(flags)

    def recompute_price_chaos(self, divine_to_chaos: float) -> int:
        """Re-derive price_chaos for every listing after a rate change."""
        from src.pricing.currency import CurrencyConverter

        conv = CurrencyConverter(divine_to_chaos)
        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT id, price_amount, price_currency FROM originator_listings"
        ).fetchall()
        n = 0
        for row in rows:
            chaos = conv.to_chaos(row["price_amount"], row["price_currency"])
            cur.execute(
                "UPDATE originator_listings SET price_chaos = ? WHERE id = ?",
                (chaos, row["id"]),
            )
            n += 1
        self.conn.commit()
        return n

    # ------------------------------------------------------------------
    # Currency rates
    # ------------------------------------------------------------------
    def set_divine_rate(self, league: str, rate: float, source: str = "manual") -> None:
        self.conn.execute(
            "INSERT INTO currency_rates (timestamp, league, divine_to_chaos, source) "
            "VALUES (?,?,?,?)",
            (_iso(datetime.now(timezone.utc)), league, float(rate), source),
        )
        self.conn.commit()

    def get_divine_rate(self, league: str) -> tuple[float, datetime] | None:
        row = self.conn.execute(
            "SELECT divine_to_chaos, timestamp FROM currency_rates WHERE league = ? "
            "ORDER BY id DESC LIMIT 1",
            (league,),
        ).fetchone()
        if not row:
            return None
        return float(row["divine_to_chaos"]), _parse_iso(row["timestamp"])

    # ------------------------------------------------------------------
    # Stash snapshots
    # ------------------------------------------------------------------
    def save_snapshot(
        self,
        league: str,
        items: Sequence[NormalizedMap],
        account: str | None = None,
        source: str = "unknown",
    ) -> int:
        tab_ids = sorted({i.stash_tab_id for i in items if i.stash_tab_id})
        tab_names = sorted({i.stash_tab_name for i in items if i.stash_tab_name})
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO stash_snapshots (created_at, league, account, tab_ids, tab_names, "
            "source, item_count) VALUES (?,?,?,?,?,?,?)",
            (
                _iso(datetime.now(timezone.utc)),
                league,
                account,
                json.dumps(tab_ids),
                json.dumps(tab_names),
                source,
                len(items),
            ),
        )
        snapshot_id = cur.lastrowid
        for item in items:
            cur.execute(
                """
                INSERT INTO stash_items (
                    snapshot_id, stash_item_id, stash_tab_id, stash_tab_name,
                    x, y, width, height, name, base_type, map_tier, item_level,
                    quality, corrupted, item_rarity_frame, is_originator,
                    quantity, rarity, pack_size, more_currency, more_maps,
                    more_scarabs, more_divination_cards,
                    supported, unsupported_reason, raw_modifiers, raw_item_data
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id,
                    item.stash_item_id,
                    item.stash_tab_id,
                    item.stash_tab_name,
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    item.name,
                    item.base_type,
                    item.map_tier,
                    item.item_level,
                    item.quality,
                    int(item.corrupted),
                    item.item_rarity_frame,
                    int(item.is_originator),
                    item.quantity,
                    item.rarity,
                    item.pack_size,
                    item.more_currency,
                    item.more_maps,
                    item.more_scarabs,
                    item.more_divination_cards,
                    int(item.supported),
                    item.unsupported_reason,
                    json.dumps(item.raw_modifiers, ensure_ascii=False),
                    json.dumps(item.raw_item_data, ensure_ascii=False),
                ),
            )
        self.conn.commit()
        return snapshot_id

    def list_snapshots(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM stash_snapshots ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def load_snapshot_items(self, snapshot_id: int) -> list[NormalizedMap]:
        rows = self.conn.execute(
            "SELECT * FROM stash_items WHERE snapshot_id = ? ORDER BY y, x", (snapshot_id,)
        ).fetchall()
        items: list[NormalizedMap] = []
        for row in rows:
            items.append(
                NormalizedMap(
                    stash_item_id=row["stash_item_id"],
                    stash_tab_id=row["stash_tab_id"],
                    stash_tab_name=row["stash_tab_name"],
                    x=row["x"],
                    y=row["y"],
                    width=row["width"],
                    height=row["height"],
                    name=row["name"] or "",
                    base_type=row["base_type"] or "",
                    map_tier=row["map_tier"],
                    item_level=row["item_level"],
                    quality=row["quality"] or 0,
                    corrupted=bool(row["corrupted"]),
                    item_rarity_frame=row["item_rarity_frame"] or "",
                    is_originator=bool(row["is_originator"]),
                    supported=bool(row["supported"]),
                    unsupported_reason=row["unsupported_reason"] or "",
                    raw_modifiers=json.loads(row["raw_modifiers"] or "[]"),
                    raw_item_data=json.loads(row["raw_item_data"] or "{}"),
                    **{name: row[name] or 0 for name in FEATURE_NAMES},
                )
            )
        return items

    # ------------------------------------------------------------------
    # Model runs and prediction history
    # ------------------------------------------------------------------
    def record_model_run(self, meta: dict[str, Any], artifact_path: str) -> int:
        cur = self.conn.cursor()
        cur.execute("UPDATE model_runs SET is_active = 0")
        cur.execute(
            "INSERT INTO model_runs (trained_at, model_version, backend, target, "
            "dataset_size, feature_list, metrics, market_date_start, market_date_end, "
            "artifact_path, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (
                meta["trained_at"],
                meta["model_version"],
                meta["backend"],
                meta["target"],
                meta["dataset_size"],
                json.dumps(meta["feature_list"]),
                json.dumps(meta["metrics"]),
                meta.get("market_date_start"),
                meta.get("market_date_end"),
                artifact_path,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def active_model_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM model_runs WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def model_runs(self, limit: int = 25) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM model_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def save_predictions(self, snapshot_id: int | None, rows: Iterable[dict[str, Any]]) -> int:
        cur = self.conn.cursor()
        n = 0
        now = _iso(datetime.now(timezone.utc))
        for row in rows:
            cur.execute(
                "INSERT INTO predictions (created_at, snapshot_id, item_fingerprint, "
                "features, predicted_chaos, suggested_chaos, interval_low_chaos, "
                "interval_high_chaos, confidence, model_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    snapshot_id,
                    row["item_fingerprint"],
                    json.dumps(row["features"]),
                    row.get("predicted_chaos"),
                    row.get("suggested_chaos"),
                    row.get("interval_low_chaos"),
                    row.get("interval_high_chaos"),
                    row.get("confidence"),
                    row.get("model_version"),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def snapshot_predictions(self, snapshot_id: int) -> list[sqlite3.Row]:
        """Prices recorded when a snapshot was taken, for then-vs-now comparison."""
        return self.conn.execute(
            "SELECT item_fingerprint, predicted_chaos, suggested_chaos, confidence, "
            "model_version, created_at FROM predictions WHERE snapshot_id = ? "
            "ORDER BY id ASC",
            (snapshot_id,),
        ).fetchall()

    # ------------------------------------------------------------------
    # Listing tracker
    # ------------------------------------------------------------------
    def _tracked_row(self, row: sqlite3.Row) -> "TrackedRow":
        from src.tracking.reconcile import TrackedRow

        return TrackedRow(
            id=row["id"],
            trade_listing_id=row["trade_listing_id"],
            fingerprint=row["fingerprint"],
            price_chaos=row["price_chaos"],
            priced_at=_parse_iso(row["priced_at"]),
            first_seen=_parse_iso(row["first_seen"]),
            last_seen=_parse_iso(row["last_seen"]),
            status=row["status"],
            gone_first_missing=_parse_iso(row["gone_first_missing"]),
            ambiguous=bool(row["ambiguous"]),
            exclude_from_learning=bool(row["exclude_from_learning"]),
        )

    def tracking_version(self) -> int:
        """Highest event id, as a cheap "has anything been polled since?" stamp.

        The multiplier cache is keyed on this. Listing writes bump a session
        counter, but the poller runs on a background thread and must not touch
        Streamlit state, so the marker has to live in the database.
        """
        row = self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM tracked_listing_events"
        ).fetchone()
        return int(row[0]) if row else 0

    def live_tracked_rows(self, account: str, league: str) -> list["TrackedRow"]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_listings WHERE account = ? AND league = ? "
            "AND status = 'live' ORDER BY priced_at",
            (account, league),
        ).fetchall()
        return [self._tracked_row(r) for r in rows]

    def recent_gone_rows(
        self, account: str, league: str, since: datetime
    ) -> list["TrackedRow"]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_listings WHERE account = ? AND league = ? "
            "AND status = 'gone' AND gone_first_missing >= ? "
            "ORDER BY gone_first_missing DESC",
            (account, league, _iso(since)),
        ).fetchall()
        return [self._tracked_row(r) for r in rows]

    @staticmethod
    def _tracked_insert_params(account: str, league: str, row) -> tuple:
        item = row.listing.item if row.listing else None
        listing = row.listing
        # listing.stash is where GGG says the item physically is. It rides in
        # raw_data because MarketListing has no field for it -- it is of no
        # interest to the market collector, and of every interest here, because
        # it is what lets the overlay draw on a listed map without a stash read.
        raw = getattr(listing, "raw_data", {}) or {}
        stash = (raw.get("listing") or {}).get("stash") or {}
        return (
            account,
            league,
            row.trade_listing_id,
            row.fingerprint,
            getattr(item, "name", None),
            getattr(item, "base_type", None),
            getattr(item, "map_tier", None),
            getattr(item, "item_level", None),
            int(getattr(item, "corrupted", False) or False),
            getattr(item, "quantity", 0),
            getattr(item, "rarity", 0),
            getattr(item, "pack_size", 0),
            getattr(item, "more_currency", 0),
            getattr(item, "more_maps", 0),
            getattr(item, "more_scarabs", 0),
            getattr(item, "more_divination_cards", 0),
            getattr(listing, "price_amount", None),
            getattr(listing, "price_currency", None),
            row.price_chaos,
            getattr(listing, "price_type", None),
            stash.get("name") or None,
            stash.get("x"),
            stash.get("y"),
            _iso(getattr(listing, "indexed_at", None)),
            _iso(row.priced_at),
            _iso(row.first_seen),
            _iso(row.last_seen),
            "live",
            int(row.ambiguous),
            json.dumps(raw),
        )

    def apply_reconciliation(self, account: str, league: str, result) -> dict[str, int]:
        """Write one reconciled poll: rows first, then the events that explain them.

        Events carry a listing id, so a freshly inserted row has to exist before
        its own `appeared` event can point at it. Hence two passes rather than
        one, and a fingerprint-to-id map to bridge them.
        """
        inserted = updated = 0
        by_fingerprint: dict[str, int] = {}

        for row in result.live:
            if row.id is None:
                cur = self.conn.execute(
                    "INSERT INTO tracked_listings (account, league, trade_listing_id, "
                    "fingerprint, name, base_type, map_tier, item_level, corrupted, "
                    "quantity, rarity, pack_size, more_currency, more_maps, "
                    "more_scarabs, more_divination_cards, price_amount, "
                    "price_currency, price_chaos, price_type, stash_name, stash_x, "
                    "stash_y, indexed_at, priced_at, first_seen, last_seen, status, "
                    "ambiguous, raw_data) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    self._tracked_insert_params(account, league, row),
                )
                by_fingerprint[row.fingerprint] = int(cur.lastrowid)
                inserted += 1
            else:
                self.conn.execute(
                    "UPDATE tracked_listings SET trade_listing_id = ?, "
                    "price_amount = ?, price_currency = ?, price_chaos = ?, "
                    "priced_at = ?, last_seen = ?, status = 'live', "
                    "gone_first_missing = NULL, ambiguous = ? WHERE id = ?",
                    (
                        row.trade_listing_id,
                        row.listing.price_amount if row.listing else None,
                        row.listing.price_currency if row.listing else None,
                        row.price_chaos,
                        _iso(row.priced_at),
                        _iso(row.last_seen),
                        int(row.ambiguous),
                        row.id,
                    ),
                )
                by_fingerprint[row.fingerprint] = row.id
                updated += 1

        for row in result.gone:
            self.conn.execute(
                "UPDATE tracked_listings SET status = 'gone', gone_first_missing = ? "
                "WHERE id = ?",
                (_iso(row.gone_first_missing), row.id),
            )
            by_fingerprint[row.fingerprint] = row.id

        for event in result.events:
            listing_id = event.row_id or by_fingerprint.get(event.fingerprint)
            if listing_id is None:
                continue
            self.conn.execute(
                "INSERT INTO tracked_listing_events (listing_id, at, event, "
                "old_price_chaos, new_price_chaos) VALUES (?,?,?,?,?)",
                (
                    listing_id,
                    _iso(event.at),
                    event.event,
                    event.old_price_chaos,
                    event.new_price_chaos,
                ),
            )

        self.conn.commit()
        return {"inserted": inserted, "updated": updated, "gone": len(result.gone)}

    def record_poll_run(
        self,
        started_at: datetime,
        finished_at: datetime | None,
        league: str,
        account: str,
        listings_seen: int,
        total_reported: int,
        complete: bool,
        error: str = "",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO listing_poll_runs (started_at, finished_at, league, account, "
            "listings_seen, total_reported, complete, error) VALUES (?,?,?,?,?,?,?,?)",
            (
                _iso(started_at),
                _iso(finished_at),
                league,
                account,
                listings_seen,
                total_reported,
                int(complete),
                error,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def last_poll_run(self, account: str, league: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM listing_poll_runs WHERE account = ? AND league = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (account, league),
        ).fetchone()

    def tracked_rows_for_page(
        self, account: str, league: str, status: str = "live"
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tracked_listings WHERE account = ? AND league = ? "
            "AND status = ? ORDER BY priced_at",
            (account, league, status),
        ).fetchall()

    def tracked_events(self, listing_ids: Sequence[int]) -> list[sqlite3.Row]:
        if not listing_ids:
            return []
        marks = ",".join("?" for _ in listing_ids)
        return self.conn.execute(
            f"SELECT * FROM tracked_listing_events WHERE listing_id IN ({marks}) "
            "ORDER BY listing_id, at, id",
            tuple(listing_ids),
        ).fetchall()

    def set_exclude_from_learning(self, listing_id: int, excluded: bool) -> None:
        self.conn.execute(
            "UPDATE tracked_listings SET exclude_from_learning = ? WHERE id = ?",
            (int(excluded), listing_id),
        )
        self.conn.commit()

    def save_tracked_predictions(self, rows: Sequence[dict[str, Any]]) -> int:
        """Store the model's number alongside the ask, so the page can compare."""
        for row in rows:
            self.conn.execute(
                "UPDATE tracked_listings SET predicted_chaos = ?, suggested_chaos = ?, "
                "interval_low_chaos = ?, interval_high_chaos = ?, model_version = ? "
                "WHERE id = ?",
                (
                    row["predicted_chaos"],
                    row["suggested_chaos"],
                    row["interval_low_chaos"],
                    row["interval_high_chaos"],
                    row["model_version"],
                    row["id"],
                ),
            )
        self.conn.commit()
        return len(rows)
