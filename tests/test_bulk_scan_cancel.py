"""Cancelling a bulk scan, and keeping what it already read.

The scan used to run inline inside `st.spinner`. Streamlit runs one script at a
time per session, so that held the session's only thread for the whole scan and
a Cancel button drawn beside it could never have been read -- the script could
not reach the line that reads it until the scan it was meant to stop had
finished.

Duration is not the reason it matters. A bulk scan is one search and a few
fetches, which is seconds on an idle connection. But the fetch loop answers a
rate limit by backing off and trying the next batch, so when something else is
already using the API those backoffs stack into minutes of a spinner with no
way out -- which is exactly the moment someone wants to give up and go to the
trade site instead.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.market.bulk import BulkScan, scan_bulk_lots
from src.market.cancel import CancelToken
from src.market.collectors.official_trade_api import CollectionCancelled, TradeApiError
from src.market.run_job import BulkScanJob


class _Converter:
    """`to_chaos`, which is all `_to_bulk_listing` asks of it."""

    @staticmethod
    def to_chaos(amount, currency):
        return float(amount or 0) * (203.0 if currency == "divine" else 1.0)


class _Collector:
    """Enough of OfficialTradeApiCollector to drive scan_bulk_lots."""

    league = "Covens"
    converter = _Converter()

    def __init__(self, ids=60, cancel=None, fail_fetch=False, block=None):
        self.cancel = cancel
        self._ids = [f"id-{i}" for i in range(ids)]
        self.fail_fetch = fail_fetch
        self.block = block
        self.fetches = 0
        self.searched = False

    def build_query(self, **_kwargs):
        return {"query": {"filters": {}}}

    def available(self):
        return True, ""

    def search(self, _query):
        if self.cancel is not None and self.cancel.is_set():
            raise CollectionCancelled("cancelled before the search went out")
        self.searched = True
        return "qid", list(self._ids), len(self._ids)

    def fetch(self, batch, _query_id):
        self.fetches += 1
        if self.block is not None:
            self.block.wait(5)
        if self.cancel is not None and self.cancel.is_set():
            raise CollectionCancelled("cancelled before the fetch went out")
        if self.fail_fetch:
            raise TradeApiError("rate limited by GGG on fetch; backing off")
        return [
            {
                "listing": {"account": {"name": f"Seller{i % 3}#1"},
                            "price": {"amount": 10, "currency": "chaos"}},
                "item": {"properties": [], "typeLine": "Map"},
            }
            for i, _ in enumerate(batch)
        ]


# -- the scan itself --------------------------------------------------------
def test_a_cancel_before_the_search_yields_an_empty_cancelled_scan():
    token = CancelToken()
    token.cancel()
    scan = scan_bulk_lots(_Collector(cancel=token), min_lot=1)
    assert scan.cancelled
    assert scan.lots == []
    assert not scan.error, "a cancel is not an error"


def test_a_cancel_mid_fetch_keeps_what_was_already_read():
    """The principle the collection runs already follow: keep what you paid for.

    Under a rate limit the batches already fetched are often the whole useful
    answer, and throwing them away to report a clean "cancelled" would waste
    requests the user has spent.
    """
    token = CancelToken()

    class _CancelAfterFirst(_Collector):
        def fetch(self, batch, query_id):
            if self.fetches >= 1:
                token.cancel()
            return super().fetch(batch, query_id)

    scan = scan_bulk_lots(_CancelAfterFirst(ids=60, cancel=token), min_lot=1)
    assert scan.cancelled
    assert scan.listings_seen > 0, "the first batch was thrown away"
    assert scan.lots, "sellers read before the cancel should still be grouped"


def test_an_uncancelled_scan_is_not_marked_cancelled():
    scan = scan_bulk_lots(_Collector(ids=20), min_lot=1)
    assert not scan.cancelled
    assert scan.listings_seen == 20


def test_a_rate_limit_is_not_a_cancel():
    """`TradeApiError` means back off and try the next batch; the loop goes on.

    `CollectionCancelled` is deliberately not a TradeApiError for exactly this
    reason, and conflating them would make a cancel invisible under a rate
    limit -- the one condition where it is most wanted.
    """
    collector = _Collector(ids=30, fail_fetch=True)
    scan = scan_bulk_lots(collector, min_lot=1)
    assert not scan.cancelled
    assert scan.listings_seen == 0
    assert collector.fetches == 3, "it should have tried every batch"


# -- the job ----------------------------------------------------------------
def _await(job, timeout=5.0):
    deadline = time.monotonic() + timeout
    while job.running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not job.running, "the scan thread did not finish"


def test_the_job_runs_off_the_calling_thread():
    """The whole point: the caller stays free to read a button."""
    gate = threading.Event()
    collector = _Collector(ids=20, cancel=CancelToken(), block=gate)
    job = BulkScanJob.start(collector, min_lot=1)
    assert job.running, "start() blocked instead of returning a handle"
    gate.set()
    _await(job)
    assert job.scan is not None


def test_cancelling_the_job_stops_the_scan():
    gate = threading.Event()
    collector = _Collector(ids=60, cancel=CancelToken(), block=gate)
    job = BulkScanJob.start(collector, min_lot=1)
    job.cancel()
    gate.set()
    _await(job)
    assert job.scan is not None and job.scan.cancelled


def test_a_finished_job_reports_its_scan_not_an_error():
    job = BulkScanJob.start(_Collector(ids=20, cancel=CancelToken()), min_lot=1)
    _await(job)
    assert job.error == ""
    assert isinstance(job.scan, BulkScan)


def test_a_raising_scan_is_reported_rather_than_swallowed():
    """A scan that dies must say so, not leave a spinner going forever."""

    class _Broken(_Collector):
        def search(self, _query):
            raise ValueError("boom")

    job = BulkScanJob.start(_Broken(cancel=CancelToken()), min_lot=1)
    _await(job)
    assert "boom" in job.error
    assert job.scan is None


def test_progress_messages_reach_the_handle():
    """They are what the live panel shows instead of a bare spinner."""
    job = BulkScanJob.start(_Collector(ids=20, cancel=CancelToken()), min_lot=1)
    _await(job)
    assert job.message, "no progress was ever reported"


def test_the_job_thread_is_a_daemon():
    """Closing the app must not hang on a scan asleep under a rate limit."""
    job = BulkScanJob.start(_Collector(ids=10, cancel=CancelToken()), min_lot=1)
    assert job._thread.daemon
    _await(job)


def test_cancelling_is_true_only_while_it_is_still_running():
    gate = threading.Event()
    job = BulkScanJob.start(
        _Collector(ids=60, cancel=CancelToken(), block=gate), min_lot=1
    )
    job.cancel()
    assert job.cancelling, "a cancel in flight should say so"
    gate.set()
    _await(job)
    assert not job.cancelling, "a finished scan is not still cancelling"


# -- the page contract ------------------------------------------------------
def test_the_page_no_longer_scans_on_the_script_thread():
    """A source check: the bug was a blocking call, not a wrong value."""
    from pathlib import Path

    from conftest import code_only

    code = code_only(Path("src/ui/bulk_buy.py"))
    assert "BulkScanJob" in code, "the page does not use the threaded job"
    assert "scan_bulk_lots" not in code, (
        "the page calls the scan directly again, which blocks the only thread "
        "the session has and makes the Cancel button unreadable"
    )


def test_the_page_offers_a_cancel_button():
    from pathlib import Path

    source = Path("src/ui/bulk_buy.py").read_text(encoding="utf-8")
    assert "Cancel scan" in source
