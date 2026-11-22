"""A cancel has to interrupt the *sleep*, not just be noticed between units.

This is the difference between a Cancel button and a decoration, and the whole
reason `src/market/cancel.py` exists. Its own docstring says it:

    press it during a penalty and nothing happens for half an hour, which
    reads as a broken button

That is what happened. `MarketRunJob` held a bare `threading.Event` and passed
`stop=event.is_set` to the pricer, and `client_for` built its collector with no
token at all -- so `OfficialTradeApiCollector._wait` fell through to a blind
`time.sleep`. The flag was therefore only ever polled *between maps*, while the
minutes on that path are spent asleep inside a rate-limit backoff. GGG's
published search policy is `5:10:60,15:60:300,30:300:1800,...` -- the third
rule carries an 1800-second penalty. A run cancelled during one sat on
"Stopping..." for up to half an hour.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.market.cancel import CancelToken, is_cancelled
from src.market.collectors.official_trade_api import OfficialTradeApiCollector


def _collector(cancel=None):
    return OfficialTradeApiCollector(
        league="Covens", user_agent="test", converter=None, cancel=cancel
    )


# -- the token itself -------------------------------------------------------
def test_sleeping_on_a_token_returns_the_moment_it_is_cancelled():
    token = CancelToken()
    threading.Timer(0.05, token.cancel).start()
    started = time.monotonic()
    token.sleep(30.0)
    assert time.monotonic() - started < 5.0, "it slept through the cancel"


def test_an_uncancelled_sleep_still_waits():
    token = CancelToken()
    started = time.monotonic()
    token.sleep(0.15)
    assert time.monotonic() - started >= 0.1


# -- the collector ----------------------------------------------------------
def test_a_collector_with_a_token_wakes_from_a_backoff():
    """The fix. A rate-limit backoff is where a cancel actually lands."""
    token = CancelToken()
    collector = _collector(token)
    threading.Timer(0.05, token.cancel).start()
    started = time.monotonic()
    collector._wait(30.0)
    assert time.monotonic() - started < 5.0, (
        "the collector slept through the cancel; its token is not being used"
    )


def test_a_collector_without_a_token_cannot_be_woken():
    """Documents the old behaviour, so nobody restores it by accident.

    A short sleep here, because the point is only that it is uninterruptible --
    the real path uses the same code with values up to 1800.
    """
    collector = _collector(None)
    started = time.monotonic()
    collector._wait(0.2)
    assert time.monotonic() - started >= 0.15


def test_the_collector_reports_a_cancel_through_stop():
    token = CancelToken()
    collector = _collector(token)
    assert not collector._stop()
    token.cancel()
    assert collector._stop()


# -- the wiring that was missing --------------------------------------------
def test_client_for_passes_the_token_to_the_collector():
    """It took a token nowhere before, which is where the bug lived."""
    from src.config import Config, _DEFAULTS
    from src.pricing.market_comparison import client_for

    token = CancelToken()
    cfg = Config({**_DEFAULTS, "league": "Covens"})

    class _Repo:
        pass

    import src.pricing.market_comparison as mc

    original = mc.converter_for
    mc.converter_for = lambda _cfg, _repo: None
    try:
        client = client_for(cfg, _Repo(), token)
    finally:
        mc.converter_for = original
    assert client.cancel is token


def test_the_pricer_accepts_a_cancel_token():
    import inspect

    from src.pricing.market_comparison import MarketComparisonPricer

    assert "cancel" in inspect.signature(MarketComparisonPricer.build).parameters


def test_the_job_holds_a_token_not_a_bare_event():
    """An Event can be polled; only a token can be slept on."""
    from src.market.run_job import MarketRunJob

    job = MarketRunJob(state=None, items=[])
    assert isinstance(job._cancel, CancelToken)
    job.cancel()
    assert is_cancelled(job._cancel)


def test_the_job_hands_its_token_to_the_pricers_client():
    """The step that makes the rest of it count.

    The token has to reach the object that actually makes the requests. A job
    that keeps its token to itself is back to polling between maps.
    """
    from src.market.run_job import MarketRunJob

    class _Client:
        cancel = None

    class _Pricer:
        client = _Client()

        def run(self, *_a, **_k):
            return None

    job = MarketRunJob.start(_Pricer(), [])
    job.join(5)
    assert _Pricer.client.cancel is job._cancel


def test_an_existing_token_on_the_client_is_not_overwritten():
    """A caller that already wired its own cancel keeps it."""
    from src.market.run_job import MarketRunJob

    mine = CancelToken()

    class _Client:
        cancel = mine

    class _Pricer:
        client = _Client()

        def run(self, *_a, **_k):
            return None

    job = MarketRunJob.start(_Pricer(), [])
    job.join(5)
    assert _Pricer.client.cancel is mine


# -- the collection loop ----------------------------------------------------
def test_the_collection_loop_stops_mid_cycle_not_only_between_cycles(tmp_path):
    """The other half of the same mistake, in my own script.

    The loop polled its stop file only at cycle boundaries, so asking it to
    stop during a cycle did nothing visible -- the API stayed busy for minutes
    after it had been told to stop. A watcher thread now sets a token the
    collector is holding.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("cl", "scripts/collect_loop.py")
    loop = importlib.util.module_from_spec(spec)
    sys.modules["cl"] = loop
    spec.loader.exec_module(loop)

    stop = tmp_path / "collect_loop.stop"
    token = CancelToken()
    watcher = threading.Thread(
        target=loop._watch_stop_file, args=(stop, token, 0.02), daemon=True
    )
    watcher.start()
    assert not token.is_set()

    stop.write_text("", encoding="utf-8")
    deadline = time.monotonic() + 3.0
    while not token.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert token.is_set(), "the stop file was not noticed during a cycle"
