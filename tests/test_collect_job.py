"""The collection run as a handle rather than a blocking call.

Same argument as `test_market_run_cancel.py` makes for the pricing run: Streamlit
gives a session one script thread, so a collection that ran inline held it for
the length of a rate-limited walk and nothing could notice a click until the run
was over. A Cancel button in front of an inline run is a decoration.

The database half is specific to collection and is the part most likely to be
got wrong quietly. The page caches one `Repository` per session and the
connection is opened `check_same_thread=False` on the stated assumption that
Streamlit serializes reruns, so the connection only ever *moves* between
threads. A worker thread breaks that assumption -- the page reads counts for the
progress panel while the worker commits listings -- and interleaved
execute/commit on one sqlite3 connection can commit a half-built transaction.
So the worker opens its own.

Nothing here touches the network: the ingest call is injected.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.database.repository import Repository
from src.market.collect_job import COLLECT_LOCK, CollectJob


@dataclass
class StubConfig:
    """Everything `CollectJob` reads off a config, and nothing else."""

    db_path: Path


def _slow_collect(delay: float = 0.05, listings: int = 3):
    def collect(cfg, repo, limit=500, progress=None, cancel=None):
        for i in range(listings):
            if cancel is not None and cancel.is_set():
                return {"inserted": i, "updated": 0, "cancelled": True}
            time.sleep(delay)
            if progress:
                progress(f"collected {i + 1}")
        return {"inserted": listings, "updated": 0, "cancelled": False}

    return collect


def test_the_collect_job_does_not_hold_the_thread_that_started_it(tmp_path):
    job = CollectJob.start(
        StubConfig(tmp_path / "job.db"),
        limit=100,
        repo_factory=lambda: None,
        collect=_slow_collect(),
    )

    # `start` returned while the walk is still going -- which is the entire
    # point: in Streamlit that thread is the one that draws the page, including
    # the Cancel button.
    assert job.running
    job.join(timeout=10)
    assert job.finished
    assert job.stats == {"inserted": 3, "updated": 0, "cancelled": False}


def test_the_worker_opens_its_own_database_connection_rather_than_sharing_one(tmp_path):
    """Two threads on one sqlite3 connection is the failure that corrupts data.

    Not a crash: interleaved execute/commit on a shared connection can commit a
    transaction another thread was half way through building. The page's
    repository must never reach the worker.
    """
    db = tmp_path / "shared.db"
    page_repo = Repository(db)
    seen: dict = {}

    def collect(cfg, repo, limit=500, progress=None, cancel=None):
        seen["repo"] = repo
        seen["conn"] = repo.conn
        seen["thread"] = threading.current_thread().name
        return {"inserted": 0, "updated": 0, "cancelled": False}

    # No repo_factory: this is the real one, which is what is being asserted.
    job = CollectJob.start(StubConfig(db), limit=10, collect=collect)
    job.join(timeout=10)

    assert not job.error, job.error
    assert seen["repo"] is not page_repo
    assert seen["conn"] is not page_repo.conn
    assert seen["thread"] != threading.current_thread().name

    # And closed on the way out. A toggle that can be flipped dozens of times a
    # session must not leak a file handle per run.
    try:
        seen["conn"].execute("SELECT 1")
    except Exception:
        pass
    else:  # pragma: no cover - only reached if the finally is missing
        raise AssertionError("the worker left its connection open")

    # The page's own connection is untouched by any of that.
    assert page_repo.count_listings("Allflame")["total"] == 0
    page_repo.close()


def test_a_collect_job_that_raises_reports_the_error_instead_of_freezing_the_bar(
    tmp_path,
):
    """A run that dies has to say so.

    The panel polls `running`; a job that swallowed the exception would go from
    "in progress" to "not running" with no result and no message, which on
    screen is a progress bar stuck at 40% forever.
    """

    def boom(cfg, repo, limit=500, progress=None, cancel=None):
        raise RuntimeError("the trade API fell over")

    job = CollectJob.start(
        StubConfig(tmp_path / "boom.db"), repo_factory=lambda: None, collect=boom
    )
    job.join(timeout=10)

    assert job.finished
    assert "the trade API fell over" in job.error
    assert job.stats is None


def test_cancelling_the_job_reaches_the_collector(tmp_path):
    job = CollectJob.start(
        StubConfig(tmp_path / "cancel.db"),
        repo_factory=lambda: None,
        collect=_slow_collect(delay=0.05, listings=40),
    )
    deadline = time.monotonic() + 10
    while job.progress is None and time.monotonic() < deadline:
        time.sleep(0.01)
    job.cancel()
    job.join(timeout=10)

    assert job.cancelled
    assert job.finished
    assert job.stats is not None and job.stats["cancelled"] is True
    assert job.stats["inserted"] < 40, "the cancel never reached the walk"


def test_the_job_releases_the_shared_collection_lock_when_it_ends(tmp_path):
    """The lock is what stops two limiters spending GGG's allowance twice.

    It is process-wide and outlives the run, so a job that failed to release it
    -- on the error path especially -- would wedge every later collection,
    manual and passive alike, for the lifetime of the app.
    """

    def boom(cfg, repo, limit=500, progress=None, cancel=None):
        raise RuntimeError("nope")

    job = CollectJob.start(
        StubConfig(tmp_path / "lock.db"), repo_factory=lambda: None, collect=boom
    )
    job.join(timeout=10)

    assert COLLECT_LOCK.acquire(timeout=1), "the lock was not released"
    COLLECT_LOCK.release()
