"""Stopping a market run, and reading it while it is still going.

The market mode is the one part of the app that can hold a connection for ten
minutes, and the two things that made it unpleasant were both about *not being
able to get at it*: there was no way to stop it, and no way to see what it had
found until it was over. If the trade API backed up forty maps into a seventy-map
tab you either waited it out or closed the page and lost the wait.

So these tests are about the escape hatch rather than the pricing:

* a cancel lands between maps, and between pool slices -- the phase where the
  wall-clock actually goes;
* nothing already paid for is thrown away by one, and a resumed run does not
  buy it again;
* the run happens off the caller's thread, so its results are readable while it
  is still running.

Like `test_market_comparison.py`, nothing here touches the network -- it drives
the same `FakeTradeClient`.
"""
from __future__ import annotations

import threading
import time

from src.market.live_comparables import (
    BucketCache,
    MapOutcome,
    MarketRunState,
    RequestBudget,
)
from src.market.run_job import MarketRunJob
from src.pricing.market_comparison import MarketComparisonPricer, MarketComparisonSettings

from tests.test_market_comparison import (
    NOW,
    FakeTradeClient,
    a_map,
    api_result,
    finder_for,
)


def _pricer(client, tmp_path, budget: int = 100, **settings_overrides):
    """A pricer over a fake client, per-map rather than pooled.

    Pooling is off so the tests can count maps: a pooled run spends its requests
    up front and then prices out of memory, which is the wrong shape for
    asserting that a cancel landed *between two maps*. The pool has its own test.
    """
    from src.config import Config, _DEFAULTS
    from src.database.repository import Repository
    from src.pricing.currency import CurrencyConverter
    from src.pricing.fair_value import PricingService

    cfg = Config({**_DEFAULTS, "league": "Allflame"})
    repo = Repository(tmp_path / "test.db")
    service = PricingService(cfg, repo, None, CurrencyConverter(210.0))
    settings_overrides.setdefault("pool", False)
    settings = MarketComparisonSettings(
        request_budget=budget,
        cache_path=str(tmp_path / "cache.json"),
        **settings_overrides,
    )
    return MarketComparisonPricer(
        service=service,
        client=client,
        settings=settings,
        cache=BucketCache(ttl_minutes=30, path=tmp_path / "cache.json", now=lambda: NOW),
        budget=RequestBudget(limit=budget),
    )


def _distinct_maps(count: int):
    """Maps different enough that each needs its own search, not a cached bucket."""
    return [a_map(quantity=80 + i * 6, more_currency=40 + i * 5) for i in range(count)]


def _listings():
    return [[api_result(f"a{i}", 40 + i) for i in range(8)]]


# ---------------------------------------------------------------------------
# Cancelling the per-map loop
# ---------------------------------------------------------------------------
def test_a_cancel_stops_the_run_and_keeps_every_map_already_priced(tmp_path):
    items = _distinct_maps(4)
    pricer = _pricer(FakeTradeClient(_listings()), tmp_path)

    # Stop once the first map is in: the check happens at the top of each
    # iteration, so this cancels going into the second.
    state = MarketRunState()
    state_ref = pricer.run(items, state=state, stop=lambda: len(state.outcomes) >= 1)

    assert state_ref.priced_count == 1
    assert len(state_ref.pending(items)) == 3


def test_a_cancelled_run_says_what_it_kept_and_what_is_left(tmp_path):
    items = _distinct_maps(4)
    state = MarketRunState()
    pricer = _pricer(FakeTradeClient(_listings()), tmp_path)
    pricer.run(items, state=state, stop=lambda: len(state.outcomes) >= 1)

    reason = state.stopped_reason
    assert "Cancelled" in reason
    assert "1 of 4" in reason, reason
    assert "3 left" in reason, reason
    # The point of the message is that stopping is safe and resumable.
    assert "Continue pricing" in reason


def test_cancelling_before_the_first_map_still_explains_itself(tmp_path):
    items = _distinct_maps(3)
    state = MarketRunState()
    pricer = _pricer(FakeTradeClient(_listings()), tmp_path)
    pricer.run(items, state=state, stop=lambda: True)

    assert state.priced_count == 0
    assert "Cancelled" in state.stopped_reason
    assert not state.outcomes, "a cancelled-on-entry run should not have priced anything"


def test_a_cancelled_run_resumes_rather_than_starting_over(tmp_path):
    items = _distinct_maps(3)
    client = FakeTradeClient(_listings())
    state = MarketRunState()
    _pricer(client, tmp_path).run(items, state=state, stop=lambda: len(state.outcomes) >= 1)
    assert state.priced_count == 1
    first = state.get(items[0])
    searches_after_cancel = len(client.searches)

    # A fresh pricer and budget, the same state -- the way the Continue button
    # does it after a cancel.
    state = _pricer(client, tmp_path).run(items, state=state)

    assert state.priced_count == 3
    assert not state.pending(items)
    # The same outcome object, not an equal one re-derived: the resumed run
    # skipped the map the cancelled one had already paid for.
    assert state.get(items[0]) is first
    assert len(client.searches) <= searches_after_cancel + 2, (
        "the resumed run searched for more maps than were left pending"
    )


def test_a_cancelled_run_writes_its_cache_so_the_requests_are_not_paid_twice(tmp_path):
    items = _distinct_maps(3)
    client = FakeTradeClient(_listings())
    state = MarketRunState()
    _pricer(client, tmp_path).run(items, state=state, stop=lambda: len(state.outcomes) >= 1)
    searches_after_cancel = len(client.searches)

    # A run with no memory of the state at all -- only the cache file on disk.
    fresh = _pricer(client, tmp_path)
    fresh.run(items[:1])

    assert len(client.searches) == searches_after_cancel, (
        "the bucket the cancelled run paid for was not written to disk"
    )
    assert fresh.budget.used == 0


# ---------------------------------------------------------------------------
# Cancelling the pool, which is where the wall-clock actually goes
# ---------------------------------------------------------------------------
def test_the_pool_stops_part_way_and_keeps_the_listings_it_already_read():
    finder = finder_for(_listings(), budget=200)
    slices = [{"sort_by": "indexed"} for _ in range(6)]

    seen = []
    pool = finder.build_pool(
        slices=slices,
        per_slice=10,
        on_slice=lambda done, total, found: seen.append(done),
        stop=lambda: len(seen) >= 2,
    )

    assert pool.stopped
    assert pool.slices_used == 2, "the cancel did not land between slices"
    assert len(pool) > 0, "the listings already read were thrown away"


def test_a_pool_that_is_not_cancelled_does_not_claim_it_was():
    finder = finder_for(_listings(), budget=200)
    pool = finder.build_pool(slices=[{"sort_by": "indexed"}], per_slice=10)
    assert not pool.stopped


def test_the_pool_is_cancellable_through_the_pricer(tmp_path):
    """The `stop` the caller hands `run` has to reach the pooling phase.

    It did not, originally, and that was the bug that mattered: pooling is
    sixteen rate-limited searches and eighty fetches *before the first map is
    priced*, so a cancel that only took effect afterwards was no cancel at all.
    """
    items = _distinct_maps(3)
    client = FakeTradeClient(_listings())
    pricer = _pricer(client, tmp_path, pool=True)
    # Force the pooled path regardless of what the cost comparison prefers.
    pricer.should_pool = lambda maps: maps > 0

    state = pricer.run(items, stop=lambda: True)

    assert client.searches == [], "a cancelled run still read the market"
    assert "Cancelled" in state.stopped_reason


# ---------------------------------------------------------------------------
# The run off the script thread
# ---------------------------------------------------------------------------
class SlowTradeClient(FakeTradeClient):
    """A fake that takes its time, so a test can look at a run in progress."""

    def __init__(self, pages, delay: float = 0.05):
        super().__init__(pages)
        self.delay = delay

    def search(self, query):
        time.sleep(self.delay)
        return super().search(query)


def test_the_job_does_not_hold_the_thread_that_started_it(tmp_path):
    items = _distinct_maps(8)
    job = MarketRunJob.start(_pricer(SlowTradeClient(_listings()), tmp_path), items)

    # `start` returned while the run is still going -- which is the entire point:
    # in Streamlit that thread is the one that draws the page.
    assert job.running
    job.join(timeout=10)
    assert job.finished
    assert job.state.priced_count == 8


def test_results_are_readable_while_the_run_is_still_going(tmp_path):
    items = _distinct_maps(12)
    job = MarketRunJob.start(_pricer(SlowTradeClient(_listings()), tmp_path), items)

    seen_mid_run = 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        seen_mid_run = job.state.priced_count
        if 0 < seen_mid_run < len(items):
            break
        time.sleep(0.01)

    assert 0 < seen_mid_run < len(items), (
        "no partial result was visible before the run finished"
    )
    job.cancel()
    job.join(timeout=10)


def test_cancelling_the_job_stops_it_early_and_keeps_what_it_had(tmp_path):
    items = _distinct_maps(20)
    job = MarketRunJob.start(_pricer(SlowTradeClient(_listings()), tmp_path), items)

    deadline = time.monotonic() + 10
    while job.state.priced_count < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    job.cancel()
    job.join(timeout=10)

    assert job.finished
    assert job.cancelled
    assert 0 < job.state.priced_count < len(items)
    assert "Cancelled" in job.state.stopped_reason


def test_progress_is_published_as_the_run_goes(tmp_path):
    items = _distinct_maps(6)
    job = MarketRunJob.start(_pricer(SlowTradeClient(_listings()), tmp_path), items)
    job.join(timeout=10)

    assert job.progress is not None
    assert job.progress.total == len(items)
    assert job.progress.done == len(items)


def test_a_run_that_raises_is_reported_rather_than_swallowed(tmp_path):
    class ExplodingClient(FakeTradeClient):
        def build_query(self, **kwargs):
            raise RuntimeError("the trade API fell over")

    job = MarketRunJob.start(
        _pricer(ExplodingClient(_listings()), tmp_path), _distinct_maps(2)
    )
    job.join(timeout=10)

    assert job.finished
    assert "the trade API fell over" in job.error


def test_the_priced_count_can_be_read_while_the_run_is_recording():
    """The page reads this on every redraw while the worker writes to it.

    Iterating the live dict rather than a snapshot raised `RuntimeError:
    dictionary changed size during iteration` -- rarely, and only on a big tab,
    which is the worst way for a bug to behave.
    """
    state = MarketRunState()
    done = threading.Event()

    def writer():
        for i in range(4000):
            state.record(MapOutcome(fingerprint=f"f{i}"))
        done.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    while not done.is_set():
        state.priced_count  # must not raise
        len(state.pending([]))
    thread.join(timeout=10)
