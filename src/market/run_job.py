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
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
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
    _cancel: threading.Event = field(default_factory=threading.Event)
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

        Not an abort: an in-flight HTTP call and a rate-limit sleep both finish
        first, which is why the page says "finishing the current request" rather
        than claiming the run has already stopped.
        """
        self._cancel.set()

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
