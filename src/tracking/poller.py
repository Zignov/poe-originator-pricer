"""Polling your own listings on a thread, the way market runs already work.

Streamlit reruns the script top to bottom on every widget touch and runs one
script at a time per session, so anything that waits holds the only thread the
page has. `src/market/run_job.py` solved that for market runs; this is the same
shape for a repeating job: a worker that owns its cadence, a `threading.Event`
off switch, and state the page reads while it runs. Nothing here imports
Streamlit.

**Give this its own Repository.** `src/database/models.py:connect` opens the
connection with `check_same_thread=False`, and its comment explains why that is
safe: Streamlit serialises reruns within a session, so the connection moves
between threads but is never used from two at once. A background poller breaks
exactly that assumption. So the caller hands the poller a `Repository` of its
own rather than sharing the page's, and the two connections never touch.

The cadence is not uniform, for a reason worth stating. A listing's first hour
is where the fast-sale signal lives -- the difference between "sold in eleven
minutes" and "sold in three hours" is most of what the learning half has to work
with, and a fifteen-minute poll blurs it. A map that has sat for three days does
not need a five-minute check to confirm it is still sitting. So young listings
are polled often and old ones are not, which spends requests where they buy
information. A markdown makes a listing young again, because the new price is a
new question.

`poll_once` takes `now` rather than reading the clock, so a whole day of polling
replays in a test in milliseconds.

**Nothing in the loop may kill the thread.** The first version guarded only the
call that fetches, on the grounds that the network is the part that fails. The
part that actually failed was the write: a passive collection flushing four
hundred listings and re-flagging outliers holds the write lock for longer than
`busy_timeout`, the poller's `apply_reconciliation` raised `database is locked`
straight out of the loop, and the thread ended -- silently, because a daemon
thread's traceback goes to a console nobody is looking at. The page went on
showing the last successful pass, for hours, with the switch still saying it was
watching. One bad pass is not a reason to stop watching, so every pass is
wrapped and the failure is recorded where the page already looks for it.

**The registry, not session state.** `src/market/passive.py` explains at length
why a background loop cannot be owned by a Streamlit session: session state dies
with the browser tab, so a refresh orphans the running thread -- which keeps
polling, uncancellable, with a rate limiter of its own -- and starts a second
one beside it. Two pollers on one account is how a fifteen-minute cadence turns
into 429s and a page full of failed passes. So the pollers live in a
process-global registry keyed by account and league, exactly one apiece, and the
page is a view of it rather than its owner.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
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
    #: Passes that raised somewhere other than the fetch -- a locked database,
    #: most likely. Counted rather than merely logged, because the page has to
    #: be able to say "it is still watching, but the last four passes failed"
    #: instead of showing a stale success and nothing else.
    failed_passes: int = 0


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
        #: Set to cut a cooldown short -- by `cancel`, so switching off is
        #: immediate, and by `poke`, so a "check now" button does not have to
        #: wait out fifteen minutes or reach into `self.repo` from the script
        #: thread. The connection belongs to the poller's thread; two threads
        #: interleaving transactions on one SQLite connection is precisely what
        #: `check_same_thread=False` stops complaining about without making safe.
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    def poll_once(self, now: datetime) -> None:
        """One pass. Records the run whether it worked or not.

        Raises nothing. The fetch has always been guarded; so is everything
        after it, because the write is what stopped the thread in practice. See
        the module docstring.
        """
        try:
            self._poll_once(now)
        except Exception as exc:  # noqa: BLE001 - a bad pass is not a reason to stop
            self.state.failed_passes += 1
            self.state.last_error = f"{type(exc).__name__}: {exc}"
            self.state.last_run_at = now
            # Best effort: if the database is what failed, this fails too, and
            # the in-memory state above is then the only record there can be.
            try:
                self.repo.record_poll_run(
                    started_at=now, finished_at=now, league=self.league,
                    account=self.account, listings_seen=0, total_reported=0,
                    complete=False, error=self.state.last_error,
                )
            except Exception:  # noqa: BLE001
                pass

    def _poll_once(self, now: datetime) -> None:
        started = now
        try:
            snapshot = self.source(self.account)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            # A search that never returned is not evidence that your tab emptied,
            # so nothing is reconciled and nothing is marked sold.
            self.state.last_error = str(exc)
            self.state.last_run_at = now
            # A rate limit is a pass that happened and failed, not a pass that
            # never was: anything waiting on the next pass -- the "check now"
            # button -- has to be released by it, with the reason.
            self.state.failed_passes += 1
            self.repo.record_poll_run(
                started_at=started, finished_at=now, league=self.league,
                account=self.account, listings_seen=0, total_reported=0,
                complete=False, error=str(exc),
            )
            return

        previous = list(self.repo.live_tracked_rows(self.account, self.league))
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
        """Fast while anything is young, slow once the book has settled.

        Guarded like the pass itself: this reads the database too, and it is
        called from the loop, so an exception here would end the thread just as
        surely as one in the write. Falling back to the slow cadence is the safe
        answer -- it polls less, never more.
        """
        try:
            rows = self.repo.live_tracked_rows(self.account, self.league)
        except Exception:  # noqa: BLE001 - see above
            return self.cfg.interval_minutes * 60.0
        young = timedelta(minutes=self.cfg.young_listing_minutes)
        if any(now - row.priced_at < young for row in rows):
            return self.cfg.young_poll_interval_minutes * 60.0
        return self.cfg.interval_minutes * 60.0

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        """Is the thread actually alive?

        The question the page has to ask, and the one `self._thread is not None`
        answers wrongly: a thread that has died stays not-None forever, so a
        poller that stopped an hour ago went on reporting itself as started and
        was never replaced. `src/market/passive.py` asks it this way for the
        same reason.
        """
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> "ListingPoller":
        """Poll immediately, then on the cadence, until cancelled.

        The immediate poll is not impatience. Polling stops when the app closes,
        so the upper bound on anything that sold overnight is "sometime before
        you next opened this" -- and every minute between opening the app and the
        first poll widens that bracket for nothing.

        Starting an already-running poller does nothing. Starting one whose
        thread has died starts a new one, which is the difference between a
        watcher that recovers and one that quietly gave up.
        """
        if self.running:
            return self
        self._cancel = threading.Event()
        self._wake = threading.Event()

        def _run() -> None:
            self.state.running = True
            try:
                while not self._cancel.is_set():
                    now = datetime.now(timezone.utc)
                    self.poll_once(now)
                    # Cleared after the pass, not before it: a poke that arrives
                    # while a pass is in flight is answered by that pass, which
                    # is the fresh reading it asked for, rather than by a second
                    # one a moment later at twice the request cost.
                    self._wake.clear()
                    if self._cancel.is_set():
                        break
                    self._wake.wait(self.next_interval_seconds(now))
            finally:
                self.state.running = False

        self._thread = threading.Thread(target=_run, name="listing-poller", daemon=True)
        self._thread.start()
        return self

    @property
    def passes(self) -> int:
        """Passes attempted this session, good and bad.

        One number so a caller waiting for "the next pass, whatever it says" can
        watch a single counter -- a failed pass is an answer too.
        """
        return self.state.polls + self.state.failed_passes

    def poke(self) -> None:
        """Poll now instead of at the end of the cooldown.

        The whole of what a "check now" button needs, and all it may safely do:
        the pass still happens on the poller's own thread, with the poller's own
        database connection.
        """
        self._wake.set()

    def wait_for_pass(self, since: int, timeout: float = 60.0) -> bool:
        """Block until the pass count moves past `since`, or `timeout` elapses.

        For a button that has to be able to say what happened. The poll it is
        waiting on is one search plus a fetch per ten maps, through the rate
        limiter, so the default allows for a limiter that is making it wait.
        """
        deadline = time.monotonic() + timeout
        while self.passes <= since:
            if not self.running or time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True

    def cancel(self) -> None:
        self._cancel.set()
        self._wake.set()  # out of the cooldown at once, not in fifteen minutes


# ----------------------------------------------------------------------
# One poller per account and league, for the life of the process.
# ----------------------------------------------------------------------
_POLLERS: dict[tuple[str, str], ListingPoller] = {}
_POLLERS_LOCK = threading.Lock()


def poller_for(
    account: str, league: str, build: Callable[[], ListingPoller]
) -> ListingPoller:
    """The poller watching this account, started if it is not already running.

    `build` is a thunk rather than a constructed poller because building one
    opens a database connection and an HTTP session, and the common case by far
    is that a poller is already running and neither is wanted.

    A registered poller whose thread has died is restarted rather than handed
    back dead. That is the whole difference between "the switch says it is
    watching" and "it is watching".
    """
    key = (account, league)
    with _POLLERS_LOCK:
        existing = _POLLERS.get(key)
        if existing is not None:
            return existing.start()  # a no-op if it is already alive
        poller = build()
        _POLLERS[key] = poller
        return poller.start()


def stop_poller(account: str, league: str) -> ListingPoller | None:
    """Cancel and forget the poller for this account, if there is one."""
    with _POLLERS_LOCK:
        poller = _POLLERS.pop((account, league), None)
    if poller is not None:
        poller.cancel()
    return poller


def running_poller(account: str, league: str) -> ListingPoller | None:
    """The registered poller, alive or not. `None` if none was ever started."""
    with _POLLERS_LOCK:
        return _POLLERS.get((account, league))
