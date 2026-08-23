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
    _hits: deque[float] = field(default_factory=deque)
    _blocked_until: float = 0.0
    _last_request: float = 0.0

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

    def note_retry_after(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    # -- gating ----------------------------------------------------------
    def _wait_time(self) -> float:
        now = time.monotonic()
        wait = max(0.0, self._blocked_until - now)
        if self.min_interval:
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
