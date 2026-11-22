"""The poller: one pass, its cadence, and its refusal to guess."""
from __future__ import annotations

import sqlite3
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


# ---------------------------------------------------------------------------
# The loop has to survive everything, not only a failed fetch
# ---------------------------------------------------------------------------
class _BrokenWriteRepo:
    """A repository whose reads work and whose writes do not.

    The failure that actually happened: a passive collection flushing four
    hundred listings holds the write lock past `busy_timeout`, and the poller's
    `apply_reconciliation` raised `database is locked` straight out of the loop.
    """

    def __init__(self, real):
        self.real = real
        self.runs = []

    def live_tracked_rows(self, *a, **k):
        return self.real.live_tracked_rows(*a, **k)

    def recent_gone_rows(self, *a, **k):
        return self.real.recent_gone_rows(*a, **k)

    def apply_reconciliation(self, *a, **k):
        raise sqlite3.OperationalError("database is locked")

    def record_poll_run(self, **kwargs):
        self.runs.append(kwargs)
        return len(self.runs)


def test_a_write_that_fails_is_recorded_rather_than_ending_the_thread(tmp_path):
    """It used to raise out of `poll_once`, kill the daemon thread and leave the
    switch saying it was watching for the rest of the session."""
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)
    poller.repo = _BrokenWriteRepo(repo)

    poller.poll_once(now=T0)  # must not raise

    assert poller.state.failed_passes == 1
    assert "database is locked" in poller.state.last_error
    assert poller.repo.runs[-1]["error"], "the failed pass is on the record"


def test_the_loop_keeps_going_after_a_failed_pass(tmp_path):
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)
    broken = _BrokenWriteRepo(repo)
    poller.repo = broken

    poller.poll_once(now=T0)
    poller.repo = repo  # the lock cleared
    poller.poll_once(now=T0 + timedelta(minutes=15))

    assert len(repo.live_tracked_rows("me", "Allflame")) == 1, "it recovered"


def test_the_cadence_falls_back_rather_than_raising_out_of_the_loop(tmp_path):
    source = _Source(MyListingsSnapshot([], total_reported=0, complete=True))
    repo, poller = _poller(tmp_path, source)

    class _Unreadable:
        def live_tracked_rows(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    poller.repo = _Unreadable()
    assert poller.next_interval_seconds(now=T0) == 15 * 60, "the slow cadence, not a crash"


def test_a_dead_poller_reports_itself_dead_and_can_be_restarted(tmp_path):
    """`self._thread is not None` says "started" forever, including after the
    thread has ended. The page needs to know the difference."""
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(tmp_path, source)

    assert not poller.running
    poller.start()
    poller.cancel()
    poller._thread.join(timeout=5)
    assert not poller.running, "a finished thread is not a running poller"

    poller.start()
    try:
        assert poller.running, "starting a dead poller starts it again"
    finally:
        poller.cancel()
        poller._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# One poller per account, owned by the process rather than a browser tab
# ---------------------------------------------------------------------------
def test_the_registry_hands_back_the_same_poller_rather_than_a_second_one(tmp_path):
    """A browser refresh used to orphan the running thread -- still polling,
    no longer cancellable -- and start another beside it. Two pollers on one
    account is how a fifteen-minute cadence becomes a page full of 429s."""
    from src.tracking.poller import poller_for, running_poller, stop_poller

    source = _Source(MyListingsSnapshot([], total_reported=0, complete=True))
    built = []

    def build():
        repo, poller = _poller(tmp_path, source)
        built.append(poller)
        return poller

    try:
        first = poller_for("me", "Allflame", build)
        second = poller_for("me", "Allflame", build)
        assert first is second
        assert len(built) == 1, "the second call did not even build one"
        assert running_poller("me", "Allflame") is first
    finally:
        stop_poller("me", "Allflame")

    assert running_poller("me", "Allflame") is None, "off means forgotten"


def test_the_registry_restarts_a_poller_whose_thread_has_died(tmp_path):
    from src.tracking.poller import poller_for, stop_poller

    source = _Source(MyListingsSnapshot([], total_reported=0, complete=True))
    repo, poller = _poller(tmp_path, source)
    try:
        poller_for("me#dead", "Allflame", lambda: poller)
        poller.cancel()
        poller._thread.join(timeout=5)
        assert not poller.running

        again = poller_for("me#dead", "Allflame", lambda: poller)
        assert again is poller and poller.running, "handed back alive, not dead"
    finally:
        stop_poller("me#dead", "Allflame")
        if poller._thread:
            poller._thread.join(timeout=5)


def test_a_poke_cuts_the_cooldown_short_and_the_caller_can_wait_for_it(tmp_path):
    """What the page's "check now" needs: a pass on the poller's own thread,
    with the poller's own connection, and an answer to wait for."""
    source = _Source(MyListingsSnapshot([_listing()], total_reported=1, complete=True))
    repo, poller = _poller(
        tmp_path, source, PollerConfig(interval_minutes=600, young_listing_minutes=0)
    )
    poller.start()
    try:
        assert poller.wait_for_pass(0, timeout=10), "the immediate first pass"
        before = poller.passes
        poller.poke()
        assert poller.wait_for_pass(before, timeout=10), (
            "the ten-hour cooldown was cut short"
        )
        assert source.calls >= 2
    finally:
        poller.cancel()
        poller._thread.join(timeout=5)


def test_waiting_on_a_poller_that_is_not_running_gives_up_rather_than_hanging(tmp_path):
    source = _Source(MyListingsSnapshot([], total_reported=0, complete=True))
    repo, poller = _poller(tmp_path, source)
    assert not poller.running
    assert poller.wait_for_pass(0, timeout=5) is False


def test_cancel_wakes_a_sleeping_poller_instead_of_waiting_out_the_gap(tmp_path):
    """An off switch that takes fifteen minutes to switch anything off gets
    pressed again and again."""
    source = _Source(MyListingsSnapshot([], total_reported=0, complete=True))
    repo, poller = _poller(tmp_path, source, PollerConfig(interval_minutes=600))
    poller.start()
    try:
        poller.wait_for_pass(0, timeout=10)
        poller.cancel()
        poller._thread.join(timeout=5)
        assert not poller.running
    finally:
        poller.cancel()


def test_a_rate_limited_pass_still_counts_as_a_pass(tmp_path):
    """Otherwise "check now" waits out its whole timeout on the one failure the
    user most needs told about."""
    def _limited(account):
        raise RuntimeError("rate limited by GGG on search; backing off")

    repo, poller = _poller(tmp_path, _limited)
    before = poller.passes
    poller.poll_once(now=T0)
    assert poller.passes > before
    assert "rate limited" in poller.state.last_error
