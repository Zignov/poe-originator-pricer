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
"""
from __future__ import annotations

import threading
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

    # ------------------------------------------------------------------
    def poll_once(self, now: datetime) -> None:
        """One pass. Records the run whether it worked or not."""
        started = now
        try:
            snapshot = self.source(self.account)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            # A search that never returned is not evidence that your tab emptied,
            # so nothing is reconciled and nothing is marked sold.
            self.state.last_error = str(exc)
            self.state.last_run_at = now
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
        """Fast while anything is young, slow once the book has settled."""
        rows = self.repo.live_tracked_rows(self.account, self.league)
        young = timedelta(minutes=self.cfg.young_listing_minutes)
        if any(now - row.priced_at < young for row in rows):
            return self.cfg.young_poll_interval_minutes * 60.0
        return self.cfg.interval_minutes * 60.0

    # ------------------------------------------------------------------
    def start(self) -> "ListingPoller":
        """Poll immediately, then on the cadence, until cancelled.

        The immediate poll is not impatience. Polling stops when the app closes,
        so the upper bound on anything that sold overnight is "sometime before
        you next opened this" -- and every minute between opening the app and the
        first poll widens that bracket for nothing.
        """
        if self._thread is not None:
            return self

        def _run() -> None:
            self.state.running = True
            try:
                while not self._cancel.is_set():
                    now = datetime.now(timezone.utc)
                    self.poll_once(now)
                    self._cancel.wait(self.next_interval_seconds(now))
            finally:
                self.state.running = False

        self._thread = threading.Thread(target=_run, name="listing-poller", daemon=True)
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()
