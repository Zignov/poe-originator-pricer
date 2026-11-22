"""A market comparison run that does not hold the thread that started it.

Streamlit reruns the whole script top to bottom on every widget touch, and it
runs one script at a time per session. A market run that priced a tab inline
therefore held the only thread the session had for as long as it took -- up to
ten minutes under GGG's rate limits -- and during that time there was nobody
left to notice a click. A cancel button drawn next to it would have been a
decoration: the script could not reach the line that read it until the run it
was meant to interrupt had already finished.

So the run goes on a worker thread and the page keeps this handle. Two things
follow from that, and they are the point of the module:

* **Cancelling works.** The flag is a plain `threading.Event` the run checks
  between maps and between pool slices, so a cancel lands within one request
  rather than one run.
* **Results are readable while the run is going.** The worker mutates the very
  `MarketRunState` the page renders from, so every map that comes back is on
  screen the moment the page next draws -- and if the trade API backs up on map
  forty of seventy, the thirty-nine already priced can be read, filtered and
  exported without waiting for, or throwing away, the rest.

Nothing here imports Streamlit. The handle is an ordinary object with a thread
in it, which is what makes it testable without a browser.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from src.market.cancel import CancelToken


def _new_token() -> CancelToken:
    return CancelToken()
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.market.bulk import BulkScan
    from src.market.cancel import CancelToken
    from src.market.live_comparables import MarketProgress, MarketRunState
    from src.ml.features import NormalizedMap
    from src.pricing.market_comparison import MarketComparisonPricer


@dataclass
class MarketRunJob:
    """One in-flight run: its state, its latest progress, and its off switch.

    `state` is live. It is the same object the worker records into, deliberately
    shared rather than copied, because a snapshot handed over at the end is
    exactly what this class exists to avoid.
    """

    state: "MarketRunState"
    items: Sequence["NormalizedMap"]
    progress: "MarketProgress | None" = None
    #: The message of anything the run raised. A run that dies must say so on
    #: the page rather than leaving a progress bar frozen at 40%.
    error: str = ""
    #: A CancelToken, not a bare Event, and the difference is the whole of
    #: whether Cancel works. An Event can only be *polled* between maps; a
    #: token can also be *slept on*, so a cancel landing during a rate-limit
    #: backoff returns from it immediately instead of at the end of it. GGG's
    #: search policy carries an 1800-second penalty, so the old bare Event
    #: meant a cancel pressed at the wrong moment did nothing visible for half
    #: an hour. See src/market/cancel.py.
    _cancel: "CancelToken" = field(default_factory=lambda: _new_token())
    _thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    @classmethod
    def start(
        cls,
        pricer: "MarketComparisonPricer",
        items: Sequence["NormalizedMap"],
        state: "MarketRunState | None" = None,
    ) -> "MarketRunJob":
        from src.market.live_comparables import MarketRunState

        job = cls(state=state or MarketRunState(), items=list(items))
        # Hand the token to the collector the pricer will actually make its
        # requests through. Passing `stop=` alone (below) only lets the run be
        # polled between maps; the collector needs the token itself so its
        # rate-limit sleeps are waited on rather than slept through. Without
        # this the Cancel button stalls on "Stopping..." for the length of a
        # backoff, which GGG's policy allows to be 1800 seconds.
        client = getattr(pricer, "client", None)
        if client is not None and getattr(client, "cancel", None) is None:
            client.cancel = job._cancel
        job._thread = threading.Thread(
            target=job._work,
            args=(pricer,),
            name="market-run",
            # Daemon so closing Streamlit does not hang on a run that is asleep
            # under a rate limit. Everything worth keeping is either already in
            # `state` or already in the bucket cache on disk.
            daemon=True,
        )
        job._thread.start()
        return job

    def _work(self, pricer: "MarketComparisonPricer") -> None:
        try:
            pricer.run(
                self.items,
                state=self.state,
                progress=self._on_progress,
                stop=self._cancel.is_set,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced on the page, not swallowed
            self.error = f"{type(exc).__name__}: {exc}"

    def _on_progress(self, update: "MarketProgress") -> None:
        # A single attribute assignment, so the reader either sees the previous
        # update or this one and never a half-built one. No lock is needed for
        # that, and a lock here would be held across the reader's whole render.
        self.progress = update

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Ask the run to stop after the request it is in the middle of.

        Not an abort: the HTTP call already on the wire still finishes. A
        rate-limit sleep does not -- the token is waited on rather than slept
        through, so a cancel cuts it short.
        """
        self._cancel.cancel()

    @property
    def cancelling(self) -> bool:
        return self._cancel.is_set() and self.running

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def finished(self) -> bool:
        return not self.running

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


@dataclass
class BulkScanJob:
    """One in-flight bulk scan: its progress, its result, and its off switch.

    The same problem `MarketRunJob` solves, in a smaller place. The bulk scan
    used to run inline inside `st.spinner`, which holds the session's only
    script thread for the whole scan -- so a Cancel button drawn beside it
    could never be read, because the script could not reach the line that reads
    it until the scan had already finished.

    It matters more here than the duration suggests. A bulk scan is one search
    and a handful of fetches, which is seconds when the connection is idle --
    but the fetch loop answers a rate limit by backing off and trying the next
    batch, and when something else is already using the API (a collection run,
    say) those backoffs stack up into minutes of a spinner with no way out.
    That is exactly when someone wants to stop and go to the trade site
    instead, and exactly when they could not.
    """

    #: Set once the scan returns. `None` while it is still going.
    scan: "BulkScan | None" = None
    message: str = ""
    error: str = ""
    _cancel: "CancelToken | None" = None
    _thread: threading.Thread | None = None

    @classmethod
    def start(cls, collector, **kwargs) -> "BulkScanJob":
        """Run `scan_bulk_lots(collector, **kwargs)` on a worker thread.

        `collector` must have been built with this job's token as its `cancel`,
        which is what lets a cancel land inside a request or a rate-limit sleep
        rather than only between batches.
        """
        from src.market.bulk import scan_bulk_lots

        job = cls(_cancel=collector.cancel)

        def work() -> None:
            try:
                job.scan = scan_bulk_lots(collector, progress=job._on_progress, **kwargs)
            except Exception as exc:  # noqa: BLE001 - surfaced on the page
                job.error = f"{type(exc).__name__}: {exc}"

        job._thread = threading.Thread(target=work, name="bulk-scan", daemon=True)
        job._thread.start()
        return job

    def _on_progress(self, message) -> None:
        # One assignment, so a reader sees either the old message or the new
        # one and never a half-written one. See MarketRunJob._on_progress.
        self.message = str(message)

    def cancel(self) -> None:
        """Ask the scan to stop. Whatever it has already read is kept."""
        if self._cancel is not None:
            self._cancel.cancel()

    @property
    def cancelling(self) -> bool:
        return bool(self._cancel) and self.running

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def finished(self) -> bool:
        return not self.running

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
