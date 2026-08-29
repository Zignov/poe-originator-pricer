"""Stopping a collection run, and keeping everything it had already read.

A collection is the longest-running thing this app does: sixteen stratified
searches and forty-odd fetches, paced by a policy that allows thirty searches
every five minutes and can put an 1800-second penalty on the connection. Almost
all of that time is spent asleep rather than on the wire, which is why the
interesting question is not "does the flag get read" but "*where* does it get
read".

These tests pin the three places it has to land:

* inside a rate-limit wait, not after it -- otherwise a cancel during a penalty
  does nothing for half an hour and reads as a broken button;
* between slices and between fetch batches, the two boundaries where the walk
  is between requests rather than inside one;
* before the request the limiter has just released, because a cancel can arrive
  in the gap between "allowed to send" and "sent".

And the one thing that makes cancelling worth doing at all: the listings the
run already paid for are still in the database afterwards, flagged for outliers
like any other collection.

Nothing here touches the network. The collector is driven through a fake
session; the ingest tests swap the collector class out entirely.
"""
from __future__ import annotations

from src.config import Config, _DEFAULTS
from src.database.repository import Repository
from src.market import ingest
from src.market.cancel import CancelToken
from src.market.collectors.official_trade_api import (
    CollectionCancelled,
    OfficialTradeApiCollector,
)
from src.market.rate_limit import RateLimiter
from src.pricing.currency import CurrencyConverter

from tests.test_market_comparison import api_result


# ---------------------------------------------------------------------------
# A fake trade API
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload, status_code: int = 200, headers: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    """A `requests.Session` that answers from memory.

    It counts what was asked of it, which is how the tests assert the thing
    that actually matters about a cancel: not that a flag was set, but that no
    further request went out after it.
    """

    def __init__(self, ids_per_search: int = 10, on_search=None, on_fetch=None):
        self.headers: dict[str, str] = {}
        self.searches: list[dict] = []
        self.fetches: list[list[str]] = []
        self.ids_per_search = ids_per_search
        self._on_search = on_search
        self._on_fetch = on_fetch

    def get(self, url, params=None, timeout=None):
        if url.endswith("/data/leagues"):
            return FakeResponse({"result": [{"id": "Allflame"}]})
        ids = url.rsplit("/", 1)[-1].split(",")
        self.fetches.append(ids)
        if self._on_fetch:
            self._on_fetch(self)
        # A distinct seller per listing, because the collector's own bulk-dump
        # backstop keeps only eight per account and would otherwise silence the
        # counts these tests are reading.
        return FakeResponse(
            {
                "result": [
                    api_result(i, 40 + n, seller=f"Exile#{i}")
                    for n, i in enumerate(ids)
                ]
            }
        )

    def post(self, url, json=None, timeout=None):
        self.searches.append(json)
        if self._on_search:
            self._on_search(self)
        n = len(self.searches)
        return FakeResponse(
            {
                "id": f"query{n}",
                "result": [f"s{n}-{i}" for i in range(self.ids_per_search)],
                "total": 500,
            }
        )


def _collector(session: FakeSession, cancel: CancelToken) -> OfficialTradeApiCollector:
    return OfficialTradeApiCollector(
        league="Allflame",
        user_agent="test",
        converter=CurrencyConverter(210.0),
        min_interval=0.0,
        session=session,
        cancel=cancel,
    )


# ---------------------------------------------------------------------------
# The wait is where the time goes, so the wait is where a cancel has to land
# ---------------------------------------------------------------------------
def test_a_cancel_lands_inside_a_rate_limit_wait_rather_than_after_it():
    """The load-bearing half of the whole feature.

    GGG's search policy carries an 1800-second penalty, and a cancel that has to
    sit that out is not a cancel. Note what a cancellable sleep alone would buy:
    nothing. `CancelToken.sleep` returns instantly once cancelled, so without the
    stop check `acquire` would simply spin through all sixty-four of its
    iterations and release the request anyway. The stop check is the part that
    returns.
    """
    limiter = RateLimiter(name="test", min_interval=0.0)
    limiter.update_from_headers(
        {"X-Rate-Limit-Ip": "5:10:60", "X-Rate-Limit-Ip-State": "5:10:1800"}
    )
    token = CancelToken()

    slept: list[float] = []

    def sleep(seconds):
        # Stands in for the user pressing Cancel while the limiter is waiting.
        slept.append(seconds)
        token.cancel()

    limiter.acquire(sleep=sleep, should_stop=token.is_set)

    assert len(slept) == 1, (
        f"acquire kept waiting after the cancel: {len(slept)} waits, not 1"
    )


def test_an_uncancelled_acquire_still_waits_out_the_whole_penalty():
    """The stop check must not turn into an escape hatch from the rate limit."""
    limiter = RateLimiter(name="test", min_interval=0.0)
    limiter.note_retry_after(30.0)
    slept: list[float] = []
    limiter.acquire(sleep=slept.append, should_stop=lambda: False)
    assert sum(slept) >= 30.0


# ---------------------------------------------------------------------------
# Where the walk stops
# ---------------------------------------------------------------------------
def test_a_cancel_stops_the_walk_between_slices_rather_than_at_the_end():
    """Sixteen slices is the whole run, so stopping "at the end" is no stop.

    The cancel is pressed during the second slice's search. The first slice is
    already read and stays read; the fourteen behind it are never asked for.
    """
    token = CancelToken()
    session = FakeSession(
        on_search=lambda s: token.cancel() if len(s.searches) == 2 else None
    )
    collector = _collector(session, token)

    listings = list(collector.collect(limit=500))

    assert len(session.searches) == 2, (
        f"the walk carried on into {len(session.searches)} slices after the cancel"
    )
    assert len(listings) == 10, "the slice that was already paid for was thrown away"


def test_a_cancel_lands_between_two_fetch_batches():
    """Not only between slices. One slice is up to ten fetches on its own.

    A hundred ids at ten per fetch is ten rate-limited requests inside a single
    slice, so a cancel that only ever landed at a slice boundary would still
    take minutes to be noticed.
    """
    token = CancelToken()
    session = FakeSession(
        ids_per_search=30,
        on_fetch=lambda s: token.cancel() if len(s.fetches) == 1 else None,
    )
    collector = _collector(session, token)

    listings = list(collector.collect(limit=500, per_slice=30))

    assert len(session.fetches) == 1, (
        f"{len(session.fetches)} fetch batches went out after the cancel"
    )
    assert len(session.searches) == 1
    assert len(listings) == 10, "the batch already fetched was not kept"


def test_no_request_is_issued_after_the_cancel_even_if_the_limiter_already_let_one_through():
    """The gap between "allowed to send" and "sent" is a real place to be.

    The limiter releases a request, and the cancel arrives in the microseconds
    before the socket is opened -- or, far more likely, arrives *during* the
    wait the limiter has just finished. Either way the request is no longer
    wanted, and checking the flag only before `acquire` would send it anyway.
    """
    token = CancelToken()
    session = FakeSession()
    collector = _collector(session, token)

    # Exactly the situation described: acquire returns, having let the request
    # through, and by then the token is set.
    def release_then_cancel(sleep=None, should_stop=None):
        token.cancel()
        return 0.0

    collector.search_limiter.acquire = release_then_cancel

    try:
        collector.search(collector.build_query())
    except CollectionCancelled:
        pass
    else:  # pragma: no cover - only reached when the check is missing
        raise AssertionError("a cancelled search did not refuse to send")

    assert session.searches == [], "a request was issued after the cancel"


def test_a_run_with_no_cancel_token_walks_every_slice_as_before():
    """The token is optional and the uncancellable callers must not change."""
    session = FakeSession(ids_per_search=1)
    collector = OfficialTradeApiCollector(
        league="Allflame",
        user_agent="test",
        converter=CurrencyConverter(210.0),
        min_interval=0.0,
        session=session,
    )
    listings = list(collector.collect(limit=3, per_slice=1))
    assert len(listings) == 3
    assert len(session.searches) == 3


# ---------------------------------------------------------------------------
# What survives a cancel
# ---------------------------------------------------------------------------
def _cfg() -> Config:
    return Config({**_DEFAULTS, "league": "Allflame"})


def _ingest_against(monkeypatch, collector) -> None:
    monkeypatch.setattr(
        ingest, "OfficialTradeApiCollector", lambda **kwargs: collector
    )


def test_everything_collected_before_the_cancel_is_still_written_to_the_database(
    tmp_path, monkeypatch
):
    """The reason a cancel is a reasonable thing to press.

    ingest flushes every fifty listings, so most of a long run is already
    committed -- but the trailing partial batch is not, and it is the batch a
    cancel is most likely to interrupt. If the early exit skipped the final
    flush, cancelling would silently throw away up to forty-nine listings that
    had already cost rate-limited requests.
    """
    token = CancelToken()
    session = FakeSession(
        ids_per_search=10,
        on_fetch=lambda s: token.cancel() if len(s.fetches) == 1 else None,
    )
    collector = _collector(session, token)
    _ingest_against(monkeypatch, collector)

    repo = Repository(tmp_path / "cancel.db")
    stats = ingest.collect_from_trade_api(_cfg(), repo, limit=500, cancel=token)

    assert stats["cancelled"] is True
    # Ten listings, well under the fifty-row flush threshold: they exist only
    # because the trailing flush still ran after the early exit.
    assert stats["inserted"] == 10
    assert repo.count_listings("Allflame")["total"] == 10
    repo.close()


def test_a_cancelled_run_still_reruns_outlier_detection_on_what_it_kept(
    tmp_path, monkeypatch
):
    """Outlier flags are computed against the whole league's spread.

    Rows that never went through the pass are unflagged, and unflagged means
    "clean" everywhere downstream -- so a cancelled run that skipped it would
    quietly feed the trainer whatever junk it happened to stop on. The pass is a
    local query and costs no requests, so being cut short is no reason to skip
    it.
    """
    token = CancelToken()
    session = FakeSession(
        ids_per_search=10,
        on_fetch=lambda s: token.cancel() if len(s.fetches) == 1 else None,
    )
    collector = _collector(session, token)
    _ingest_against(monkeypatch, collector)

    ran: list[bool] = []
    real_refresh = ingest.refresh_outliers
    monkeypatch.setattr(
        ingest,
        "refresh_outliers",
        lambda cfg, repo: (ran.append(True), real_refresh(cfg, repo))[1],
    )

    repo = Repository(tmp_path / "cancel-outliers.db")
    stats = ingest.collect_from_trade_api(_cfg(), repo, limit=500, cancel=token)

    assert ran, "a cancelled run left its new listings un-flagged"
    assert stats["evaluated"] == 10
    repo.close()


def test_an_uncancelled_run_does_not_report_itself_as_cancelled(tmp_path, monkeypatch):
    session = FakeSession(ids_per_search=1)
    collector = OfficialTradeApiCollector(
        league="Allflame",
        user_agent="test",
        converter=CurrencyConverter(210.0),
        min_interval=0.0,
        session=session,
    )
    _ingest_against(monkeypatch, collector)

    repo = Repository(tmp_path / "clean.db")
    stats = ingest.collect_from_trade_api(_cfg(), repo, limit=2)

    assert stats["cancelled"] is False
    repo.close()
