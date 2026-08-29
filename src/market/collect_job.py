"""A collection run that does not hold the thread that started it.

The sibling of `src/market/run_job.py`, for the other long API job in the app,
and it exists for the same reason: Streamlit runs one script at a time per
session, so a collection that ran inline held the only thread the session had
for as long as GGG's rate limits took -- and a Cancel button drawn beside it was
decoration, because the script could not reach the line that read the click
until the run it was meant to interrupt had already ended.

Two things are specific to collection rather than pricing, and both are about
the database.

**The worker opens its own `Repository`.** `src/ui/common.py::get_repo()` caches
one repository per Streamlit session, and `src/database/models.py::connect()`
passes `check_same_thread=False` with an explicit justification: Streamlit
serializes reruns within a session, so that one connection is never used
concurrently, it only moves between threads. A worker thread is precisely what
breaks that -- the script thread reads counts for the progress panel while the
worker commits listings -- and interleaved execute/commit on a single sqlite3
connection can commit a half-built transaction. Two connections against a
WAL database is the supported arrangement; sharing one across two live threads
is not, so the job never touches the page's repository.

**The connection is closed in a `finally`.** A collection run is started from a
toggle that can be flipped dozens of times in a session, and a leaked
connection per run is a file handle per run, held for as long as the app is up.

Nothing here imports Streamlit -- same rule as `run_job.py`. The handle is an
ordinary object with a thread in it, which is what makes it testable without a
browser, and what lets the passive controller drive it from a loop that has no
session at all.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from src.market.cancel import CancelToken

#: Held for the whole of any read of the market, manual or passive.
#:
#: Two collections at once are two `RateLimiter` instances, and neither one can
#: see the other's requests -- each believes it is the only traffic on the
#: connection, so together they can spend twice GGG's published allowance while
#: both are individually convinced they are obeying it. That is the single
#: easiest way for this tool to look like the badly-behaved scraper its
#: User-Agent goes to some trouble to say it is not.
#:
#: Module-level, because the module is imported once per process and therefore
#: outlives every Streamlit rerun and session. A session-scoped lock would guard
#: nothing: two browser tabs are two sessions against one rate limiter.
COLLECT_LOCK = threading.Lock()


@dataclass
class CollectJob:
    """One in-flight collection: its progress, its result and its off switch."""

    cfg: Any
    limit: int = 500
    #: The latest `ProgressUpdate` from the collector. A single attribute
    #: assignment, so a reader sees either the previous tick or this one and
    #: never a half-built one -- the same trade `MarketRunJob` makes, and for
    #: the same reason: a lock here would be held across the page's whole
    #: render.
    progress: Any = None
    #: The stats dict `collect_from_trade_api` returns. `None` until the run
    #: ends, which is how the panel tells "still going" from "finished with
    #: nothing".
    stats: dict[str, Any] | None = None
    #: The message of anything the run raised. A run that dies must say so on
    #: the page rather than leaving a progress bar frozen at 40%.
    error: str = ""
    #: True while the run is queued behind another collection. Shown, because a
    #: run that has started and made no progress for a minute otherwise looks
    #: broken when it is being polite.
    waiting_for_lock: bool = False
    _cancel: CancelToken = field(default_factory=CancelToken)
    _thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    @classmethod
    def start(
        cls,
        cfg,
        limit: int = 500,
        repo_factory: Callable[[], Any] | None = None,
        collect: Callable[..., dict] | None = None,
    ) -> "CollectJob":
        """Spawn the worker and return immediately.

        `repo_factory` and `collect` are injection points for the tests, which
        must never open a socket. Left alone, the job opens its own `Repository`
        on `cfg.db_path` and calls the real ingest path.
        """
        job = cls(cfg=cfg, limit=limit)
        job._thread = threading.Thread(
            target=job._work,
            args=(repo_factory, collect),
            name="market-collect",
            # Daemon so closing Streamlit does not hang on a run asleep under a
            # rate limit. Everything collected before that point is already
            # committed -- ingest flushes every fifty listings.
            daemon=True,
        )
        job._thread.start()
        return job

    def _work(self, repo_factory=None, collect=None) -> None:
        from src.market.ingest import collect_from_trade_api

        collect = collect or collect_from_trade_api
        if repo_factory is None:
            from src.database.repository import Repository

            def repo_factory():
                return Repository(self.cfg.db_path)

        self.waiting_for_lock = True
        with COLLECT_LOCK:
            self.waiting_for_lock = False
            repo = None
            try:
                repo = repo_factory()
                self.stats = collect(
                    self.cfg,
                    repo,
                    limit=self.limit,
                    progress=self._on_progress,
                    cancel=self._cancel,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced on the page
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                if repo is not None:
                    try:
                        repo.close()
                    except Exception:  # noqa: BLE001 - a closed db is not news
                        pass

    def _on_progress(self, update) -> None:
        self.progress = update

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Ask the run to stop at its next slice or fetch boundary.

        Not an abort. An HTTP request already in flight finishes, and so does
        the current page of results -- which is why the page says "Stopping…"
        rather than claiming the run is over. A rate-limit wait, on the other
        hand, is cut short: see `RateLimiter.acquire`.
        """
        self._cancel.cancel()

    @property
    def token(self) -> CancelToken:
        return self._cancel

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

    @property
    def inserted(self) -> int:
        return int((self.stats or {}).get("inserted", 0))

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
