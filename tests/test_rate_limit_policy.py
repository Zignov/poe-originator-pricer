"""The configured interval is a floor until GGG states its own policy.

`http.min_request_interval_seconds` is described in config as an initial
conservative guess, and it was costing real time: a 1s pause between fetches
made a 100-listing read take 12 seconds when GGG's own fetch policy allows
twelve requests every four seconds. Once the server has told us the rules, the
rules are what we obey -- that is more self-limiting than a guess, not less.
"""
from __future__ import annotations

from src.market.rate_limit import RateLimiter

#: The live fetch policy, as returned in X-Rate-Limit-Ip.
FETCH_POLICY = {"X-Rate-Limit-Ip": "12:4:10,16:12:300", "X-Rate-Limit-Ip-State": "1:4:0,1:12:0"}


def _limiter(**kw) -> RateLimiter:
    return RateLimiter(name="test", **kw)


def test_the_guess_applies_before_any_policy_is_known():
    limiter = _limiter(min_interval=1.0)
    slept: list[float] = []
    limiter.acquire(sleep=slept.append)
    limiter.acquire(sleep=slept.append)
    assert sum(slept) > 0, "with no server policy, the configured floor must hold"


def test_the_server_policy_replaces_the_guess_once_known():
    limiter = _limiter(min_interval=1.0)
    limiter.update_from_headers(FETCH_POLICY)
    slept: list[float] = []
    for _ in range(10):
        limiter.acquire(sleep=slept.append)
    # 10 requests sit inside 12-per-4s, so nothing should be waiting a full second.
    assert sum(slept) < 1.0, f"waited {sum(slept):.2f}s for 10 requests inside the policy"


def test_the_server_policy_is_still_enforced_at_its_limit():
    limiter = _limiter(min_interval=0.0)
    limiter.update_from_headers({"X-Rate-Limit-Ip": "3:10:60", "X-Rate-Limit-Ip-State": "0:10:0"})
    slept: list[float] = []
    for _ in range(4):
        limiter.acquire(sleep=slept.append)
    assert sum(slept) > 0, "the fourth request must wait for the 3-per-10s rule"


def test_a_penalty_is_still_slept_out_whatever_the_floor():
    limiter = _limiter(min_interval=0.0)
    limiter.update_from_headers(
        {"X-Rate-Limit-Ip": "12:4:10", "X-Rate-Limit-Ip-State": "12:4:30"}
    )
    slept: list[float] = []
    limiter.acquire(sleep=slept.append)
    assert sum(slept) > 0, "an active penalty must be waited out"


def test_retry_after_still_blocks():
    limiter = _limiter(min_interval=0.0)
    limiter.update_from_headers(FETCH_POLICY)
    limiter.note_retry_after(5.0)
    slept: list[float] = []
    limiter.acquire(sleep=slept.append)
    assert sum(slept) >= 5.0
