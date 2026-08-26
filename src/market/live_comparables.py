"""Pricing a map from the maps *currently listed that look like it*.

This is the second pricing mode. Where the model answers "what does a map with
these seven numbers usually go for", this asks the trade site directly: show me
the maps on sale right now whose rewards sit inside a window around mine, and
derive a price from those. It is slower and it costs requests, and in exchange
the number is auditable -- every priced map carries the exact listings it came
from, with their distance and their weight.

Everything in this module is either a pure function or a small dataclass. The
only thing that touches the network is `LiveComparableFinder`, which talks to an
injected client (`TradeSearchClient`); `OfficialTradeApiCollector` satisfies that
protocol, and the tests pass a fake.

What was verified live, against 3.29 league "Allflame" on 2026-08-25, in four
searches and one fetch:

  * **A windowed query works and is honoured.** One search carrying `map_iiq`
    74-94, `map_iir` 124-144, `map_packsize` 22-42 and the pseudo stat
    `pseudo_map_more_currency_drops` 37-57 returned listings whose printed
    properties all sat inside those windows (+77%, +128%, +28%, +47%).

  * **A tight window really does come back nearly empty.** That same query
    matched a `total` of **1**. This is the whole reason the tolerance widens
    adaptively rather than being a constant: on a market of a few hundred maps,
    a window narrow enough to be a true comparable usually matches nobody.

  * **Absence of a reward is expressible, and it matters.** A `{"type": "not"}`
    stat group on `pseudo_map_more_scarab_drops` with `min: 1` returned 492
    listings against 152 for the matching `and` group -- so the two halves
    partition the market, and a map with no scarab bonus can be compared only
    against other maps with no scarab bonus rather than being quietly pooled
    with maps carrying +50%.

  * **The search rate-limit policy is the cost of this mode.** GGG reported
    `X-Rate-Limit-Ip: 5:10:60,15:60:300,30:300:1800,600:21600:3600`. Thirty
    searches per five minutes is one every ten seconds sustained, which is why
    there is a request budget, a bucket cache and a resumable run rather than a
    loop that just goes.

Measured end to end on the same day: pricing three maps out of a 20-item stash
export cost 14 requests on the first run and 4 on a second run over the same tab,
the difference being bucket-cache hits. Call it 4-7 requests per distinct map
when the tolerance has to widen to its last step and 2-4 when it does not.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence

import numpy as np

from src.market.collectors.official_trade_api import (
    FETCH_BATCH,
    REWARD_STATS,
    TradeApiError,
    default_slices,
)
from src.market.comparables import scaled_distance
from src.market.outliers import DEFAULT_PLAUSIBLE_CHAOS, PEER_FACTOR
from src.parsing.item_parser import classify, parse_api_item
from src.parsing.price_parser import parse_api_price
from src.pricing.currency import CurrencyConverter
from src.schema import FEATURE_NAMES, NormalizedMap

# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

#: Half-width of the search window on each feature at tolerance multiplier 1.0,
#: in the same points the game prints.
#:
#: These are not one number applied to seven features, because the features do
#: not share a scale: rarity runs to about 160 and more_scarabs to about 50, so
#: "+/-20" is a tight window on one and a meaningless one on the other. The
#: figures below are roughly half the observed spread of each feature in the
#: collected 3.29 sample, which makes one multiplier mean the same *kind* of
#: closeness on every feature.
TOLERANCE_BASE: dict[str, int] = {
    "quantity": 10,
    "rarity": 20,
    "pack_size": 5,
    "more_currency": 12,
    "more_maps": 10,
    "more_scarabs": 10,
    "more_divination_cards": 5,
}

#: Multipliers tried in order until enough comparables come back. Starting tight
#: and widening is the only workable order: a live probe of a 1.0-multiplier
#: window matched exactly one listing, so a fixed window would either be too
#: tight for most maps or too loose for all of them. Which step succeeded is
#: recorded and feeds confidence -- an answer found at 4.0 is a different kind of
#: answer from one found at 1.0, and the user is told which they got.
DEFAULT_TOLERANCE_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 4.0)

#: Enough comparables to stop widening.
DEFAULT_MIN_COMPARABLES = 6

#: Most listings read for one map. A search returns at most 100 ids and each
#: fetch reads 10, so this is really a cap on fetch requests: 30 costs 3.
DEFAULT_MAX_COMPARABLES = 30

#: Comparables older than this are not evidence about the price *now*.
DEFAULT_MAX_LISTING_AGE_HOURS = 48.0

#: Enough close comparables for the count itself to stop being the weak link.
GOOD_COMPARABLES = 10


def reward_window(item: NormalizedMap, multiplier: float) -> dict[str, tuple[int, int] | None]:
    """The min/max window on each feature at this tolerance.

    `None` means "this map does not have this reward at all", which is a
    different statement from a window around zero and is filtered differently
    (see `comparable_query`).
    """
    window: dict[str, tuple[int, int] | None] = {}
    for name in FEATURE_NAMES:
        value = int(getattr(item, name, 0) or 0)
        if value <= 0:
            window[name] = None
            continue
        half = max(1, int(round(TOLERANCE_BASE[name] * multiplier)))
        window[name] = (max(0, value - half), value + half)
    return window


def describe_window(window: dict[str, tuple[int, int] | None]) -> str:
    """One line a user can read, e.g. 'qty 74-94, rarity 124-144, no scarabs'."""
    labels = {
        "quantity": "qty",
        "rarity": "rarity",
        "pack_size": "pack",
        "more_currency": "currency",
        "more_maps": "maps",
        "more_scarabs": "scarabs",
        "more_divination_cards": "div cards",
    }
    parts = []
    for name in FEATURE_NAMES:
        bounds = window[name]
        if bounds is None:
            if name in REWARD_STATS:
                parts.append(f"no {labels[name]}")
            continue
        parts.append(f"{labels[name]} {bounds[0]}-{bounds[1]}")
    return ", ".join(parts)


def comparable_query(
    build_query: Callable[..., dict[str, Any]],
    item: NormalizedMap,
    multiplier: float,
    status: str = "online",
) -> dict[str, Any]:
    """The trade search that finds maps like `item`, at this tolerance.

    Three decisions are baked in:

    * **Sorted newest-first.** A search returns at most 100 ids however many
      match, so the sort decides which 100 arrive -- and sorting by price would
      hand back only one end of exactly the distribution being measured. This is
      the same trap the training collector documents, and it is worse here,
      because here the sample *is* the answer rather than one row of it.

    * **Collapsed by account.** One seller dumping a tab at a flat price would
      otherwise supply most of the comparables for a whole bucket and set its
      price single-handed.

    * **Online only.** The model is fitted to the whole listing book because
      that is the largest honest sample; this mode is answering "what is a map
      like this selling for right now", and an offline seller's ask is not an
      offer you can take. `status` is a parameter so that assumption can be
      turned off without editing code.
    """
    window = reward_window(item, multiplier)
    extra_stats: list[dict[str, Any]] = []
    for feature, stat_id in REWARD_STATS.items():
        bounds = window[feature]
        if bounds is None:
            # Verified live: the `not` group and the matching `and` group split
            # the market cleanly (492 / 152 on more_scarabs), so a map with no
            # scarab bonus is compared only against maps that also have none.
            extra_stats.append(
                {"type": "not", "filters": [{"id": stat_id, "value": {"min": 1}}]}
            )
        else:
            extra_stats.append(
                {
                    "type": "and",
                    "filters": [{"id": stat_id, "value": {"min": bounds[0], "max": bounds[1]}}],
                }
            )
    return build_query(
        sort_by="indexed",
        sort="desc",
        status=status,
        collapse=True,
        quantity=window["quantity"],
        map_rarity=window["rarity"],
        pack_size=window["pack_size"],
        extra_stats=extra_stats,
    )


# ---------------------------------------------------------------------------
# Bucket cache
# ---------------------------------------------------------------------------

def bucket_key(
    item: NormalizedMap,
    *,
    league: str = "",
    map_tier: int | None = None,
    require_uncorrupted: bool | None = None,
) -> str:
    """A key shared by maps close enough that one search answers for all of them.

    A stash tab of Originator Maps is not a set of unrelated items: several maps
    routinely land within a few points of each other on every feature, and
    searching separately for each would pay a full request for an identical
    answer. Each feature is quantised to its own tolerance step -- so two maps in
    one bucket differ by less than the tightest search window, which is exactly
    the condition under which one query's results are comparables for both.

    The scope goes in the key because the same rewards mean a different market
    in a different league or at a different tier.
    """
    quantised = [
        int(round((int(getattr(item, name, 0) or 0)) / TOLERANCE_BASE[name]))
        for name in FEATURE_NAMES
    ]
    scope = f"{league}|t{map_tier}|{'unc' if require_uncorrupted else 'any'}"
    return scope + "|" + ",".join(str(v) for v in quantised)


@dataclass(frozen=True)
class LiveComparable:
    """One listing that is currently on the market and looks like the map."""

    features: dict[str, int]
    price_chaos: float
    price_amount: float | None = None
    price_currency: str | None = None
    price_type: str | None = None
    seller: str | None = None
    listing_id: str | None = None
    name: str = ""
    indexed_at: datetime | None = None
    #: Filled in by `derive_market_estimate`, not by the fetch.
    distance: float = 0.0
    weight: float = 0.0

    def age_hours(self, now: datetime | None = None) -> float | None:
        if self.indexed_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.indexed_at).total_seconds() / 3600.0

    def vector(self) -> list[int]:
        return [int(self.features.get(name, 0) or 0) for name in FEATURE_NAMES]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["indexed_at"] = self.indexed_at.isoformat() if self.indexed_at else None
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LiveComparable":
        indexed = payload.get("indexed_at")
        parsed = None
        if indexed:
            try:
                parsed = datetime.fromisoformat(indexed)
            except ValueError:
                parsed = None
            if parsed and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return cls(**{**payload, "indexed_at": parsed})


@dataclass
class CacheEntry:
    stored_at: datetime
    tolerance: float
    comparables: list[LiveComparable]
    total_matching: int = 0
    requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stored_at": self.stored_at.isoformat(),
            "tolerance": self.tolerance,
            "total_matching": self.total_matching,
            "requests": self.requests,
            "comparables": [c.to_dict() for c in self.comparables],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CacheEntry":
        stored = datetime.fromisoformat(payload["stored_at"])
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        return cls(
            stored_at=stored,
            tolerance=float(payload.get("tolerance", 0.0)),
            comparables=[LiveComparable.from_dict(c) for c in payload.get("comparables", [])],
            total_matching=int(payload.get("total_matching", 0)),
            requests=int(payload.get("requests", 0)),
        )


class BucketCache:
    """Search results keyed by bucket, with a TTL, optionally file-backed.

    The TTL is not a performance knob. Live prices go stale, and an hour-old
    answer presented as the current market is worse than saying nothing -- so
    the default is short and it is configurable.

    The file backing is what makes a run resumable across processes: a second
    run over the same tab re-reads the buckets it already paid for and spends
    its budget only on what is left.
    """

    def __init__(
        self,
        ttl_minutes: float = 30.0,
        path: str | Path | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.ttl = timedelta(minutes=float(ttl_minutes))
        self.path = Path(path) if path else None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._entries: dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        if self.path and self.path.exists():
            self.load()

    def get(self, key: str) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if self._now() - entry.stored_at > self.ttl:
            # Dropped rather than kept and ignored: a stale entry that stays in
            # the file would be re-read on every later run and re-expired.
            del self._entries[key]
            self.misses += 1
            return None
        self.hits += 1
        return entry

    def put(self, key: str, entry: CacheEntry) -> None:
        self._entries[key] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A truncated cache is a cache, not a crash. Worst case the run pays
            # for its searches again.
            return
        for key, entry in (payload or {}).items():
            try:
                self._entries[key] = CacheEntry.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                continue

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        live = {
            key: entry.to_dict()
            for key, entry in self._entries.items()
            if self._now() - entry.stored_at <= self.ttl
        }
        self.path.write_text(json.dumps(live), encoding="utf-8")


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@dataclass
class RequestBudget:
    """A hard ceiling on requests for one run.

    Not a suggestion and not a rate: the rate limiter already decides how *fast*
    requests go out, and this decides how many there are at all. They solve
    different problems -- a run that obeys every rate limit perfectly can still
    take an hour, and the user has to be able to say "spend a hundred requests
    and stop".
    """

    limit: int = 200
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def can_spend(self, count: int = 1) -> bool:
        return self.used + count <= self.limit

    def spend(self, count: int = 1) -> None:
        self.used += count


def estimate_seconds(requests_remaining: int, seconds_per_request: float) -> float:
    """How long the rest of a run will take, at the sustained allowed rate.

    Deliberately the *sustained* rate rather than the burst rate: GGG's search
    policy allows five requests in ten seconds but only thirty in five minutes,
    and quoting the first would promise a minute for a ten-minute run.
    """
    return max(0.0, float(requests_remaining) * max(0.0, float(seconds_per_request)))


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
    return f"{seconds / 3600:.1f}h"


# ---------------------------------------------------------------------------
# Price derivation
# ---------------------------------------------------------------------------

@dataclass
class MarketDerivation:
    """A price, an interval, a confidence -- and the evidence for all three."""

    fair_value_chaos: float
    interval_low_chaos: float
    interval_high_chaos: float
    confidence: str
    confidence_score: float
    confidence_reasons: list[str] = field(default_factory=list)
    comparables: list[LiveComparable] = field(default_factory=list)
    discarded: list[tuple[LiveComparable, str]] = field(default_factory=list)
    tolerance: float = 0.0
    window: dict[str, tuple[int, int] | None] = field(default_factory=dict)
    total_matching: int = 0
    requests_used: int = 0
    from_cache: bool = False


class NoComparables(RuntimeError):
    """Raised when nothing usable came back, so no price can be derived."""


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    """Quantile of a weighted sample, as a step function over sorted values.

    A step function rather than an interpolation on purpose: the output is
    always a price some real listing actually carries, which keeps the number
    explainable -- you can point at the row it came from.
    """
    arr = np.asarray(list(values), dtype=float)
    w = np.asarray(list(weights), dtype=float)
    if arr.size == 0:
        raise NoComparables("no values to take a quantile of")
    order = np.argsort(arr)
    arr, w = arr[order], np.clip(w[order], 0.0, None)
    total = w.sum()
    if total <= 0:
        return float(np.quantile(arr, q))
    cumulative = np.cumsum(w)
    index = int(np.searchsorted(cumulative, q * total, side="left"))
    return float(arr[min(index, arr.size - 1)])


def distance_weights(distances: np.ndarray) -> np.ndarray:
    """1 / (1 + d^2), so a map twice as far away counts far less than half.

    Inverse-square rather than inverse-linear because the alternative gives a
    distant map a stubbornly large share: on a typical bucket, linear weighting
    left the furthest comparable carrying about a third of the weight of the
    nearest, which is not what "comparable" is supposed to mean.
    """
    return 1.0 / (1.0 + np.asarray(distances, dtype=float) ** 2)


def derive_market_estimate(
    item: NormalizedMap,
    comparables: Sequence[LiveComparable],
    *,
    tolerance: float,
    min_comparables: int = DEFAULT_MIN_COMPARABLES,
    max_age_hours: float = DEFAULT_MAX_LISTING_AGE_HOURS,
    plausible_chaos: tuple[float, float] = DEFAULT_PLAUSIBLE_CHAOS,
    total_matching: int = 0,
    now: datetime | None = None,
) -> MarketDerivation:
    """Turn a set of live listings into a price, an interval and a confidence.

    Outliers are dropped in two passes, both mirroring `src/market/outliers.py`
    rather than inventing a second opinion:

    1. the absolute plausible-price bounds, which catch parked inventory;
    2. a peer test against this set's own 10th/90th percentile, at the same
       `PEER_FACTOR` the stored-listing pass uses.

    The second is meaningful *here* where it often is not in the database,
    because every listing in this set is already a comparable by construction --
    that is what the query window bought.
    """
    now = now or datetime.now(timezone.utc)
    kept: list[LiveComparable] = []
    discarded: list[tuple[LiveComparable, str]] = []

    low_bound, high_bound = plausible_chaos
    for comp in comparables:
        price = comp.price_chaos
        if price is None or not math.isfinite(price) or price <= 0:
            discarded.append((comp, "no usable price"))
            continue
        if price < low_bound:
            discarded.append((comp, f"below the plausible range ({low_bound:g}c)"))
            continue
        if price > high_bound:
            discarded.append((comp, f"above the plausible range ({high_bound:g}c)"))
            continue
        age = comp.age_hours(now)
        if age is not None and age > max_age_hours:
            discarded.append((comp, f"listed {age / 24:.1f} days ago"))
            continue
        kept.append(comp)

    if not kept:
        raise NoComparables("no usable listings came back for this map")

    prices = np.array([c.price_chaos for c in kept], dtype=float)
    if prices.size >= 4:
        survivors: list[LiveComparable] = []
        for index, (comp, price) in enumerate(zip(kept, prices)):
            # Leave-one-out, exactly as the stored-listing pass excludes a row
            # from its own peer group. Without that, a single 900c ask in a set
            # of six drags the 90th percentile up to nearly its own value and
            # then passes the test it should fail.
            others = np.delete(prices, index)
            p10 = float(np.quantile(others, 0.10))
            p90 = float(np.quantile(others, 0.90))
            if p90 > 0 and price > PEER_FACTOR * p90:
                discarded.append((comp, f"{price / p90:.0f}x its peers ({p90:.0f}c)"))
            elif p10 > 0 and price < p10 / PEER_FACTOR:
                discarded.append((comp, f"{p10 / price:.0f}x below its peers ({p10:.0f}c)"))
            else:
                survivors.append(comp)
        if survivors:
            kept = survivors

    matrix = np.array([c.vector() for c in kept], dtype=float)
    vector = np.array(item.feature_vector(), dtype=float)
    distances = scaled_distance(matrix, vector)
    weights = distance_weights(distances)
    kept = [
        LiveComparable(
            **{
                **c.to_dict(),
                "indexed_at": c.indexed_at,
                "distance": float(distances[i]),
                "weight": float(weights[i]),
            }
        )
        for i, c in enumerate(kept)
    ]
    kept.sort(key=lambda c: c.distance)

    prices = np.array([c.price_chaos for c in kept], dtype=float)
    weights = np.array([c.weight for c in kept], dtype=float)

    fair = weighted_quantile(prices, weights, 0.5)
    # The interval is the interquartile band of the comparables themselves. It
    # is a statement about the market's own spread, which is the honest thing a
    # set of real asks can say -- not a multiplier picked to look reassuring.
    low = weighted_quantile(prices, weights, 0.25)
    high = weighted_quantile(prices, weights, 0.75)
    if high <= low:
        low, high = min(low, fair), max(high, fair)

    level, score, reasons = score_market_confidence(
        kept,
        fair=fair,
        low=low,
        high=high,
        tolerance=tolerance,
        min_comparables=min_comparables,
        max_age_hours=max_age_hours,
        now=now,
    )
    return MarketDerivation(
        fair_value_chaos=float(fair),
        interval_low_chaos=float(low),
        interval_high_chaos=float(high),
        confidence=level,
        confidence_score=score,
        confidence_reasons=reasons,
        comparables=kept,
        discarded=discarded,
        tolerance=tolerance,
        window=reward_window(item, tolerance),
        total_matching=total_matching,
    )


def score_market_confidence(
    comparables: Sequence[LiveComparable],
    *,
    fair: float,
    low: float,
    high: float,
    tolerance: float,
    min_comparables: int = DEFAULT_MIN_COMPARABLES,
    max_age_hours: float = DEFAULT_MAX_LISTING_AGE_HOURS,
    now: datetime | None = None,
) -> tuple[str, float, list[str]]:
    """Four signals, averaged, with the reason for each spelled out.

    Deliberately the same shape and the same thresholds as `src/ml/confidence.py`
    so that "Medium" means something comparable in both modes -- the two numbers
    are shown side by side, and a confidence label that meant different things in
    each column would be actively misleading.

    The four are the four ways this mode can be wrong:

      * **too few comparables** -- a price from three asks is a rumour;
      * **too wide a tolerance** -- the window had to be opened until it caught
        maps that are not really like this one;
      * **stale listings** -- an ask from two days ago is not today's market;
      * **high dispersion** -- the comparables themselves do not agree, so no
        single number from them is worth much.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    parts: list[float] = []

    count = len(comparables)
    support = min(count / GOOD_COMPARABLES, 1.0)
    parts.append(support)
    if count < min_comparables:
        reasons.append(f"only {count} live comparables (wanted {min_comparables})")
    elif count < GOOD_COMPARABLES:
        reasons.append(f"{count} live comparables")
    else:
        reasons.append(f"{count} live comparables")

    # 1.0 at the tightest step, halving each time the window doubles.
    tightness = float(np.clip(1.0 / max(tolerance, 1.0), 0.0, 1.0))
    parts.append(tightness)
    if tolerance > 1.0:
        reasons.append(
            f"search had to be widened to {tolerance:g}x tolerance to find enough maps"
        )

    ages = [a for a in (c.age_hours(now) for c in comparables) if a is not None]
    if ages:
        median_age = float(np.median(ages))
        freshness = float(np.clip(1.0 - median_age / max(max_age_hours, 1e-6), 0.0, 1.0))
        parts.append(freshness)
        if median_age > max_age_hours / 2:
            reasons.append(f"comparables are ~{median_age:.0f}h old")
    else:
        parts.append(0.5)
        reasons.append("listing times unavailable, so freshness is unknown")

    if fair > 0:
        dispersion = (high - low) / fair
        agreement = float(np.clip(1.5 - dispersion, 0.0, 1.0))
        parts.append(agreement)
        if dispersion > 0.6:
            reasons.append(
                f"comparable prices disagree widely (+/-{dispersion * 50:.0f}% around the middle)"
            )
    else:
        parts.append(0.0)
        reasons.append("comparables imply an effectively zero price")

    score = float(np.mean(parts))
    if score >= 0.7:
        level = "High"
    elif score >= 0.45:
        level = "Medium"
    else:
        level = "Low"
    return level, score, reasons


# ---------------------------------------------------------------------------
# The network seam
# ---------------------------------------------------------------------------

class TradeSearchClient(Protocol):
    """The only three things this mode needs from the trade API.

    `OfficialTradeApiCollector` satisfies it as it stands, which is the point:
    the rate limiting, the User-Agent, the 429 back-off and the header-driven
    policy all already live there and are not reimplemented here. A test passes
    a fake with the same three members and never touches the network.
    """

    converter: CurrencyConverter
    map_tier: int
    require_uncorrupted: bool

    def build_query(self, **kwargs: Any) -> dict[str, Any]: ...

    def search(self, query: dict[str, Any]) -> tuple[str, list[str], int]: ...

    def fetch(self, ids: list[str], query_id: str) -> list[dict[str, Any]]: ...


def to_live_comparable(
    result: dict[str, Any],
    converter: CurrencyConverter,
    map_tier: int | None,
    require_uncorrupted: bool,
) -> LiveComparable | None:
    """One /fetch result as a comparable, or None if it is not one.

    Mirrors the collector's `_to_listing` rather than reusing it because the
    output is different: nothing here is stored, so there is no dedupe key, no
    capture time and no raw payload to keep -- only what has to be shown next to
    the price.
    """
    item = result.get("item") or {}
    info = result.get("listing") or {}
    nm = classify(
        parse_api_item(item), map_tier=map_tier, require_uncorrupted=require_uncorrupted
    )
    if not nm.is_originator:
        return None

    price = parse_api_price(info)
    chaos = converter.to_chaos(price["price_amount"], price["price_currency"])
    if chaos is None or chaos <= 0:
        return None

    indexed_at = None
    raw_indexed = info.get("indexed")
    if raw_indexed:
        try:
            indexed_at = datetime.fromisoformat(str(raw_indexed).replace("Z", "+00:00"))
        except ValueError:
            indexed_at = None

    return LiveComparable(
        features=nm.features(),
        price_chaos=float(chaos),
        price_amount=price["price_amount"],
        price_currency=price["price_currency"],
        price_type=price["price_type"],
        seller=(info.get("account") or {}).get("name"),
        listing_id=result.get("id"),
        name=nm.name,
        indexed_at=indexed_at,
    )


# ---------------------------------------------------------------------------
# The shared pool
# ---------------------------------------------------------------------------

#: How many listing ids to read from each pool slice. Every ten cost one fetch
#: request, and the fetch policy allows fifty per five minutes, so this is the
#: knob that decides most of a pooled run's wall-clock. Fifty ids across the
#: sixteen slices is around eight hundred live listings for eighty fetches.
DEFAULT_POOL_PER_SLICE = 50


@dataclass(frozen=True)
class LivePool:
    """Every listing read for one run, to be shared by every map in the tab.

    The mode's original shape was one search per map, and on a full tab that is
    where all the time went: seventy maps quantise to about sixty-five distinct
    buckets, so the bucket cache saves almost nothing, and each bucket pays a
    search and up to three fetches.

    What makes a pool possible is that the search window was never what decided
    the price. `derive_market_estimate` picks a map's comparables out of
    whatever it is handed, by scaled distance, and weights them by it. The
    per-map query was only ever a way to *fetch* a relevant set. So the same
    listings can be fetched once for the whole tab and filtered per map in
    memory, and every map's price is derived from exactly the same evidence it
    would have had -- provided the pool reaches far enough to contain it, which
    is what `select_from_pool` returning nothing is the signal for.
    """

    comparables: tuple[LiveComparable, ...] = ()
    requests: int = 0
    slices_used: int = 0
    fetched_at: datetime | None = None
    error: str = ""

    def __len__(self) -> int:
        return len(self.comparables)


def matches_window(
    comparable: LiveComparable, window: dict[str, tuple[int, int] | None]
) -> bool:
    """Does this listing satisfy the window a search would have been given?

    The local half of `comparable_query`, and deliberately the same rule rather
    than a looser "near enough": a `None` window means the map has no such
    reward at all, which the query expresses as a `not` group, so a listing that
    *does* carry that reward is not a comparable and must not be pooled in with
    ones that do. Getting this wrong is invisible -- the price still comes out,
    just derived from maps that are not like yours.
    """
    for name in FEATURE_NAMES:
        bounds = window[name]
        value = int(comparable.features.get(name, 0) or 0)
        if bounds is None:
            if value > 0:
                return False
            continue
        if not (bounds[0] <= value <= bounds[1]):
            return False
    return True


def select_from_pool(
    item: NormalizedMap,
    pool: Sequence[LiveComparable],
    *,
    tolerance_schedule: Sequence[float] = DEFAULT_TOLERANCE_SCHEDULE,
    min_comparables: int = DEFAULT_MIN_COMPARABLES,
    max_comparables: int = DEFAULT_MAX_COMPARABLES,
) -> tuple[list[LiveComparable], float]:
    """The pool members that answer for one map, and the tolerance that found them.

    Walks the same widening schedule the network path walks, for the same
    reason and with the same stopping rule -- so a map priced from the pool
    carries a tolerance meaning exactly what it means on the other path, and
    the confidence score built from it stays comparable between the two.

    Returns an empty list when even the widest step cannot find
    `min_comparables`, which is the caller's cue to spend a real search.
    """
    best: list[LiveComparable] = []
    best_tolerance = 0.0
    for tolerance in tolerance_schedule:
        window = reward_window(item, tolerance)
        found = [comp for comp in pool if matches_window(comp, window)]
        if len(found) > len(best):
            best, best_tolerance = found, tolerance
        if len(found) >= min_comparables:
            break
    if len(best) < min_comparables:
        return [], best_tolerance
    if len(best) > max_comparables:
        # Nearest first, so a cap keeps the closest evidence rather than
        # whichever listings happened to be indexed most recently.
        vector = np.array(item.feature_vector(), dtype=float)
        matrix = np.array([c.vector() for c in best], dtype=float)
        order = np.argsort(scaled_distance(matrix, vector))
        best = [best[i] for i in order[:max_comparables]]
    return best, best_tolerance


@dataclass
class FetchOutcome:
    """What one bucket's worth of searching produced, budget included."""

    comparables: list[LiveComparable] = field(default_factory=list)
    tolerance: float = 0.0
    total_matching: int = 0
    requests: int = 0
    from_cache: bool = False
    budget_exhausted: bool = False
    error: str = ""


class LiveComparableFinder:
    """Search, widen, fetch -- with a cache in front and a budget behind.

    The loop is the whole mode: ask for maps inside a tight window, and if too
    few come back, open the window one step and ask again. Every step costs a
    search, which is why the schedule is short and the budget is checked before
    each one rather than after.
    """

    def __init__(
        self,
        client: TradeSearchClient,
        cache: BucketCache,
        budget: RequestBudget,
        *,
        league: str = "",
        tolerance_schedule: Sequence[float] = DEFAULT_TOLERANCE_SCHEDULE,
        min_comparables: int = DEFAULT_MIN_COMPARABLES,
        max_comparables: int = DEFAULT_MAX_COMPARABLES,
        status: str = "online",
    ):
        self.client = client
        self.cache = cache
        self.budget = budget
        self.league = league
        self.tolerance_schedule = tuple(tolerance_schedule) or DEFAULT_TOLERANCE_SCHEDULE
        self.min_comparables = min_comparables
        self.max_comparables = max_comparables
        self.status = status

    def key_for(self, item: NormalizedMap) -> str:
        return bucket_key(
            item,
            league=self.league,
            map_tier=self.client.map_tier,
            require_uncorrupted=self.client.require_uncorrupted,
        )

    def build_pool(
        self,
        *,
        slices: Sequence[dict[str, Any]] | None = None,
        per_slice: int = DEFAULT_POOL_PER_SLICE,
        on_slice: Callable[[int, int, int], None] | None = None,
    ) -> LivePool:
        """Read one shared set of live listings for the whole tab.

        The slices are the collector's own -- the same stratification the
        training path uses, and for the same reason: a trade search returns at
        most a hundred ids however many match, so one query cannot see the
        market and sixteen spread across the reward range can.

        Cost is flat in the number of maps. Sixteen searches and eighty fetches
        price a three-map tab and a seventy-map tab alike, where the per-map
        path pays a search and up to three fetches for each distinct bucket.
        """
        slices = list(slices if slices is not None else default_slices())
        found: list[LiveComparable] = []
        seen: set[str] = set()
        requests_used = 0
        used_slices = 0
        error = ""

        for index, spec in enumerate(slices):
            if not self.budget.can_spend(1):
                error = "request budget ran out while building the pool"
                break
            query = self.client.build_query(
                sort_by=spec.get("sort_by", "indexed"),
                sort=spec.get("sort", "desc"),
                status=self.status,
                collapse=True,
                extra_stats=spec.get("extra_stats"),
            )
            self.budget.spend(1)
            requests_used += 1
            try:
                query_id, ids, _total = self.client.search(query)
            except TradeApiError as exc:
                # One slice failing is not the run failing: the pool is a union,
                # and fifteen slices still price most of a tab.
                error = str(exc)
                break
            used_slices += 1
            ids = [i for i in ids[:per_slice] if i not in seen]
            for start in range(0, len(ids), FETCH_BATCH):
                if not self.budget.can_spend(1):
                    error = "request budget ran out while building the pool"
                    break
                self.budget.spend(1)
                requests_used += 1
                try:
                    results = self.client.fetch(ids[start : start + FETCH_BATCH], query_id)
                except TradeApiError as exc:
                    error = str(exc)
                    break
                for result in results:
                    comp = to_live_comparable(
                        result,
                        self.client.converter,
                        self.client.map_tier,
                        self.client.require_uncorrupted,
                    )
                    if comp is None:
                        continue
                    # Listings repeat across slices -- a map with both a currency
                    # and a scarab bonus is in two of them -- and a duplicate
                    # would count twice in the weighted quantile.
                    key = comp.listing_id or f"{comp.seller}|{comp.price_chaos}|{comp.vector()}"
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(comp)
            if on_slice is not None:
                on_slice(index + 1, len(slices), len(found))

        return LivePool(
            comparables=tuple(found),
            requests=requests_used,
            slices_used=used_slices,
            fetched_at=datetime.now(timezone.utc),
            error=error,
        )

    def find_in_pool(self, item: NormalizedMap, pool: LivePool) -> FetchOutcome:
        """Answer for one map out of the shared pool, spending nothing."""
        comparables, tolerance = select_from_pool(
            item,
            pool.comparables,
            tolerance_schedule=self.tolerance_schedule,
            min_comparables=self.min_comparables,
            max_comparables=self.max_comparables,
        )
        return FetchOutcome(
            comparables=comparables,
            tolerance=tolerance,
            total_matching=len(comparables),
            requests=0,
            from_cache=True,
        )

    def find(self, item: NormalizedMap) -> FetchOutcome:
        key = self.key_for(item)
        cached = self.cache.get(key)
        if cached is not None:
            return FetchOutcome(
                comparables=list(cached.comparables),
                tolerance=cached.tolerance,
                total_matching=cached.total_matching,
                requests=0,
                from_cache=True,
            )

        best = FetchOutcome()
        for tolerance in self.tolerance_schedule:
            if not self.budget.can_spend(1):
                best.budget_exhausted = True
                return best
            query = comparable_query(self.client.build_query, item, tolerance, self.status)
            self.budget.spend(1)
            best.requests += 1
            try:
                query_id, ids, total = self.client.search(query)
            except TradeApiError as exc:
                # The client has already backed off; widening on a rate-limit
                # error would only spend more budget on a bigger query.
                best.error = str(exc)
                return best

            ids = ids[: self.max_comparables]
            found: list[LiveComparable] = []
            for start in range(0, len(ids), FETCH_BATCH):
                if not self.budget.can_spend(1):
                    best.budget_exhausted = True
                    break
                self.budget.spend(1)
                best.requests += 1
                try:
                    results = self.client.fetch(ids[start : start + FETCH_BATCH], query_id)
                except TradeApiError as exc:
                    best.error = str(exc)
                    break
                for result in results:
                    comp = to_live_comparable(
                        result,
                        self.client.converter,
                        self.client.map_tier,
                        self.client.require_uncorrupted,
                    )
                    if comp is not None:
                        found.append(comp)

            if len(found) > len(best.comparables):
                best.comparables = found
                best.tolerance = tolerance
                best.total_matching = total

            if len(found) >= self.min_comparables or best.budget_exhausted:
                break

        if best.comparables:
            self.cache.put(
                key,
                CacheEntry(
                    stored_at=datetime.now(timezone.utc),
                    tolerance=best.tolerance,
                    comparables=best.comparables,
                    total_matching=best.total_matching,
                    requests=best.requests,
                ),
            )
        return best


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------

@dataclass
class MapOutcome:
    fingerprint: str
    derivation: MarketDerivation | None = None
    reason: str = ""

    @property
    def priced(self) -> bool:
        return self.derivation is not None


@dataclass
class MarketRunState:
    """What has been priced so far, so a run can stop and be picked up again.

    Interrupting a run -- closing the page, hitting the budget, a 429 -- must not
    throw away the requests already spent. Results are held by item fingerprint,
    and a second pass over the same tab skips everything already in here and
    spends what is left of the budget on the rest.
    """

    outcomes: dict[str, MapOutcome] = field(default_factory=dict)
    requests_used: int = 0
    cache_hits: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stopped_reason: str = ""
    #: Listings in the shared pool, and the requests it cost. Zero on a run that
    #: did not pool.
    pool_size: int = 0
    pool_requests: int = 0
    #: Maps the pool could not answer for, which fell back to their own search.
    pool_fallbacks: int = 0

    def pending(self, items: Iterable[NormalizedMap]) -> list[NormalizedMap]:
        return [i for i in items if i.fingerprint() not in self.outcomes]

    def record(self, outcome: MapOutcome) -> None:
        self.outcomes[outcome.fingerprint] = outcome

    def get(self, item: NormalizedMap) -> MapOutcome | None:
        return self.outcomes.get(item.fingerprint())

    @property
    def priced_count(self) -> int:
        return sum(1 for o in self.outcomes.values() if o.priced)


@dataclass
class MarketProgress:
    done: int
    total: int
    requests_used: int
    request_budget: int
    eta_seconds: float
    message: str = ""

    def caption(self) -> str:
        return (
            f"{self.done}/{self.total} maps  |  {self.requests_used}/{self.request_budget} "
            f"requests  |  about {format_duration(self.eta_seconds)} left"
        )
