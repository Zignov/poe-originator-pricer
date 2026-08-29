"""The poller: one pass, its cadence, and its refusal to guess."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.database.repository import Repository
from src.market.my_listings import MyListingsSnapshot
from src.schema import MarketListing, NormalizedMap
from src.tracking.poller import ListingPoller, PollerConfig

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
CFG = PollerConfig(
    interval_minutes=15, young_listing_minutes=60, young_poll_interval_minutes=5
)


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
    return repo, ListingPoller(
        repo=repo, source=source, account="me", league="Allflame", cfg=cfg
    )


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


def test_a_failed_pass_does_not_mark_live_listings_sold(tmp_path):
    """A search that never returned is not evidence that your tab emptied."""
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)

    poller.source = lambda account: (_ for _ in ()).throw(RuntimeError("boom"))
    poller.poll_once(now=T0 + timedelta(minutes=15))

    assert len(repo.live_tracked_rows("me", "Allflame")) == 1


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


def test_a_reprice_between_polls_restarts_the_cadence_clock(tmp_path):
    """The new price is young even though the map is not."""
    source = _Source(
        MyListingsSnapshot([_listing(180.0)], total_reported=1, complete=True),
        MyListingsSnapshot([_listing(120.0)], total_reported=1, complete=True),
    )
    repo, poller = _poller(tmp_path, source)
    poller.poll_once(now=T0)
    later = T0 + timedelta(hours=5)
    poller.poll_once(now=later)
    assert poller.next_interval_seconds(now=later) == 5 * 60
