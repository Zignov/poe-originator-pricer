"""Client-side obedience to GGG's published rate limits.

GGG returns the active policy in response headers, e.g.

    X-Rate-Limit-Ip:       5:10:60,15:60:300,30:300:1800,600:21600:3600
    X-Rate-Limit-Ip-State: 1:10:0,2:60:0,2:300:0,110:21600:0

Each rule is `max_hits:period_seconds:penalty_seconds`, and the matching state
entry is `current_hits:period_seconds:active_penalty_seconds`. Rather than
guessing an interval, this tracker reads whatever the server reports and waits
until the request would not exceed any rule. It never tries to work around a
limit -- if the server signals a penalty, it sleeps out the penalty.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

#: The policies GGG returned live on 2026-08-25, used as the *opening guess*
#: until the server states its own -- which it does on the first response, and
#: which then wins.
#:
#: Seeding these matters for one reason: before any request has been made a
#: limiter knows no rules, and everything that asks it "how long will this run
#: take" got `min_interval` back instead -- a two-second placeholder against a
#: real allowance of thirty searches per five minutes. That is how a page came
#: to promise six minutes for a job that takes an hour and a half. A stale
#: guess that is the right order of magnitude is worth far more than a fresh
#: one that is out by eighteen times.
OBSERVED_SEARCH_POLICY = "5:10:60,15:60:300,30:300:1800,600:21600:3600"
OBSERVED_FETCH_POLICY = "12:4:10,16:12:300,50:300:300,1000:21600:1800"


@dataclass
class Rule:
    max_hits: int
    period: float
    penalty: float


@dataclass
class RateLimiter:
    """One limiter per endpoint policy (search and fetch have different ones)."""

    name: str = "default"
    min_interval: float = 0.0
    rules: list[Rule] = field(default_factory=list)
    #: An opening guess at the policy, in the header's own format. Replaced by
    #: whatever the server reports on the first response.
    policy: str | None = None
    _hits: deque[float] = field(default_factory=deque)
    _blocked_until: float = 0.0
    _last_request: float = 0.0

    def __post_init__(self) -> None:
        if not self.rules and self.policy:
            self.rules = [Rule(m, p, pen) for m, p, pen in self._parse_triplets(self.policy)]

    # -- header handling -------------------------------------------------
    @staticmethod
    def _parse_triplets(value: str | None) -> list[tuple[int, float, float]]:
        out: list[tuple[int, float, float]] = []
        for chunk in (value or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) != 3:
                continue
            try:
                out.append((int(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        return out

    def update_from_headers(self, headers) -> None:
        limit_header = None
        state_header = None
        for key, value in (headers or {}).items():
            low = key.lower()
            if low.endswith("-state"):
                if low.startswith("x-rate-limit-"):
                    state_header = value
            elif low.startswith("x-rate-limit-") and low not in (
                "x-rate-limit-policy",
                "x-rate-limit-rules",
            ):
                limit_header = value

        rules = self._parse_triplets(limit_header)
        if rules:
            self.rules = [Rule(m, p, pen) for m, p, pen in rules]

        for hits, period, penalty in self._parse_triplets(state_header):
            if penalty > 0:
                self._blocked_until = max(self._blocked_until, time.monotonic() + penalty)
            # If the server says we are close to a rule, pre-emptively back off.
            for rule in self.rules:
                if rule.period == period and rule.max_hits and hits >= rule.max_hits - 1:
                    self._blocked_until = max(
                        self._blocked_until, time.monotonic() + rule.period / rule.max_hits
                    )

    def sustained_interval(self) -> float:
        """Seconds per request the *tightest* reported rule allows, long-run.

        For estimating how long a run will take, nothing else. The burst rules
        are not the binding constraint on a long run: GGG's live search policy
        on 2026-08-25 was `5:10:60,15:60:300,30:300:1800,600:21600:3600`, and
        while the first rule allows a request every two seconds, the third
        allows thirty every five minutes -- ten seconds each, sustained. Quoting
        the burst rate would tell a user "one minute" for a run that takes ten.

        Returns the configured guess while no policy has been reported yet.
        """
        periods = [rule.period / rule.max_hits for rule in self.rules if rule.max_hits > 0]
        if not periods:
            return self.min_interval
        return max(periods)

    def seconds_for(self, count: int) -> float:
        """How long `count` requests take from a standing start, under the rules.

        Not `count * sustained_interval`, and the difference is the whole point.
        Multiplying by the sustained rate charges every request at the pace of
        the *slowest* rule, which for GGG's search policy is the six-hour one at
        thirty-six seconds each -- a rule that does not bind at all until the
        six-hundredth request. Quoting it makes a ninety-request run look like
        an hour when it is twelve minutes.

        What actually happens is that each rule lets `max_hits` through free and
        then paces the rest at one period per batch, so the cost of a rule is
        `(count - max_hits) / max_hits * period` and the binding rule is
        whichever of those is largest. Below every rule's burst allowance the
        answer is zero, which is right: five searches really do go straight out.
        """
        n = max(0, int(count))
        if n <= 0:
            return 0.0
        worst = 0.0
        for rule in self.rules:
            if rule.max_hits <= 0:
                continue
            worst = max(worst, max(0.0, (n - rule.max_hits)) / rule.max_hits * rule.period)
        if not self.rules:
            return n * self.min_interval
        return worst

    def note_retry_after(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    # -- gating ----------------------------------------------------------
    def _wait_time(self) -> float:
        now = time.monotonic()
        wait = max(0.0, self._blocked_until - now)
        # The configured interval is a starting guess, used only until the server
        # states its own policy. Once GGG has reported the rules, those are what
        # is obeyed -- they are the authority, and the guess was costing real
        # time: a 1s floor turned a 100-listing read into 12 seconds against a
        # published allowance of twelve fetches every four.
        if self.min_interval and not self.rules:
            wait = max(wait, self._last_request + self.min_interval - now)
        for rule in self.rules:
            if rule.max_hits <= 0:
                continue
            recent = [t for t in self._hits if now - t < rule.period]
            if len(recent) >= rule.max_hits:
                oldest = recent[-rule.max_hits]
                wait = max(wait, oldest + rule.period - now)
        return wait

    def acquire(self, sleep=time.sleep) -> float:
        """Block until a request is allowed. Returns how long it waited."""
        waited = 0.0
        for _ in range(64):
            wait = self._wait_time()
            if wait <= 0:
                break
            sleep(wait)
            waited += wait
        now = time.monotonic()
        self._hits.append(now)
        self._last_request = now
        horizon = max((r.period for r in self.rules), default=60.0)
        while self._hits and now - self._hits[0] > horizon:
            self._hits.popleft()
        return waited
