# Listing Tracker (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poll the account's own instant-buyout listings, record re-prices and disappearances in an event log, and ship a My Listings page that shows every map still sitting in the store with a stash-search regex, an overlay export, and a recommended lower price.

**Architecture:** A worker thread (modelled on `src/market/run_job.py`) calls an account-filtered trade search every N minutes and hands the snapshot to a pure reconciler that emits events. Everything with a judgement in it — identity matching, disappearance handling, the reprice ladder — is a pure function taking plain dataclasses, so it is tested without a network, a database or a browser. Only the collector and the poller do I/O.

**Tech Stack:** Python 3.11+, sqlite3, requests, Streamlit, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-28-listing-tracker-design.md`

**Follow-on plan:** `docs/superpowers/plans/2026-08-28-listing-tracker-learning.md` replaces this plan's fallback reprice ladder with a fit derived from censored data, and adds the personal calibration factor. This plan is complete and useful without it.

## Global Constraints

- Scope is T16, rare, uncorrupted Originator Maps only — `cfg.scope.map_tier`, `cfg.scope.require_uncorrupted`. The model prices nothing else.
- `gone` means **sold**. There is no sold/delisted workflow; `exclude_from_learning` on the row is the only escape hatch.
- The trade query MUST carry `collapse: false`, `status: "any"`, and no `sale_type` key. Spec §2.
- A poll that cannot enumerate every listing is `complete = 0`, and **disappearance handling is skipped entirely for that pass**. Spec §2.
- Never store a point estimate of time-to-sell. Disappearance is a bracket `[last_seen, gone_first_missing]`. Spec §4.
- No new entries in `requirements.txt`.
- Migrations are append-only: add entry `3` to `MIGRATIONS`, never edit `1` or `2`. See the comment at the top of `src/database/models.py`.
- All new pure modules live in `src/tracking/` and import neither `streamlit` nor `sqlite3`.
- Times are timezone-aware UTC (`datetime.now(timezone.utc)`), stored as ISO strings, matching `src/database/repository.py`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/database/models.py` | **Modify** — migration `3`: the three tracker tables |
| `src/database/repository.py` | **Modify** — tracker read/write methods |
| `src/market/collectors/official_trade_api.py` | **Modify** — promote `_to_listing` to public `listing_from_result` |
| `src/market/my_listings.py` | **Create** — account-filtered search/fetch → `list[MarketListing]` |
| `src/tracking/__init__.py` | **Create** — empty package marker |
| `src/tracking/reconcile.py` | **Create** — pure: previous rows + snapshot → events |
| `src/tracking/reprice.py` | **Create** — pure: stale row → recommended price (ladder; the fit arrives in the follow-on plan) |
| `src/tracking/poller.py` | **Create** — the worker thread and its cadence |
| `src/ui/my_listings.py` | **Create** — the page |
| `app.py` | **Modify** — register the page |
| `src/config.py` | **Modify** — `listing_tracker` defaults |
| `config.example.yaml`, `config.yaml` | **Modify** — the new block |
| `docs/data-access.md` | **Modify** — §3's "no continuous background polling" is no longer true |

---

### Task 1: Migration 3 — the tracker tables

**Files:**
- Modify: `src/database/models.py` (append to `MIGRATIONS`)
- Test: `tests/test_tracker_schema.py`

**Interfaces:**
- Consumes: `init_db(path)` from `src/database/models.py`
- Produces: tables `tracked_listings`, `tracked_listing_events`, `listing_poll_runs`

- [ ] **Step 1: Write the failing test**

```python
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
    assert {"id", "listing_id", "at", "event", "old_price_chaos", "new_price_chaos"} == _columns(
        conn, "tracked_listing_events"
    )
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker_schema.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: tracked_listings`

- [ ] **Step 3: Append migration 3**

Add to the end of the `MIGRATIONS` list in `src/database/models.py`, after entry `(2, ...)`:

```python
    (
        3,
        """
        -- The account's own listings, tracked over time. One row per map, not
        -- per price: a re-price mutates priced_at here and appends an event
        -- below, so a map that wore three prices is one row and three episodes.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracker_schema.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite — migrations touch every test that opens a database**

Run: `python -m pytest -q`
Expected: no new failures against the pre-task baseline

- [ ] **Step 6: Commit**

```bash
git add src/database/models.py tests/test_tracker_schema.py
git commit -m "Add migration 3: listing tracker tables"
```

---

### Task 2: The reconciler

This is the heart of the feature and the part most worth getting right before anything is built on it. It is pure: no database, no network, no clock of its own.

**Files:**
- Create: `src/tracking/__init__.py`, `src/tracking/reconcile.py`
- Test: `tests/test_tracking_reconcile.py`

**Interfaces:**
- Consumes: `MarketListing`, `NormalizedMap` from `src/schema.py`; `NormalizedMap.fingerprint()`
- Produces:
  - `TrackedRow(id, trade_listing_id, fingerprint, price_chaos, priced_at, first_seen, last_seen, status, gone_first_missing, ambiguous, listing=None)`
  - `Event(fingerprint, trade_listing_id, at, event, old_price_chaos, new_price_chaos, row_id)`
  - `Reconciliation(events, live, gone, ambiguous, skipped_disappearance)`
  - `reconcile(previous, snapshot, now, complete, reappearance_window_hours=6.0) -> Reconciliation`

- [ ] **Step 1: Write the failing test**

```python
"""The reconciler: what one poll means for what we already knew."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.schema import MarketListing, NormalizedMap
from src.tracking.reconcile import TrackedRow, reconcile

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _map(name="Glyph Solace", quantity=90):
    return NormalizedMap(
        name=name, base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=quantity, rarity=140,
        pack_size=30, more_currency=47,
    )


def _listing(name="Glyph Solace", price=120.0, listing_id="abc", quantity=90):
    item = _map(name, quantity)
    return MarketListing(
        item=item, listing_id=listing_id, league="Allflame",
        price_amount=price, price_currency="chaos", price_chaos=price,
        price_type="~b/o", seller="me",
    )


def _row(fingerprint, price=120.0, listing_id="abc", at=T0, status="live"):
    return TrackedRow(
        id=1, trade_listing_id=listing_id, fingerprint=fingerprint,
        price_chaos=price, priced_at=at, first_seen=at, last_seen=at,
        status=status,
    )


def test_new_listing_appears():
    result = reconcile([], [_listing()], now=T0, complete=True)
    assert [e.event for e in result.events] == ["appeared"]
    assert len(result.live) == 1
    assert result.live[0].priced_at == T0


def test_unchanged_listing_only_refreshes_last_seen():
    row = _row(_map().fingerprint())
    later = T0 + timedelta(minutes=15)
    result = reconcile([row], [_listing()], now=later, complete=True)
    assert result.events == ()
    assert result.live[0].last_seen == later
    assert result.live[0].priced_at == T0


def test_reprice_emits_event_and_restarts_the_clock():
    row = _row(_map().fingerprint(), price=180.0)
    later = T0 + timedelta(hours=3)
    result = reconcile([row], [_listing(price=120.0)], now=later, complete=True)
    assert [e.event for e in result.events] == ["repriced"]
    assert result.events[0].old_price_chaos == 180.0
    assert result.events[0].new_price_chaos == 120.0
    assert result.live[0].priced_at == later, "the clock restarts at the new price"


def test_vanished_listing_is_gone_and_brackets_the_sale():
    row = _row(_map().fingerprint())
    row = row.replace(last_seen=T0 + timedelta(minutes=20))
    later = T0 + timedelta(hours=9)
    result = reconcile([row], [], now=later, complete=True)
    assert [e.event for e in result.events] == ["gone"]
    gone = result.gone[0]
    assert gone.status == "gone"
    assert gone.last_seen == T0 + timedelta(minutes=20), "lower bound is untouched"
    assert gone.gone_first_missing == later, "upper bound is this poll"


def test_partial_poll_never_marks_anything_gone():
    row = _row(_map().fingerprint())
    result = reconcile([row], [], now=T0 + timedelta(hours=1), complete=False)
    assert result.events == ()
    assert result.gone == ()
    assert result.skipped_disappearance is True


def test_id_change_with_matching_fingerprint_is_a_reprice_not_a_sale():
    row = _row(_map().fingerprint(), price=180.0, listing_id="old")
    later = T0 + timedelta(hours=1)
    result = reconcile([row], [_listing(price=120.0, listing_id="new")], now=later, complete=True)
    assert [e.event for e in result.events] == ["repriced"]
    assert result.live[0].trade_listing_id == "new"
    assert result.gone == ()


def test_reappearance_inside_the_window_undoes_the_sale():
    fp = _map().fingerprint()
    row = TrackedRow(
        id=1, trade_listing_id="abc", fingerprint=fp, price_chaos=120.0,
        priced_at=T0, first_seen=T0, last_seen=T0, status="gone",
        gone_first_missing=T0 + timedelta(hours=1),
    )
    result = reconcile([row], [_listing()], now=T0 + timedelta(hours=2), complete=True)
    assert [e.event for e in result.events] == ["reappeared"]
    assert result.live[0].status == "live"
    assert result.live[0].gone_first_missing is None, "it was never sold"


def test_reappearance_outside_the_window_is_a_different_map():
    fp = _map().fingerprint()
    row = TrackedRow(
        id=1, trade_listing_id="abc", fingerprint=fp, price_chaos=120.0,
        priced_at=T0, first_seen=T0, last_seen=T0, status="gone",
        gone_first_missing=T0 + timedelta(hours=1),
    )
    result = reconcile(
        [row], [_listing()], now=T0 + timedelta(days=3), complete=True,
        reappearance_window_hours=6.0,
    )
    assert [e.event for e in result.events] == ["appeared"]
    assert result.live[0].id is None, "a new row, not the sold one resurrected"


def test_two_identical_live_maps_are_flagged_not_guessed():
    a = _listing(listing_id="a")
    b = _listing(listing_id="b")
    result = reconcile([], [a, b], now=T0, complete=True)
    assert len(result.live) == 2
    assert all(r.ambiguous for r in result.live)
    assert result.ambiguous == (_map().fingerprint(),)


def test_distinct_maps_are_not_ambiguous():
    result = reconcile(
        [], [_listing(listing_id="a"), _listing(listing_id="b", quantity=70)],
        now=T0, complete=True,
    )
    assert not any(r.ambiguous for r in result.live)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking'`

- [ ] **Step 3: Write the implementation**

Create `src/tracking/__init__.py` empty, then `src/tracking/reconcile.py`:

```python
"""What one poll of your own listings means for what we already knew.

Pure. No database, no network, no clock of its own -- `now` is passed in, so a
sequence of polls can be replayed in a test in microseconds and a bug in the
disappearance rules is reproducible rather than a thing that happened overnight.

The rules that carry the weight:

* **A re-price restarts the clock.** Selling ten minutes after a markdown is a
  ten-minute sale at the new price, not a three-day sale at the old one. So a
  price change moves `priced_at` and every duration downstream measures from
  there.
* **A partial poll changes nothing.** A listing missing because it fell outside
  a truncated result window is indistinguishable from one that sold, and
  counting it as a sale would inject a fake fast sale into the data the
  calibration is fitted on. Better to learn nothing this pass.
* **A disappearance is bracketed, never timed.** `last_seen` is the last moment
  it was there, `gone_first_missing` the first it was not. The truth is
  somewhere between, and nothing here pretends to know where.
* **Identity is a judgement, so it lives here** rather than in a unique index.
  Trade ids are matched first; a changed id with a matching reward fingerprint
  is the same map re-listed, not a sale and a new listing. Two live maps that
  cannot be told apart are flagged, not guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Sequence

from src.schema import MarketListing

APPEARED = "appeared"
REPRICED = "repriced"
GONE = "gone"
REAPPEARED = "reappeared"


@dataclass(frozen=True)
class TrackedRow:
    """A tracked listing reduced to what reconciling needs to decide."""

    id: int | None
    trade_listing_id: str | None
    fingerprint: str
    price_chaos: float | None
    priced_at: datetime
    first_seen: datetime
    last_seen: datetime
    status: str = "live"
    gone_first_missing: datetime | None = None
    ambiguous: bool = False
    #: The snapshot row this was matched to, when there was one. The writer
    #: needs the item to fill in name and rewards on an insert; reconciling
    #: itself never reads it.
    listing: MarketListing | None = None

    def replace(self, **changes) -> "TrackedRow":
        return replace(self, **changes)


@dataclass(frozen=True)
class Event:
    fingerprint: str
    trade_listing_id: str | None
    at: datetime
    event: str
    old_price_chaos: float | None = None
    new_price_chaos: float | None = None
    row_id: int | None = None


@dataclass(frozen=True)
class Reconciliation:
    events: tuple[Event, ...] = ()
    live: tuple[TrackedRow, ...] = ()
    gone: tuple[TrackedRow, ...] = ()
    ambiguous: tuple[str, ...] = ()
    skipped_disappearance: bool = False


def _same_price(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) < 1e-6


def reconcile(
    previous: Sequence[TrackedRow],
    snapshot: Sequence[MarketListing],
    now: datetime,
    complete: bool,
    reappearance_window_hours: float = 6.0,
) -> Reconciliation:
    """Fold one snapshot into the rows we already hold."""
    events: list[Event] = []
    live: list[TrackedRow] = []
    gone: list[TrackedRow] = []

    by_id = {r.trade_listing_id: r for r in previous if r.trade_listing_id and r.status == "live"}
    unmatched_live = {r.fingerprint: r for r in previous if r.status == "live"}
    recently_gone = {
        r.fingerprint: r
        for r in previous
        if r.status == "gone"
        and r.gone_first_missing is not None
        and now - r.gone_first_missing <= timedelta(hours=reappearance_window_hours)
    }

    # Fingerprints appearing more than once in this snapshot cannot be told
    # apart from each other. Flag them; never guess which is which.
    seen: dict[str, int] = {}
    for listing in snapshot:
        fp = listing.item.fingerprint()
        seen[fp] = seen.get(fp, 0) + 1
    duplicated = {fp for fp, n in seen.items() if n > 1}

    matched_rows: set[int] = set()

    for listing in snapshot:
        fp = listing.item.fingerprint()
        ambiguous = fp in duplicated
        row = by_id.get(listing.listing_id)
        if row is None and not ambiguous:
            row = unmatched_live.get(fp)
        if row is not None and row.id in matched_rows:
            row = None

        if row is not None:
            if row.id is not None:
                matched_rows.add(row.id)
            if _same_price(row.price_chaos, listing.price_chaos):
                live.append(
                    row.replace(
                        last_seen=now,
                        trade_listing_id=listing.listing_id or row.trade_listing_id,
                        ambiguous=ambiguous or row.ambiguous,
                        listing=listing,
                    )
                )
                continue
            events.append(
                Event(
                    fingerprint=fp,
                    trade_listing_id=listing.listing_id,
                    at=now,
                    event=REPRICED,
                    old_price_chaos=row.price_chaos,
                    new_price_chaos=listing.price_chaos,
                    row_id=row.id,
                )
            )
            live.append(
                row.replace(
                    trade_listing_id=listing.listing_id or row.trade_listing_id,
                    price_chaos=listing.price_chaos,
                    priced_at=now,
                    last_seen=now,
                    ambiguous=ambiguous or row.ambiguous,
                    listing=listing,
                )
            )
            continue

        # Not a live row. It may be one we called sold a moment ago -- a sold
        # map cannot come back, so a quick return means it was never sold.
        revived = recently_gone.get(fp) if not ambiguous else None
        if revived is not None and revived.id not in matched_rows:
            if revived.id is not None:
                matched_rows.add(revived.id)
            events.append(
                Event(
                    fingerprint=fp,
                    trade_listing_id=listing.listing_id,
                    at=now,
                    event=REAPPEARED,
                    row_id=revived.id,
                )
            )
            live.append(
                revived.replace(
                    trade_listing_id=listing.listing_id,
                    price_chaos=listing.price_chaos,
                    last_seen=now,
                    status="live",
                    gone_first_missing=None,
                    listing=listing,
                )
            )
            continue

        events.append(
            Event(
                fingerprint=fp,
                trade_listing_id=listing.listing_id,
                at=now,
                event=APPEARED,
                new_price_chaos=listing.price_chaos,
            )
        )
        live.append(
            TrackedRow(
                id=None,
                trade_listing_id=listing.listing_id,
                fingerprint=fp,
                price_chaos=listing.price_chaos,
                priced_at=listing.indexed_at or now,
                first_seen=now,
                last_seen=now,
                status="live",
                ambiguous=ambiguous,
                listing=listing,
            )
        )

    if not complete:
        # Say nothing about what is missing: we do not know that it is missing.
        return Reconciliation(
            events=tuple(events),
            live=tuple(live),
            gone=(),
            ambiguous=tuple(sorted(duplicated)),
            skipped_disappearance=True,
        )

    for row in previous:
        if row.status != "live" or row.id in matched_rows:
            continue
        events.append(
            Event(
                fingerprint=row.fingerprint,
                trade_listing_id=row.trade_listing_id,
                at=now,
                event=GONE,
                old_price_chaos=row.price_chaos,
                row_id=row.id,
            )
        )
        gone.append(row.replace(status="gone", gone_first_missing=now))

    return Reconciliation(
        events=tuple(events),
        live=tuple(live),
        gone=tuple(gone),
        ambiguous=tuple(sorted(duplicated)),
        skipped_disappearance=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_reconcile.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/tracking/__init__.py src/tracking/reconcile.py tests/test_tracking_reconcile.py
git commit -m "Add listing reconciler: events from one poll of your own listings"
```

---

### Task 3: The account-filtered collector

**Files:**
- Modify: `src/market/collectors/official_trade_api.py` (rename `_to_listing` → `listing_from_result`)
- Create: `src/market/my_listings.py`
- Test: `tests/test_my_listings.py`

**Interfaces:**
- Consumes: `OfficialTradeApiCollector.build_query`, `.search`, `.fetch`, `.listing_from_result`
- Produces: `MyListingsSnapshot(listings, total_reported, complete, error)`; `fetch_my_listings(collector, account, max_ids=100) -> MyListingsSnapshot`

- [ ] **Step 1: Write the failing test**

```python
"""Reading your own listings: the query shape, and refusing to guess when truncated."""
from __future__ import annotations

from typing import Any

import pytest

from src.market.my_listings import fetch_my_listings
from src.market.collectors.official_trade_api import OfficialTradeApiCollector


class _Converter:
    def to_chaos(self, amount, currency):
        return amount


def _result(listing_id: str, price: float) -> dict[str, Any]:
    return {
        "id": listing_id,
        "listing": {
            "indexed": "2026-08-28T10:00:00Z",
            "account": {"name": "me"},
            "price": {"type": "~b/o", "amount": price, "currency": "chaos"},
        },
        "item": {
            "name": "Glyph Solace",
            "typeLine": "Terror Solace",
            "ilvl": 84,
            "frameType": 2,
            "league": "Allflame",
            "identified": True,
            "properties": [
                {"name": "Map Tier", "values": [["16", 0]]},
                {"name": "Item Quantity", "values": [["+90%", 1]]},
                {"name": "Item Rarity", "values": [["+140%", 1]]},
                {"name": "Monster Pack Size", "values": [["+30%", 1]]},
            ],
            "implicitMods": ["Area is Influenced by the Originator's Memories"],
        },
    }


class _FakeCollector(OfficialTradeApiCollector):
    def __init__(self, ids, total, results):
        super().__init__(league="Allflame", user_agent="test", converter=_Converter())
        self._ids, self._total, self._results = ids, total, results
        self.queries: list[dict] = []

    def search(self, query):
        self.queries.append(query)
        return "qid", list(self._ids), self._total

    def fetch(self, ids, query_id):
        return [self._results[i] for i in ids]


def test_query_carries_the_account_filter_and_no_collapse():
    c = _FakeCollector(["a"], 1, {"a": _result("a", 120.0)})
    fetch_my_listings(c, account="me")
    trade = c.queries[0]["query"]["filters"]["trade_filters"]["filters"]
    assert trade["account"] == {"input": "me"}
    assert "collapse" not in trade, (
        "collapse groups by account -- on, an account search returns one row and "
        "every other map looks sold. In this codebase off means the key is "
        "omitted: build_query emits it only when on, bulk.py relies on that, and "
        "test_live_search_link pins the same convention."
    )
    assert "sale_type" not in trade, "omitted means buyout-or-fixed-price"
    assert c.queries[0]["query"]["status"] == {"option": "any"}


def test_complete_snapshot_returns_every_listing():
    c = _FakeCollector(["a", "b"], 2, {"a": _result("a", 120.0), "b": _result("b", 90.0)})
    snap = fetch_my_listings(c, account="me")
    assert snap.complete is True
    assert snap.total_reported == 2
    assert sorted(l.listing_id for l in snap.listings) == ["a", "b"]


def test_truncated_result_is_marked_incomplete():
    ids = [f"i{n}" for n in range(100)]
    c = _FakeCollector(ids, 250, {i: _result(i, 100.0) for i in ids})
    snap = fetch_my_listings(c, account="me", max_ids=100)
    assert snap.complete is False, "250 listings cannot be enumerated by one search"
    assert snap.total_reported == 250


def test_empty_account_name_refuses_rather_than_polling():
    c = _FakeCollector([], 0, {})
    with pytest.raises(ValueError, match="account"):
        fetch_my_listings(c, account="")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_my_listings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.market.my_listings'`

- [ ] **Step 3: Promote `_to_listing`, then write the collector**

In `src/market/collectors/official_trade_api.py`, rename the method and its single call site:

```python
    def listing_from_result(self, result: dict[str, Any]) -> MarketListing | None:
```

Then update the one internal caller inside `collect` (it currently reads `self._to_listing(result)`) to `self.listing_from_result(result)`.

Create `src/market/my_listings.py`:

```python
"""Your own live instant-buyout listings, read from the trade search.

The trade search takes a "Seller Account" filter, so re-checking everything you
have listed is one search plus a fetch per ten maps -- about twenty requests an
hour on a fifteen-minute cadence, against a sustained policy of thirty searches
per five minutes. It reuses the ordinary collector, its rate limiters and its
parsing; only the completeness rule is really new. The query itself has a
precedent -- `src/market/bulk.py:151` already issues an uncollapsed,
account-scoped, newest-first search for exactly this reason ("a targeted query
is a single search and one or two fetches rather than a sweep of the market").
The difference is that bulk parses results into a thin `BulkListing` for buying
and stores nothing, while the tracker needs the full `NormalizedMap` -- the
reward features are what the fingerprint and the prediction are built from.

Two parameters are not preferences:

`collapse` is off. It means "collapse listings by account", so an
account-filtered search with it on plausibly returns a single row -- and every
other map you own would look sold.

`status` is "any". With "online", logging out is indistinguishable from selling
the whole tab.

The fetch result also carries `listing.stash` -- name, x and y -- so a tracked
listing knows where the map physically sits without a second stash read. It rides
along in `MarketListing.raw_data`, which `listing_from_result` already fills with
the whole listing block, and the repository lifts it out on write.

And one rule matters more than either: **a snapshot that cannot be enumerated is
not a snapshot.** A search returns at most 100 ids, so past that we cannot tell a
map that sold from a map that fell outside the window. The snapshot says so, and
the reconciler declines to mark anything sold from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.market.collectors.official_trade_api import (
    FETCH_BATCH,
    OfficialTradeApiCollector,
)
from src.schema import MarketListing

#: A single trade search returns at most this many ids, however many match.
#: `src/market/bulk.py` already names this for its own account-scoped scan.
from src.market.bulk import SEARCH_PAGE as SEARCH_RESULT_CAP


@dataclass
class MyListingsSnapshot:
    listings: list[MarketListing] = field(default_factory=list)
    total_reported: int = 0
    complete: bool = False
    error: str = ""


def fetch_my_listings(
    collector: OfficialTradeApiCollector,
    account: str,
    max_ids: int = SEARCH_RESULT_CAP,
) -> MyListingsSnapshot:
    """Every Originator Map `account` currently has listed for buyout."""
    if not account:
        raise ValueError("account name is required to read your own listings")

    query = collector.build_query(
        sort_by="indexed",
        sort="desc",
        status="any",
        collapse=False,
        account=account,
    )
    _query_id, ids, total = collector.search(query)

    listings: list[MarketListing] = []
    for start in range(0, min(len(ids), max_ids), FETCH_BATCH):
        batch = ids[start : start + FETCH_BATCH]
        for result in collector.fetch(batch, _query_id):
            listing = collector.listing_from_result(result)
            if listing is not None:
                listings.append(listing)

    return MyListingsSnapshot(
        listings=listings,
        total_reported=total,
        complete=total <= max_ids and len(ids) >= min(total, max_ids),
    )
```

**Do not change `build_query`.** It already accepts `collapse` and `account`, and
it already encodes "uncollapsed" the way this codebase means it: by omitting the
key. `src/market/bulk.py:151` depends on that, and
`tests/test_live_search_link.py::test_the_link_is_uncollapsed_so_a_seller_shows_every_map`
asserts `"collapse" not in trade`. Emitting `{"option": "false"}` instead would
break that test and change the query every other account-scoped caller sends.
Passing `collapse=False` is already correct and sufficient.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_my_listings.py tests/test_market_sampling.py -v`
Expected: all pass. `test_market_sampling.py` is included because the `collapse` change touches the shared query builder.

- [ ] **Step 5: Commit**

```bash
git add src/market/my_listings.py src/market/collectors/official_trade_api.py tests/test_my_listings.py
git commit -m "Read the account's own listings from the trade search"
```

---

### Task 4: Repository methods

**Files:**
- Modify: `src/database/repository.py`
- Test: `tests/test_tracker_repository.py`

**Interfaces:**
- Produces, on `Repository`:
  - `live_tracked_rows(account, league) -> list[TrackedRow]`
  - `recent_gone_rows(account, league, since) -> list[TrackedRow]`
  - `apply_reconciliation(account, league, result) -> dict[str, int]`
  - `record_poll_run(started_at, finished_at, league, account, listings_seen, total_reported, complete, error) -> int`
  - `tracked_rows_for_page(account, league, status="live") -> list[sqlite3.Row]`
  - `tracked_events(listing_ids) -> list[sqlite3.Row]`
  - `set_exclude_from_learning(listing_id, excluded)`
  - `last_poll_run(account, league) -> sqlite3.Row | None`

- [ ] **Step 1: Write the failing test**

```python
"""Persisting what the reconciler decided."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.database.repository import Repository
from src.schema import MarketListing, NormalizedMap
from src.tracking.reconcile import TrackedRow, reconcile

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
    repo.apply_reconciliation("me", "Allflame", reconcile([], [_listing(180.0)], T0, True))

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker_repository.py -v`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'apply_reconciliation'`

- [ ] **Step 3: Implement the methods**

Append to `src/database/repository.py`, inside `class Repository`, following the existing style (`self.conn`, ISO strings, `sqlite3.Row` returns). Add `from src.tracking.reconcile import TrackedRow` to the imports.

```python
    # ------------------------------------------------------------------
    # Listing tracker
    # ------------------------------------------------------------------
    def _tracked_row(self, row: sqlite3.Row) -> TrackedRow:
        return TrackedRow(
            id=row["id"],
            trade_listing_id=row["trade_listing_id"],
            fingerprint=row["fingerprint"],
            price_chaos=row["price_chaos"],
            priced_at=datetime.fromisoformat(row["priced_at"]),
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
            status=row["status"],
            gone_first_missing=(
                datetime.fromisoformat(row["gone_first_missing"])
                if row["gone_first_missing"]
                else None
            ),
            ambiguous=bool(row["ambiguous"]),
        )

    def live_tracked_rows(self, account: str, league: str) -> list[TrackedRow]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_listings WHERE account = ? AND league = ? "
            "AND status = 'live' ORDER BY priced_at",
            (account, league),
        ).fetchall()
        return [self._tracked_row(r) for r in rows]

    def recent_gone_rows(
        self, account: str, league: str, since: datetime
    ) -> list[TrackedRow]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_listings WHERE account = ? AND league = ? "
            "AND status = 'gone' AND gone_first_missing >= ? "
            "ORDER BY gone_first_missing DESC",
            (account, league, since.isoformat()),
        ).fetchall()
        return [self._tracked_row(r) for r in rows]

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
                    "price_currency, price_chaos, price_type, indexed_at, priced_at, "
                    "stash_name, stash_x, stash_y, first_seen, last_seen, "
                    "status, ambiguous, raw_data) "
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
                        row.priced_at.isoformat(),
                        row.last_seen.isoformat(),
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
                (row.gone_first_missing.isoformat(), row.id),
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
                    event.at.isoformat(),
                    event.event,
                    event.old_price_chaos,
                    event.new_price_chaos,
                ),
            )

        self.conn.commit()
        return {"inserted": inserted, "updated": updated, "gone": len(result.gone)}

    @staticmethod
    def _tracked_insert_params(account: str, league: str, row) -> tuple:
        item = row.listing.item if row.listing else None
        listing = row.listing
        # listing.stash is where GGG says the item physically is. It rides in
        # raw_data because MarketListing has no field for it -- it is of no
        # interest to the market collector, and of every interest here, because
        # it is what lets the overlay draw on a listed map.
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
            int(getattr(item, "corrupted", False)),
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
            listing.indexed_at.isoformat() if listing and listing.indexed_at else None,
            row.priced_at.isoformat(),
            stash.get("name") or None,
            stash.get("x"),
            stash.get("y"),
            row.first_seen.isoformat(),
            row.last_seen.isoformat(),
            "live",
            int(row.ambiguous),
            json.dumps(getattr(listing, "raw_data", {}) or {}),
        )

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
                started_at.isoformat(),
                finished_at.isoformat() if finished_at else None,
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

    def save_tracked_predictions(self, rows: Sequence[dict]) -> int:
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
```

Confirm `json`, `sqlite3`, `datetime` and `Sequence` are already imported at the top of the file; add whichever is missing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracker_repository.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/database/repository.py tests/test_tracker_repository.py
git commit -m "Persist tracked listings, their events and every poll run"
```

---

### Task 5: The reprice ladder

The derived recommendation needs the censored fit, which is the follow-on plan. This task builds the fallback ladder the fit will later override — and it is labelled as a fallback from the start, so no number ever claims to be measured when it is not.

**Files:**
- Create: `src/tracking/reprice.py`
- Test: `tests/test_tracking_reprice.py`

**Interfaces:**
- Produces:
  - `Recommendation(price_chaos, reason, source, floored)`
  - `LadderConfig(steps, floor_ratio, stale_after_hours)`
  - `recommend_ladder(price_chaos, hours_listed, predicted_chaos, interval_low_chaos, cfg) -> Recommendation | None`

- [ ] **Step 1: Write the failing test**

```python
"""What to re-price a map to when it has not sold."""
from __future__ import annotations

import pytest

from src.tracking.reprice import LadderConfig, recommend_ladder

CFG = LadderConfig(steps=((24, 0.92), (72, 0.85), (168, 0.78)), floor_ratio=0.75,
                   stale_after_hours=24)


def test_a_fresh_listing_gets_no_recommendation():
    assert recommend_ladder(150.0, hours_listed=3, predicted_chaos=140.0,
                            interval_low_chaos=120.0, cfg=CFG) is None


def test_one_day_stale_steps_down_one_rung():
    rec = recommend_ladder(150.0, hours_listed=30, predicted_chaos=140.0,
                           interval_low_chaos=100.0, cfg=CFG)
    assert rec.price_chaos == pytest.approx(138.0)
    assert rec.source == "ladder"
    assert "30 h" in rec.reason and "has not sold" in rec.reason


def test_a_week_stale_steps_down_furthest():
    rec = recommend_ladder(150.0, hours_listed=200, predicted_chaos=140.0,
                           interval_low_chaos=90.0, cfg=CFG)
    assert rec.price_chaos == pytest.approx(117.0)


def test_the_floor_stops_a_stale_map_giving_itself_away():
    rec = recommend_ladder(110.0, hours_listed=200, predicted_chaos=140.0,
                           interval_low_chaos=120.0, cfg=CFG)
    assert rec.floored is True
    assert rec.price_chaos == pytest.approx(120.0), "never below the interval floor"
    assert "floor" in rec.reason


def test_already_at_the_floor_recommends_nothing_and_says_why():
    rec = recommend_ladder(120.0, hours_listed=400, predicted_chaos=140.0,
                           interval_low_chaos=120.0, cfg=CFG)
    assert rec.floored is True
    assert rec.price_chaos == pytest.approx(120.0)
    assert "may simply not want it" in rec.reason


def test_a_map_with_no_prediction_uses_the_ladder_without_a_floor():
    rec = recommend_ladder(150.0, hours_listed=30, predicted_chaos=None,
                           interval_low_chaos=None, cfg=CFG)
    assert rec.price_chaos == pytest.approx(138.0)
    assert rec.floored is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_reprice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking.reprice'`

- [ ] **Step 3: Write the implementation**

```python
"""What to re-price a map to when it has not sold.

A map that has not sold at its price over some hours is evidence that its
clearing price is lower. This module turns that into a number, and is careful
about two things.

**It says where the number came from.** The ladder here is a fallback: fixed
steps by staleness, used until there is enough of your own sale history to fit
the real thing (see the learning plan). A fallback labelled as a measurement is
worse than no number at all, so `source` is on every recommendation and the page
prints it.

**It has a floor.** Left alone, a ladder walks a map that nobody wants down
until it is being given away -- each markdown producing more evidence that the
price is still too high, which is exactly what a ladder is built to react to.
The floor is the bottom of the model's own confidence interval: below that, the
honest answer is that the market may not want this map at a sensible price, not
a smaller number.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LadderConfig:
    #: (hours listed, multiplier) rungs, ascending by hours. The last rung whose
    #: hours the listing has passed is the one that applies.
    steps: tuple[tuple[float, float], ...] = ((24, 0.92), (72, 0.85), (168, 0.78))
    #: Hard floor as a fraction of the prediction, used when no interval is known.
    floor_ratio: float = 0.75
    stale_after_hours: float = 24.0


@dataclass(frozen=True)
class Recommendation:
    price_chaos: float
    reason: str
    source: str  # "ladder" | "fit"
    floored: bool = False


def recommend_ladder(
    price_chaos: float,
    hours_listed: float,
    predicted_chaos: float | None,
    interval_low_chaos: float | None,
    cfg: LadderConfig,
) -> Recommendation | None:
    """A lower price for a listing that has sat, or None if it has not sat long."""
    if price_chaos is None or price_chaos <= 0:
        return None

    rung = None
    for hours, multiplier in cfg.steps:
        if hours_listed >= hours:
            rung = multiplier
    if rung is None:
        return None

    floor = interval_low_chaos
    if floor is None and predicted_chaos is not None:
        floor = predicted_chaos * cfg.floor_ratio

    stepped = price_chaos * rung
    if floor is not None and stepped <= floor:
        at_floor = price_chaos <= floor + 1e-6
        reason = (
            f"listed {hours_listed:.0f} h and has not sold, but {stepped:.0f}c would "
            f"be under the {floor:.0f}c floor. "
            + (
                "It is already at the floor -- the market may simply not want it at "
                "a sensible price."
                if at_floor
                else f"Recommending the floor, {floor:.0f}c."
            )
        )
        return Recommendation(
            price_chaos=round(floor, 1), reason=reason, source="ladder", floored=True
        )

    return Recommendation(
        price_chaos=round(stepped, 1),
        reason=(
            f"listed {hours_listed:.0f} h at {price_chaos:.0f}c and has not sold; "
            f"stepping to {stepped:.0f}c. (Fallback ladder -- not enough of your own "
            f"sale history yet to measure what actually clears.)"
        ),
        source="ladder",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_reprice.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/tracking/reprice.py tests/test_tracking_reprice.py
git commit -m "Add the fallback reprice ladder, with a floor and an honest source"
```

---

### Task 6: The poller

**Files:**
- Create: `src/tracking/poller.py`
- Modify: `src/config.py`, `config.example.yaml`, `config.yaml`
- Test: `tests/test_tracking_poller.py`

**Interfaces:**
- Consumes: `fetch_my_listings`, `reconcile`, `Repository.apply_reconciliation`, `Repository.record_poll_run`
- Produces:
  - `PollerConfig(interval_minutes, young_listing_minutes, young_poll_interval_minutes, reappearance_window_hours)`
  - `PollState(last_run_at, last_error, polls, listings_live, running)`
  - `ListingPoller.start(repo, collector, account, league, cfg) -> ListingPoller`, `.poll_once(now)`, `.cancel()`, `.next_interval_seconds(now)`

- [ ] **Step 1: Write the failing test**

```python
"""The poller: one pass, its cadence, and its refusal to guess."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.database.repository import Repository
from src.market.my_listings import MyListingsSnapshot
from src.schema import MarketListing, NormalizedMap
from src.tracking.poller import ListingPoller, PollerConfig

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
CFG = PollerConfig(interval_minutes=15, young_listing_minutes=60,
                   young_poll_interval_minutes=5)


def _listing(price=120.0, listing_id="abc"):
    item = NormalizedMap(
        name="Glyph Solace", base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=90, rarity=140, pack_size=30,
    )
    return MarketListing(
        item=item, listing_id=listing_id, league="Allflame", price_amount=price,
        price_currency="chaos", price_chaos=price, price_type="~b/o", seller="me",
    )


class _Source:
    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    def __call__(self, account):
        self.calls += 1
        return self.snapshots[min(self.calls - 1, len(self.snapshots) - 1)]


def _poller(tmp_path, source, cfg=CFG):
    repo = Repository(tmp_path / "t.db")
    return repo, ListingPoller(repo=repo, source=source, account="me",
                               league="Allflame", cfg=cfg)


def test_one_pass_records_the_listing_and_the_run(tmp_path):
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)

    assert len(repo.live_tracked_rows("me", "Allflame")) == 1
    run = repo.last_poll_run("me", "Allflame")
    assert run["complete"] == 1 and run["listings_seen"] == 1


def test_an_incomplete_snapshot_never_marks_anything_sold(tmp_path):
    source = _Source(
        MyListingsSnapshot([_listing()], total_reported=1, complete=True),
        MyListingsSnapshot([], total_reported=250, complete=False),
    )
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)
    poller.poll_once(now=T0 + timedelta(minutes=15))

    assert len(repo.live_tracked_rows("me", "Allflame")) == 1, "still live, not sold"
    assert repo.last_poll_run("me", "Allflame")["complete"] == 0


def test_a_failed_pass_is_recorded_rather_than_swallowed(tmp_path):
    def _boom(account):
        raise RuntimeError("rate limited by GGG on search; backing off")

    repo, poller = _poller(tmp_path, _boom)
    poller.poll_once(now=T0)

    run = repo.last_poll_run("me", "Allflame")
    assert "rate limited" in run["error"]
    assert run["complete"] == 0
    assert poller.state.last_error


def test_young_listings_are_polled_more_often(tmp_path):
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)
    assert poller.next_interval_seconds(now=T0) == 5 * 60


def test_an_all_old_book_falls_back_to_the_slow_cadence(tmp_path):
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)
    assert poller.next_interval_seconds(now=T0 + timedelta(hours=5)) == 15 * 60


def test_an_empty_book_uses_the_slow_cadence(tmp_path):
    source = _Source(MyListingsSnapshot([], total_reported=0, complete=True))
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)
    assert poller.next_interval_seconds(now=T0) == 15 * 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_poller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking.poller'`

- [ ] **Step 3: Write the implementation**

```python
"""Polling your own listings on a thread, the way market runs already work.

Streamlit reruns the script top to bottom on every widget touch and runs one
script at a time per session, so anything that waits holds the only thread the
page has. `src/market/run_job.py` solved that for market runs; this is the same
shape for a repeating job: a worker that owns its own cadence, a
`threading.Event` off switch, and state the page reads while it runs. Nothing
here imports Streamlit.

The cadence is not uniform, for a reason worth stating. A listing's first hour
is where the fast-sale signal lives -- the difference between "sold in eleven
minutes" and "sold in three hours" is most of what the learning half has to work
with, and a fifteen-minute poll blurs it. A map that has sat for three days does
not need a five-minute check to tell us it is still sitting. So young listings
are polled often and old ones are not, which spends requests where they buy
information.

`poll_once` takes `now` rather than reading the clock, so a whole day of polling
replays in a test in milliseconds.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from src.market.my_listings import MyListingsSnapshot
from src.tracking.reconcile import reconcile


@dataclass(frozen=True)
class PollerConfig:
    interval_minutes: float = 15.0
    young_listing_minutes: float = 60.0
    young_poll_interval_minutes: float = 5.0
    reappearance_window_hours: float = 6.0


@dataclass
class PollState:
    last_run_at: datetime | None = None
    last_error: str = ""
    polls: int = 0
    listings_live: int = 0
    last_complete: bool = False
    running: bool = False


class ListingPoller:
    """One repeating poll of one account's listings."""

    def __init__(
        self,
        repo,
        source: Callable[[str], MyListingsSnapshot],
        account: str,
        league: str,
        cfg: PollerConfig,
    ):
        self.repo = repo
        self.source = source
        self.account = account
        self.league = league
        self.cfg = cfg
        self.state = PollState()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def poll_once(self, now: datetime) -> None:
        started = now
        try:
            snapshot = self.source(self.account)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            self.state.last_error = str(exc)
            self.state.last_run_at = now
            self.repo.record_poll_run(
                started_at=started, finished_at=now, league=self.league,
                account=self.account, listings_seen=0, total_reported=0,
                complete=False, error=str(exc),
            )
            return

        previous = self.repo.live_tracked_rows(self.account, self.league)
        previous += self.repo.recent_gone_rows(
            self.account,
            self.league,
            since=now - timedelta(hours=self.cfg.reappearance_window_hours),
        )
        result = reconcile(
            previous,
            snapshot.listings,
            now=now,
            complete=snapshot.complete,
            reappearance_window_hours=self.cfg.reappearance_window_hours,
        )
        self.repo.apply_reconciliation(self.account, self.league, result)
        self.repo.record_poll_run(
            started_at=started, finished_at=now, league=self.league,
            account=self.account, listings_seen=len(snapshot.listings),
            total_reported=snapshot.total_reported, complete=snapshot.complete,
            error="",
        )
        self.state.last_error = ""
        self.state.last_run_at = now
        self.state.polls += 1
        self.state.listings_live = len(result.live)
        self.state.last_complete = snapshot.complete

    def next_interval_seconds(self, now: datetime) -> float:
        """Fast while anything is young, slow once the book has settled."""
        rows = self.repo.live_tracked_rows(self.account, self.league)
        young = timedelta(minutes=self.cfg.young_listing_minutes)
        if any(now - r.priced_at < young for r in rows):
            return self.cfg.young_poll_interval_minutes * 60.0
        return self.cfg.interval_minutes * 60.0

    # ------------------------------------------------------------------
    def start(self) -> "ListingPoller":
        """Poll immediately, then on the cadence, until cancelled.

        The immediate poll is not impatience. Polling stops when the app closes,
        so the upper bound on anything that sold overnight is "sometime before
        you next opened this" -- and every minute between opening the app and
        the first poll widens that bracket for no reason.
        """
        if self._thread is not None:
            return self

        def _run() -> None:
            self.state.running = True
            try:
                while not self._cancel.is_set():
                    now = datetime.now(timezone.utc)
                    with self._lock:
                        self.poll_once(now)
                    self._cancel.wait(self.next_interval_seconds(now))
            finally:
                self.state.running = False

        self._thread = threading.Thread(
            target=_run, name="listing-poller", daemon=True
        )
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()
```

Then add the config block. In `config.example.yaml` and `config.yaml`:

```yaml
listing_tracker:
  enabled: false                 # stays off until POE_ACCOUNT_NAME is set
  poll_interval_minutes: 15
  young_listing_minutes: 60      # a listing this new is polled on the fast cadence
  young_poll_interval_minutes: 5
  reappearance_window_hours: 6   # a "sold" map returning this soon was never sold
  stale_after_hours: 24          # page filter: what counts as 'sitting'
  ladder:
    steps: [[24, 0.92], [72, 0.85], [168, 0.78]]
    floor_ratio: 0.75
```

And the matching defaults in `src/config.py`, following the shape of the existing `"scope"` and `"market_comparison"` entries in the defaults dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_poller.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/tracking/poller.py src/config.py config.yaml config.example.yaml tests/test_tracking_poller.py
git commit -m "Add the listing poller: immediate first pass, faster while listings are young"
```

---

### Task 7: The My Listings page

**Files:**
- Create: `src/ui/my_listings.py`
- Modify: `app.py`, `docs/data-access.md`
- Test: `tests/test_my_listings_page.py`

**Interfaces:**
- Consumes: everything above, plus `build_highlight_batches` from `src/stash/highlight_regex.py` and `Predictor` from `src/ml/predict.py`
- Produces:
  - `stale_rows(rows, now, stale_after_hours) -> list[dict]`
  - `regex_for_stale(stale, all_rows) -> HighlightBatches`
  - `render()`

- [ ] **Step 1: Write the failing test**

The page's decisions are extracted as pure functions so they are testable without a browser, matching how `tests/test_ui_smoke.py` treats the other pages.

```python
"""The stale-listing view: which maps it selects, and what it lights up in game."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.ui.my_listings import regex_for_stale, stale_rows

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _row(name, hours, price=150.0, predicted=140.0, low=120.0):
    return {
        "id": hash(name) % 10_000,
        "name": name,
        "base_type": "Terror Solace",
        "price_chaos": price,
        "predicted_chaos": predicted,
        "interval_low_chaos": low,
        "priced_at": (T0 - timedelta(hours=hours)).isoformat(),
        "ambiguous": 0,
        "exclude_from_learning": 0,
    }


def test_only_maps_past_the_threshold_are_stale():
    rows = [_row("Glyph Solace", 3), _row("Rune Path", 30), _row("Bleak Lands", 200)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    assert [s["name"] for s in stale] == ["Bleak Lands", "Rune Path"], "oldest first"


def test_each_stale_row_carries_hours_and_a_recommendation():
    stale = stale_rows([_row("Rune Path", 30)], now=T0, stale_after_hours=24)
    assert stale[0]["hours_listed"] == 30
    assert stale[0]["recommendation"].price_chaos < 150.0
    assert stale[0]["recommendation"].source == "ladder"


def test_the_regex_lights_the_stale_maps_and_not_the_fresh_ones():
    rows = [_row("Glyph Solace", 3), _row("Rune Path", 30)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    batches = regex_for_stale(stale, rows)
    assert batches.ok
    pattern = batches.pages[0].pattern
    assert "Rune" in pattern or "Path" in pattern
    assert "Glyph" not in pattern and "Solace" not in pattern


def test_no_stale_maps_produces_no_pattern():
    rows = [_row("Glyph Solace", 3)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    assert stale == []
    assert not regex_for_stale(stale, rows).ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_my_listings_page.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.ui.my_listings'`

- [ ] **Step 3: Write the page**

Create `src/ui/my_listings.py`. The two pure functions first — these are what the test drives — then `render()` built on them in the style of `src/ui/stash_pricer.py` (which already calls `build_highlight_batches` and can be read as the model for the regex block and the copy controls).

```python
"""What you have listed, what has not sold, and what to do about it.

The page answers one question -- which of my maps are sitting -- and then makes
that answer actionable in the two places the answer is useful: a stash search
that lights the stale maps up in game, and an overlay that writes the new price
onto them where they physically are.

The stale set is the well-measured half of this feature. A disappearance is only
bracketed, because polling stops when the app closes; but a map still listed
after sixty-two hours has been listed for sixty-two hours whatever the gaps, so
everything on this page rests on exact numbers.

The decisions live in `stale_rows` and `regex_for_stale` rather than inside
`render`, so they are tested without a browser.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.stash.highlight_regex import HighlightBatches, build_highlight_batches
from src.tracking.reprice import LadderConfig, Recommendation, recommend_ladder


def stale_rows(
    rows, now: datetime, stale_after_hours: float, ladder: LadderConfig | None = None
) -> list[dict]:
    """The listings that have sat at least `stale_after_hours`, longest first."""
    ladder = ladder or LadderConfig(stale_after_hours=stale_after_hours)
    out: list[dict] = []
    for row in rows:
        priced_at = datetime.fromisoformat(row["priced_at"])
        hours = (now - priced_at).total_seconds() / 3600.0
        if hours < stale_after_hours:
            continue
        entry = dict(row)
        entry["hours_listed"] = hours
        entry["recommendation"] = recommend_ladder(
            price_chaos=row["price_chaos"],
            hours_listed=hours,
            predicted_chaos=row["predicted_chaos"],
            interval_low_chaos=row["interval_low_chaos"],
            cfg=ladder,
        )
        out.append(entry)
    out.sort(key=lambda e: e["hours_listed"], reverse=True)
    return out


def regex_for_stale(stale, all_rows) -> HighlightBatches:
    """A stash search that lights the stale maps and nothing else in the tab.

    `build_highlight_regex`'s contract is that the exclusion corpus is the full
    item text of every map you do *not* want lit -- not just their names -- so a
    fragment that happens to sit inside a shared line like "Map (Tier 16)"
    cannot light the whole tab. The fresh listings are that corpus here.
    """
    stale_names = [r["name"] for r in stale if r["name"]]
    stale_ids = {r["id"] for r in stale}
    excluded = [
        "\n".join(filter(None, [r["name"], r["base_type"], "Map (Tier 16)"]))
        for r in all_rows
        if r["id"] not in stale_ids
    ]
    return build_highlight_batches(stale_names, excluded)
```

Then `render()`: read `POE_ACCOUNT_NAME` and league from config, start or reuse the poller in `st.session_state`, show poll status (last run, whether it was complete, any error), the staleness slider, the table with ask / model / hours / recommendation / reason, the regex pages with copy buttons, the overlay export button, and the `exclude_from_learning` checkboxes. Follow `src/ui/stash_pricer.py` for the copy-block and overlay-export idioms.

Register in `app.py` — in the Price group, after Stash Pricer:

```python
PAGES = {
    # Price
    "Dashboard": "src.ui.dashboard",
    "Stash Pricer": "src.ui.stash_pricer",
    "My Listings": "src.ui.my_listings",
    "Single Map": "src.ui.single_map",
    ...
}
```

**Check the nav CSS.** `app.py`'s own comment warns that the group headings above Dashboard, Market Data and Model Analysis are drawn by `nth-child` selectors in `src/ui/common.py`. Adding a page to the Price group shifts every later position by one, so the `.nav-group` `nth-child` values for the Market and Tool headings must each be incremented, or the headings land on the wrong rows.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_my_listings_page.py tests/test_ui_smoke.py -v`
Expected: all pass

- [ ] **Step 5: Update the data-access record**

`docs/data-access.md` §3 ends with "**Not done, deliberately:** no continuous background polling…". That is no longer true. Amend that paragraph to record what changed and why it is still modest:

```markdown
**Polling, as of 2026-08-28.** The listing tracker polls one account-scoped
query -- your own listings -- on a 5-to-15-minute cadence while the app is open:
one search plus one fetch per ten listings, roughly twenty requests an hour
against a sustained policy of thirty searches per five minutes. It is still
driven by GGG's own headers, still backs off rather than retrying on 429, and
still collects nothing about anybody else. What remains not done: distributed
collection, polling with the app closed, and any attempt to enumerate the
10,000-result cap.
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/ui/my_listings.py app.py src/ui/common.py docs/data-access.md tests/test_my_listings_page.py
git commit -m "Add the My Listings page: stale maps, stash regex, overlay export"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2 reading listings, collapse/status/sale_type, >100 handling, cost | 3 |
| §3 identity, one row per map, no unique index | 1, 2 |
| §4 event log, gone = sold, reprice restarts clock, censoring, poll runs | 1, 2, 4, 6 |
| §5 schema | 1 |
| §6 module split | all |
| §7 the estimator | **learning plan** |
| §8 page, staleness filter, regex, overlay, reprice recommendation | 5, 7 |
| §8 alerts | **learning plan** (thresholds are observed-distribution) |
| §9 the learning | **learning plan** |
| §10 configuration | 6 |
| §11 testing | every task |
| §2 doc update | 7 |

**Placeholder scan:** none — every step carries the code or the exact edit.

**Type consistency:** `TrackedRow` and `Reconciliation` are defined in Task 2 and consumed with those field names in Tasks 4 and 6. `MyListingsSnapshot(listings, total_reported, complete, error)` is defined in Task 3 and constructed with those names in Task 6's fake. `Recommendation(price_chaos, reason, source, floored)` is defined in Task 5 and read in Task 7. `listing_from_result` is renamed in Task 3 and called there only.

**Known gap, deliberate:** Task 7's `render()` is described rather than written out line by line, because it is Streamlit layout following two existing pages closely and the decisions inside it are already extracted into the two tested functions. If the implementer finds a judgement inside `render` that wants a test, that is a signal to extract it, not to test the widget.
