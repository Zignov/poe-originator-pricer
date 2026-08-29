"""Collecting on a slow loop, unattended, for as long as the app is open.

The manual collect is a good tool for filling a gap you know about. It is a bad
tool for the thing the dataset actually needs, which is a steady trickle: the
listing book turns over, a model trained on last week's asks prices this week's
maps badly, and nobody remembers to press a button every half hour.

So: scan, go quiet long enough for the rate limit to forget the scan happened,
scan again. One cycle is roughly four hundred maps for about fifty requests,
which is well inside GGG's published allowance -- the cooldown is not what keeps
this legal, `RateLimiter` is, unconditionally and from the server's own headers.
The cooldown is what keeps it *polite*, and what makes the traffic look like
what it is rather than like someone draining the index.

Where this lives, and why it is not somewhere better
----------------------------------------------------
A process-global singleton, which is not a thing to reach for lightly. The
alternatives are worse:

* **Session state** dies with the browser tab and cannot outlive a rerun, and
  the Streamlit script thread only runs when a widget is touched. A loop that
  sleeps thirty minutes cannot live there at all.
* **A separate process, or an OS timer**, would keep collecting after the app
  is closed. That is the one behaviour this must not have. `src/config.py`'s
  User-Agent docstring goes to some trouble to make this tool identifiable and
  well-behaved, and a background job the user has closed the app on -- and
  therefore believes is stopped -- collecting anyway is precisely the thing
  that gets a project's User-Agent blocked.

The module is imported once per process, so the controller survives every rerun,
page navigation and browser refresh, and the Market Data page is a *view* of it
rather than its owner. Two browser tabs see one collector, which is correct:
there is one rate limit.

The switch is deliberately not persisted, though the settings are. A collector
that restarts itself on launch because a YAML file said so is the same failure
as the separate process, one step removed.

Nothing here imports Streamlit. Everything the page needs is a plain attribute
read off `PassiveState`, and the one thing the worker cannot do for itself --
dropping Streamlit's model caches after a retrain -- it signals by bumping a
counter the page watches. `invalidate_model_cache()` touches `st.session_state`
and calls `.clear()` on `@st.cache_resource` objects; neither is legal off the
script thread.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.market.cancel import CancelToken
from src.market.collect_job import COLLECT_LOCK

#: Phases, in the order one cycle passes through them.
IDLE = "idle"
WAITING = "waiting"
SCANNING = "scanning"
TRAINING = "training"
COOLING = "cooling"
STOPPED = "stopped"


@dataclass(frozen=True)
class PassiveSettings:
    """The four knobs, and the one that decides whether the model keeps up."""

    #: Listings per scan. Not requests: a scan spends roughly one search per
    #: query slice plus one fetch per ten results, so four hundred maps is
    #: about fifty-six requests.
    maps_per_scan: int = 400
    #: How long to stay off the wire between scans.
    cooldown_minutes: float = 30.0
    #: Switch off by itself after this long. 0 means never, which is the
    #: default because "until I close the app" is the honest lifetime anyway.
    auto_off_after_hours: float = 0.0
    auto_retrain: bool = True
    #: Retrain every N scans rather than every scan, because training is a
    #: backend bake-off measured in minutes and four hundred new listings do
    #: not move a five-thousand-row fit.
    retrain_every_scans: int = 4

    @classmethod
    def from_config(cls, cfg) -> "PassiveSettings":
        get = cfg.get
        return cls(
            maps_per_scan=int(get("collection.passive.maps_per_scan", 400)),
            cooldown_minutes=float(get("collection.passive.cooldown_minutes", 30)),
            auto_off_after_hours=float(get("collection.passive.auto_off_after_hours", 0)),
            auto_retrain=bool(get("collection.passive.auto_retrain", True)),
            retrain_every_scans=int(get("collection.passive.retrain_every_scans", 4)),
        )


@dataclass
class PassiveState:
    """What the page reads. Plain attributes, written by one thread only."""

    phase: str = IDLE
    scans_done: int = 0
    listings_this_scan: int = 0
    listings_total: int = 0
    #: `time.monotonic()` of the next scan, while cooling. Monotonic rather
    #: than wall clock so a laptop suspending does not produce a countdown that
    #: runs backwards.
    next_scan_at: float | None = None
    started_at: float = 0.0
    stop_at: float | None = None
    stopped_reason: str = ""
    last_error: str = ""
    #: Bumped after each successful retrain. The page compares it against its
    #: own watermark and drops the model caches on the script thread, which is
    #: the only thread allowed to touch them.
    model_generation: int = 0

    def seconds_until_next_scan(self, now: float | None = None) -> float:
        if self.next_scan_at is None:
            return 0.0
        return max(0.0, self.next_scan_at - (now if now is not None else time.monotonic()))


class PassiveCollector:
    """The loop. One per process; get it from `controller()`."""

    def __init__(self) -> None:
        self._state = PassiveState()
        self._cancel = CancelToken()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def state(self) -> PassiveState:
        """The live object, not a copy: the page renders straight off it."""
        return self._state

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        cfg,
        settings: PassiveSettings | None = None,
        *,
        collect: Callable[..., dict] | None = None,
        train: Callable[..., Any] | None = None,
        repo_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start the loop if it is not already going.

        The injection points exist so the tests can run a day of cycles in
        milliseconds against a stub collector. Nothing here opens a socket by
        itself.
        """
        with self._lock:
            if self.running:
                return
            settings = settings or PassiveSettings.from_config(cfg)
            now = clock()
            self._cancel = CancelToken()
            self._state = PassiveState(
                phase=WAITING,
                started_at=now,
                stop_at=(
                    now + settings.auto_off_after_hours * 3600.0
                    if settings.auto_off_after_hours > 0
                    else None
                ),
            )
            self._thread = threading.Thread(
                target=self._loop,
                args=(cfg, settings, collect, train, repo_factory, clock),
                name="market-passive",
                daemon=True,
            )
            self._thread.start()

    def stop(self, reason: str = "switched off") -> None:
        """Ask the loop to stop, and wake it if it is in a cooldown.

        The wake is the point. A thirty-minute `sleep` would mean the toggle
        took up to half an hour to visibly do anything, and a switch that does
        not switch anything off gets pressed again and again.
        """
        self._state.stopped_reason = reason
        self._cancel.cancel()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------
    def _loop(self, cfg, settings, collect, train, repo_factory, clock) -> None:
        state = self._state
        try:
            while not self._cancel.is_set():
                if state.stop_at is not None and clock() >= state.stop_at:
                    state.stopped_reason = (
                        f"switched itself off after {settings.auto_off_after_hours:g}h"
                    )
                    break

                inserted = self._scan(cfg, settings, collect, repo_factory)
                if self._cancel.is_set():
                    break
                state.scans_done += 1

                if self._should_train(settings, state, inserted):
                    self._train(cfg, train, state)
                if self._cancel.is_set():
                    break

                state.phase = COOLING
                state.next_scan_at = clock() + settings.cooldown_minutes * 60.0
                # Interruptible: this is what makes `stop()` immediate.
                self._cancel.sleep(settings.cooldown_minutes * 60.0)
        except Exception as exc:  # noqa: BLE001 - surfaced on the page
            state.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            state.phase = STOPPED
            state.next_scan_at = None
            if not state.stopped_reason:
                state.stopped_reason = "stopped"

    def _scan(self, cfg, settings, collect, repo_factory) -> int:
        from src.market.ingest import collect_from_trade_api

        collect = collect or collect_from_trade_api
        state = self._state
        state.phase = WAITING
        # Behind the same lock the manual collect takes, so the two never read
        # the market at once -- see COLLECT_LOCK's docstring. During a cooldown
        # the lock is free, so a manual run never queues behind a sleeping one.
        with COLLECT_LOCK:
            if self._cancel.is_set():
                return 0
            state.phase = SCANNING
            state.listings_this_scan = 0
            repo = None
            try:
                if repo_factory is None:
                    from src.database.repository import Repository

                    repo = Repository(cfg.db_path)
                else:
                    repo = repo_factory()
                stats = collect(
                    cfg,
                    repo,
                    limit=settings.maps_per_scan,
                    progress=self._on_progress,
                    cancel=self._cancel,
                ) or {}
                inserted = int(stats.get("inserted", 0))
                state.listings_this_scan = inserted
                state.listings_total += inserted
                if stats.get("error"):
                    state.last_error = str(stats["error"])
                return inserted
            except Exception as exc:  # noqa: BLE001 - one bad scan is not fatal
                state.last_error = f"{type(exc).__name__}: {exc}"
                return 0
            finally:
                if repo is not None:
                    try:
                        repo.close()
                    except Exception:  # noqa: BLE001 - a closed db is not news
                        pass

    @staticmethod
    def _should_train(settings, state, inserted: int) -> bool:
        """Only on the interval, and only if the scans actually found anything.

        The second half matters more than it looks. A league that has gone
        quiet returns the same listings every scan, and retraining on an
        unchanged dataset spends minutes to produce the model that is already
        loaded -- while dropping every page's caches to do it.
        """
        if not settings.auto_retrain or inserted <= 0:
            return False
        return state.scans_done % max(1, settings.retrain_every_scans) == 0

    def _train(self, cfg, train, state) -> None:
        if train is None:
            from src.ml.train import train as train  # noqa: PLW0127

        state.phase = TRAINING
        try:
            train(cfg)
        except Exception as exc:  # noqa: BLE001 - NotEnoughData included
            # Deliberately not fatal. Too little data to fit is a reason to
            # keep collecting, not a reason to stop.
            state.last_error = f"{type(exc).__name__}: {exc}"
            return
        state.model_generation += 1

    def _on_progress(self, update) -> None:
        collected = getattr(update, "collected", None)
        if collected is not None:
            self._state.listings_this_scan = int(collected)


_CONTROLLER: PassiveCollector | None = None
_CONTROLLER_LOCK = threading.Lock()


def controller() -> PassiveCollector:
    """The one collector in this process.

    Built on first use rather than at import, so importing this module -- which
    the Market Data page does on every rerun -- never starts anything.
    """
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = PassiveCollector()
        return _CONTROLLER
