"""Not paying twice for the same listing.

A trade search hands back up to 100 ids for one request. A fetch costs one
request per ten. So the fetch is ten times dearer per listing, and before any of
this existed it was being spent re-reading rows already in the database --
measured at 61% of every fetch request over a six-cycle run.

Two filters fix it and this file pins both: ids already taken this run, and ids
the caller says are not worth re-reading. The third piece is the pacing that
falls out of it -- once repeats are free to detect, the gap between scans can be
driven by how much the market actually moved.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.market.passive import PassiveSettings


class FakeCollector:
    """Drives the real `collect()` loop against canned search/fetch results."""

    def __init__(self, pages, **kwargs):
        from src.market.collectors.official_trade_api import OfficialTradeApiCollector

        self.pages = pages
        self.searched = []
        self.fetched = []
        self.inner = OfficialTradeApiCollector.__new__(OfficialTradeApiCollector)
        self.inner.skip_ids = kwargs.get("skip_ids")
        self.inner.cancel = None
        self.inner.last_walk = {}
        self.inner.search = self._search
        self.inner.fetch = self._fetch
        self.inner.build_query = lambda **spec: spec
        self.inner._stop = lambda: False
        self.inner._wait = lambda s: None
        self.inner.listing_from_result = lambda r: r

    def _search(self, query):
        page = self.pages[min(len(self.searched), len(self.pages) - 1)]
        self.searched.append(query)
        return "qid", list(page), len(page)

    def _fetch(self, ids, query_id):
        self.fetched.extend(ids)
        return [_listing(i) for i in ids]


class _Listing:
    def __init__(self, ident):
        self.ident = ident
        self.seller = f"seller-{ident}"


def _listing(ident):
    return _Listing(ident)


def _walk(pages, slices=3, skip=None, limit=500):
    collector = FakeCollector(pages, skip_ids=skip)
    from src.market.collectors.official_trade_api import OfficialTradeApiCollector

    got = list(
        OfficialTradeApiCollector.collect(
            collector.inner,
            limit=limit,
            slices=[{"n": i} for i in range(slices)],
            per_slice=100,
        )
    )
    return collector, got


# ---------------------------------------------------------------------------
def test_an_id_returned_by_two_slices_is_only_fetched_once():
    """Every slice sorts by indexed desc, so a map matching two of them sits
    near the top of both. It used to be fetched twice."""
    overlapping = [["a", "b", "c"], ["b", "c", "d"], ["c", "d", "e"]]
    collector, got = _walk(overlapping)
    assert collector.fetched == ["a", "b", "c", "d", "e"]
    assert len(got) == 5


def test_the_seen_set_spans_slices_not_just_one_fetch_batch():
    """dedupe() downstream only ever caught collisions inside one 50-row batch."""
    collector, _ = _walk([["x"], ["x"], ["x"], ["x"]], slices=4)
    assert collector.fetched == ["x"]


def test_ids_the_caller_says_to_skip_are_never_fetched():
    collector, got = _walk(
        [["a", "b", "c", "d"]], slices=1, skip=lambda ids: {"b", "d"}
    )
    assert collector.fetched == ["a", "c"]
    assert [g.ident for g in got] == ["a", "c"]


def test_a_skip_filter_that_raises_does_not_stop_the_run():
    """The filter is an optimisation. Losing it must cost requests, not data."""
    def boom(ids):
        raise RuntimeError("database went away")

    collector, got = _walk([["a", "b"]], slices=1, skip=boom)
    assert collector.fetched == ["a", "b"]


def test_the_walk_reports_how_much_of_what_it_saw_was_new():
    """The saturation signal, measured on search results so it costs no fetch."""
    collector, _ = _walk([["a", "b", "c", "d"]], slices=1, skip=lambda ids: {"c", "d"})
    walk = collector.inner.last_walk
    assert walk["ids_seen"] == 4
    assert walk["ids_fresh"] == 2
    assert walk["fresh_ratio"] == pytest.approx(0.5)


def test_a_run_that_sees_nothing_reports_a_fresh_ratio_of_one_not_a_crash():
    collector, _ = _walk([[]], slices=1)
    assert collector.inner.last_walk["fresh_ratio"] == 1.0


# ---------------------------------------------------------------------------
# The database side of the filter
# ---------------------------------------------------------------------------
def _repo(tmp_path):
    from src.database.repository import Repository

    return Repository(tmp_path / "t.db")


def test_only_ids_seen_inside_the_window_are_reported_as_known(tmp_path):
    """Not "is it stored" -- a seller who marks a map down publishes real
    information, and an id never re-read would freeze at its first ask."""
    from src.schema import MarketListing, NormalizedMap

    repo = _repo(tmp_path)
    now = datetime.now(timezone.utc)
    rows = []
    for ident, age_hours in (("fresh", 1), ("stale", 48)):
        rows.append(
            MarketListing(
                listing_id=ident, league="Allflame",
                item=NormalizedMap(base_type="Map (Tier 16)", map_tier=16,
                                   is_originator=True, quantity=100),
                price_amount=50, price_currency="chaos", price_chaos=50.0,
                seller="s", captured_at=now - timedelta(hours=age_hours),
                indexed_at=now - timedelta(hours=age_hours), source="official_trade_api",
            )
        )
    repo.upsert_listings(rows)

    cutoff = now - timedelta(hours=12)
    known = repo.listing_ids_seen_since(["fresh", "stale", "never-seen"], cutoff)
    assert known == {"fresh"}
    repo.close()


def test_an_empty_id_list_costs_no_query(tmp_path):
    repo = _repo(tmp_path)
    assert repo.listing_ids_seen_since([], datetime.now(timezone.utc)) == set()
    assert repo.listing_ids_seen_since(["", None], datetime.now(timezone.utc)) == set()
    repo.close()


def test_more_ids_than_sqlite_takes_as_parameters_are_chunked(tmp_path):
    """SQLite caps host parameters at 999 and a search page can be handed on
    in bulk. The chunking must not turn into a silent truncation."""
    repo = _repo(tmp_path)
    ids = [f"id-{i}" for i in range(2500)]
    assert repo.listing_ids_seen_since(ids, datetime.now(timezone.utc)) == set()
    repo.close()


def test_the_listing_id_lookup_has_an_index_behind_it(tmp_path):
    """Run per slice against the whole table; without an index that is 100
    table scans a scan."""
    repo = _repo(tmp_path)
    names = {r["name"] for r in repo.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='originator_listings'")}
    assert "idx_listings_listing_id" in names
    repo.close()


# ---------------------------------------------------------------------------
# Pacing on saturation
# ---------------------------------------------------------------------------
def test_a_scan_that_comes_back_mostly_repeats_stretches_the_gap():
    settings = PassiveSettings()
    gap = settings.cooldown_minutes
    stretched = settings.next_cooldown_minutes(gap, fresh_ratio=0.02)
    assert stretched > gap


def test_a_market_that_is_moving_pulls_the_gap_back_in():
    settings = PassiveSettings()
    gap = settings.next_cooldown_minutes(60.0, fresh_ratio=0.9)
    assert gap < 60.0


def test_the_gap_never_falls_below_the_configured_floor():
    settings = PassiveSettings()
    gap = 12.0
    for _ in range(10):
        gap = settings.next_cooldown_minutes(gap, fresh_ratio=1.0)
    assert gap == pytest.approx(settings.cooldown_minutes)


def test_the_gap_never_climbs_past_the_ceiling():
    """A dead league still gets looked at a few times a day."""
    settings = PassiveSettings()
    gap = 12.0
    for _ in range(50):
        gap = settings.next_cooldown_minutes(gap, fresh_ratio=0.0)
    assert gap == pytest.approx(settings.max_cooldown_minutes)


def test_a_middling_scan_leaves_the_gap_where_it_is():
    settings = PassiveSettings()
    assert settings.next_cooldown_minutes(30.0, fresh_ratio=0.3) == pytest.approx(30.0)


def test_the_gap_is_unchanged_before_the_first_scan_has_measured_anything():
    settings = PassiveSettings()
    assert settings.next_cooldown_minutes(20.0, fresh_ratio=None) == pytest.approx(20.0)


def test_a_dead_market_reaches_the_ceiling_in_a_handful_of_scans_not_overnight():
    settings = PassiveSettings()
    gap, scans = settings.cooldown_minutes, 0
    while gap < settings.max_cooldown_minutes and scans < 100:
        gap = settings.next_cooldown_minutes(gap, fresh_ratio=0.0)
        scans += 1
    assert scans <= 6, f"took {scans} scans to back off fully"
