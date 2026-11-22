"""The market-comparison pricing mode, wired up.

`src/market/live_comparables.py` holds the logic -- query windows, tolerance
widening, bucket keys, weighting, the derivation and the budget. This module is
the adapter: it reads the config block, builds the trade client, walks a tab
while reporting progress, and turns each result into the same `PricedMap` the
model mode produces so that neither the UI nor the CLI has to special-case it.

The two modes sit beside each other and neither overwrites the other. A
`PricedMap` says which one produced it (`source`), and where both have run the
UI shows both columns -- the maps they disagree about most are the interesting
ones, and collapsing that into a single number would throw the signal away.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd

from src.config import Config, ROOT, user_agent
from src.database.repository import Repository
from src.market.cancel import CancelToken
from src.market.collectors.official_trade_api import (
    FETCH_BATCH,
    OfficialTradeApiCollector,
    default_slices,
)
from src.market.ingest import converter_for
from src.market.live_comparables import (
    DEFAULT_MAX_COMPARABLES,
    DEFAULT_MAX_LISTING_AGE_HOURS,
    DEFAULT_MIN_COMPARABLES,
    DEFAULT_POOL_PER_SLICE,
    DEFAULT_TOLERANCE_SCHEDULE,
    BucketCache,
    FetchOutcome,
    LiveComparableFinder,
    LivePool,
    MapOutcome,
    MarketProgress,
    MarketRunState,
    NoComparables,
    RequestBudget,
    TradeSearchClient,
    derive_market_estimate,
    estimate_seconds,
    format_duration,
)
from src.market.outliers import DEFAULT_PLAUSIBLE_CHAOS
from src.pricing.fair_value import PricedMap, PricingService
from src.schema import FEATURE_NAMES, NormalizedMap

#: Where the bucket cache lives when the config does not say. Under data/ with
#: the database, because it is the same kind of thing: fetched market state that
#: is worth keeping between runs and worth throwing away without a second
#: thought.
DEFAULT_CACHE_PATH = "data/market_comparison_cache.json"


@dataclass
class MarketComparisonSettings:
    """The `market_comparison` config block, with the defaults spelled out."""

    tolerance_schedule: tuple[float, ...] = DEFAULT_TOLERANCE_SCHEDULE
    min_comparables: int = DEFAULT_MIN_COMPARABLES
    max_comparables: int = DEFAULT_MAX_COMPARABLES
    request_budget: int = 200
    #: Read one shared set of listings for the whole tab instead of one search
    #: per map. On by default because the per-map path is flat-out slower for
    #: anything but a handful of maps -- see `MarketComparisonPricer.plan`.
    pool: bool = True
    pool_per_slice: int = DEFAULT_POOL_PER_SLICE
    cache_ttl_minutes: float = 30.0
    max_listing_age_hours: float = DEFAULT_MAX_LISTING_AGE_HOURS
    status: str = "online"
    cache_path: str = DEFAULT_CACHE_PATH
    plausible_chaos: tuple[float, float] = DEFAULT_PLAUSIBLE_CHAOS

    @classmethod
    def from_config(cls, cfg: Config) -> "MarketComparisonSettings":
        block = cfg.get("market_comparison", {}) or {}
        bounds = cfg.get("market.plausible_price_chaos", list(DEFAULT_PLAUSIBLE_CHAOS))
        schedule = tuple(float(x) for x in block.get("tolerance_schedule", DEFAULT_TOLERANCE_SCHEDULE))
        return cls(
            tolerance_schedule=schedule or DEFAULT_TOLERANCE_SCHEDULE,
            min_comparables=int(block.get("min_comparables", DEFAULT_MIN_COMPARABLES)),
            max_comparables=int(block.get("max_comparables", DEFAULT_MAX_COMPARABLES)),
            request_budget=int(block.get("request_budget", 200)),
            pool=bool(block.get("pool", True)),
            pool_per_slice=int(block.get("pool_per_slice", DEFAULT_POOL_PER_SLICE)),
            cache_ttl_minutes=float(block.get("cache_ttl_minutes", 30.0)),
            max_listing_age_hours=float(
                block.get("max_listing_age_hours", DEFAULT_MAX_LISTING_AGE_HOURS)
            ),
            status=str(block.get("status", "online")),
            cache_path=str(block.get("cache_path", DEFAULT_CACHE_PATH)),
            plausible_chaos=(float(bounds[0]), float(bounds[1])),
        )

    def resolved_cache_path(self) -> Path:
        path = Path(self.cache_path)
        return path if path.is_absolute() else (ROOT / path)


def client_for(
    cfg: Config, repo: Repository, cancel: "CancelToken | None" = None
) -> OfficialTradeApiCollector:
    """The same collector the training path uses, with the same limiter.

    Reused rather than reimplemented on purpose: the User-Agent, the
    header-driven rate limiting and the 429 back-off are not things this mode
    should have its own opinion about.

    `cancel` is what makes the page's Cancel button work, and leaving it out
    was a real bug. Without a token the collector's `_wait` falls through to a
    blind `time.sleep`, so a cancel is only noticed *between maps* -- and the
    sleeps on this path are rate-limit backoffs measured in minutes. GGG's
    published search policy carries an 1800-second penalty, so a run cancelled
    during one sat on "Stopping..." for up to half an hour, which reads as a
    broken button. `src/market/cancel.py` was written for exactly this and
    this caller never used it.
    """
    return OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=converter_for(cfg, repo),
        min_interval=cfg.get("http.min_request_interval_seconds", 2.0),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        cancel=cancel,
    )


def comparables_frame(outcome: MapOutcome, now: datetime | None = None) -> pd.DataFrame:
    """The evidence behind one price, in the same shape the model mode shows.

    Same column names as `src/market/comparables.py` so the existing detail panel
    renders it without knowing which mode produced it, plus the two columns that
    are the point of this mode: how far each listing is from the map, and how
    much of the answer it therefore carries.
    """
    columns = list(FEATURE_NAMES) + [
        "price_chaos",
        "price_amount",
        "price_currency",
        "seller",
        "age_hours",
        "distance",
        "weight",
    ]
    if outcome.derivation is None:
        return pd.DataFrame(columns=columns)
    now = now or datetime.now(timezone.utc)
    records = [
        {
            **{name: int(comp.features.get(name, 0) or 0) for name in FEATURE_NAMES},
            "price_chaos": comp.price_chaos,
            "price_amount": comp.price_amount,
            "price_currency": comp.price_currency,
            "seller": comp.seller,
            "age_hours": comp.age_hours(now),
            "distance": comp.distance,
            "weight": comp.weight,
        }
        for comp in outcome.derivation.comparables
    ]
    return pd.DataFrame.from_records(records, columns=columns)


@dataclass(frozen=True)
class RunPlan:
    """What a run is about to cost, before a single request goes out."""

    pooled: bool
    maps: int
    searches: int
    fetches: int
    seconds: float

    def caption(self) -> str:
        how = (
            f"one shared read of the market ({self.searches} searches, "
            f"{self.fetches} fetches) for all {self.maps} maps"
            if self.pooled
            else f"{self.searches} searches and {self.fetches} fetches, one map at a time"
        )
        return f"{how} -- about {format_duration(self.seconds)} under GGG's rate limits"


class MarketComparisonPricer:
    """Prices a set of maps from live listings, resumably and within a budget."""

    def __init__(
        self,
        service: PricingService,
        client: TradeSearchClient,
        settings: MarketComparisonSettings | None = None,
        cache: BucketCache | None = None,
        budget: RequestBudget | None = None,
    ):
        self.service = service
        self.client = client
        self.settings = settings or MarketComparisonSettings()
        self.cache = cache or BucketCache(
            ttl_minutes=self.settings.cache_ttl_minutes,
            path=self.settings.resolved_cache_path(),
        )
        self.budget = budget or RequestBudget(limit=self.settings.request_budget)
        self.finder = LiveComparableFinder(
            client=client,
            cache=self.cache,
            budget=self.budget,
            league=service.cfg.get("league", ""),
            tolerance_schedule=self.settings.tolerance_schedule,
            min_comparables=self.settings.min_comparables,
            max_comparables=self.settings.max_comparables,
            status=self.settings.status,
        )

    # ------------------------------------------------------------------
    @classmethod
    def build(
        cls,
        cfg: Config,
        repo: Repository,
        service: PricingService,
        budget: int | None = None,
        tolerance_schedule: Sequence[float] | None = None,
        cancel: "CancelToken | None" = None,
    ) -> "MarketComparisonPricer":
        settings = MarketComparisonSettings.from_config(cfg)
        if budget is not None:
            settings.request_budget = int(budget)
        if tolerance_schedule:
            settings.tolerance_schedule = tuple(float(x) for x in tolerance_schedule)
        return cls(
            service=service,
            client=client_for(cfg, repo, cancel),
            settings=settings,
        )

    # ------------------------------------------------------------------
    def seconds_per_request(self) -> float:
        """What one more request costs in wall-clock, at the sustained rate."""
        limiter = getattr(self.client, "search_limiter", None)
        if limiter is None:
            return 0.0
        return limiter.sustained_interval()

    def plan(self, maps: int) -> "RunPlan":
        """What this run will cost, in requests and in wall-clock.

        Split by endpoint because the two have different policies and the
        difference decides the design: GGG allows thirty searches per five
        minutes but only fifty *fetches* in the same window, so on the per-map
        path -- where every search is followed by up to three fetches -- the
        fetches are the larger half of the bill, and counting requests as one
        undifferentiated number hides that.

        The old estimate multiplied a request count by the search limiter's
        sustained interval. Two things were wrong with it: before the first
        response that interval was a two-second placeholder rather than a
        policy, and even afterwards it charged every request at the rate of the
        six-hour rule, which does not bind until the six-hundredth. It promised
        six minutes for runs that took an hour and a half.
        """
        return self._plan_for(maps, self.should_pool(maps))

    def should_pool(self, maps: int) -> bool:
        """Pool only when pooling is actually the cheaper way to price this tab.

        A shared read costs the same sixteen searches whether there are three
        maps or seventy, so on a small tab it is pure overhead -- three maps
        answer in three searches and nine fetches, inside every burst allowance,
        in no measurable time at all. Rather than pick a threshold and defend it,
        both plans are costed against the published policy and the cheaper one
        wins; the crossover then moves on its own if GGG changes the rules.
        """
        if not self.settings.pool or maps <= 0:
            return False
        return self._plan_for(maps, True).seconds < self._plan_for(maps, False).seconds

    def _plan_for(self, maps: int, pooled: bool) -> "RunPlan":
        search = getattr(self.client, "search_limiter", None)
        fetch = getattr(self.client, "fetch_limiter", None)
        per_fetch = max(1, FETCH_BATCH)

        if pooled and maps > 0:
            slices = len(default_slices())
            searches = slices
            fetches = slices * math.ceil(self.settings.pool_per_slice / per_fetch)
        else:
            # One search per distinct map is the floor, and the tolerance
            # schedule is what it costs when the first window comes back short.
            searches = maps
            fetches = maps * math.ceil(self.settings.max_comparables / per_fetch)

        searches = min(searches, self.budget.limit)
        fetches = min(fetches, max(0, self.budget.limit - searches))
        seconds = 0.0
        if search is not None:
            seconds += search.seconds_for(searches)
        if fetch is not None:
            seconds += fetch.seconds_for(fetches)
        return RunPlan(
            pooled=bool(pooled and maps > 0),
            maps=maps,
            searches=searches,
            fetches=fetches,
            seconds=seconds,
        )

    def run(
        self,
        items: Sequence[NormalizedMap],
        state: MarketRunState | None = None,
        progress: Callable[[MarketProgress], None] | None = None,
        stop: Callable[[], bool] | None = None,
    ) -> MarketRunState:
        """Price everything not already priced, until the budget runs out.

        Returns the state, whether it finished or not. Nothing is thrown away
        when it does not: every map already priced stays priced, and calling this
        again with the same state continues from there.
        """
        state = state or MarketRunState()
        pending = state.pending(items)
        total = len(items)
        #: Maps that actually reached the network, so the estimate is per *paid*
        #: map. Counting every outcome would divide the requests spent by a total
        #: padded with unsupported maps and cache hits, which cost nothing, and
        #: the estimate would fall as the run went on rather than converging.
        paid_lookups = 0

        def report(message: str) -> None:
            if not progress:
                return
            left = len(state.pending(items))
            # Two requests -- one search, one fetch -- is the floor for a map that
            # answers on the first tolerance step, so it is the optimistic
            # assumption until the run has measured its own average.
            per_map = max(2.0, self.budget.used / paid_lookups) if paid_lookups else 2.0
            projected = min(self.budget.remaining, int(left * per_map))
            # Costed as searches: the split between the two endpoints is not
            # knowable mid-run, and searches are the dearer of the two, so this
            # is the honest upper end rather than a number that drifts down.
            limiter = getattr(self.client, "search_limiter", None)
            eta = (
                limiter.seconds_for(projected)
                if limiter is not None
                else estimate_seconds(projected, self.seconds_per_request())
            )
            progress(
                MarketProgress(
                    done=len(state.outcomes),
                    total=total,
                    requests_used=self.budget.used,
                    request_budget=self.budget.limit,
                    eta_seconds=eta,
                    message=message,
                )
            )

        report("starting")

        # One read of the market for the whole tab, before any map is priced.
        # Everything below then asks the pool first and only pays for a search
        # when the pool genuinely cannot answer -- which is the unusual map, not
        # the common one.
        pool = LivePool()
        if self.should_pool(sum(1 for i in pending if i.supported)):
            report(f"reading the market once for all {len(pending)} maps")
            pool = self.finder.build_pool(
                per_slice=self.settings.pool_per_slice,
                on_slice=lambda done, total, found: report(
                    f"market read {done}/{total}: {found} listings"
                ),
                stop=stop,
            )
            state.pool_size = len(pool)
            state.pool_requests = pool.requests
            state.requests_used = self.budget.used
            report(f"{len(pool)} live listings pooled in {pool.requests} requests")

        for item in pending:
            if stop is not None and stop():
                state.stopped_reason = self._cancelled_reason(state, items)
                break
            if self.budget.exhausted:
                state.stopped_reason = (
                    f"request budget of {self.budget.limit} used up; "
                    f"{len(state.pending(items))} maps left. Run it again to continue."
                )
                break
            if not item.supported:
                state.record(
                    MapOutcome(
                        fingerprint=item.fingerprint(),
                        reason=item.unsupported_reason or "unsupported",
                    )
                )
                continue

            outcome = FetchOutcome()
            if pool.comparables:
                outcome = self.finder.find_in_pool(item, pool)
            if not outcome.comparables:
                # The pool holds nothing like this map. That is a real answer
                # about an unusual map rather than a failure, and it is worth a
                # search of its own precisely because it is rare.
                if pool.comparables:
                    state.pool_fallbacks += 1
                outcome = self.finder.find(item)
            state.requests_used = self.budget.used
            if outcome.from_cache:
                state.cache_hits += 1
            else:
                paid_lookups += 1

            if outcome.budget_exhausted and not outcome.comparables:
                state.stopped_reason = (
                    f"request budget of {self.budget.limit} used up; "
                    f"{len(state.pending(items))} maps left. Run it again to continue."
                )
                break

            if not outcome.comparables:
                state.record(
                    MapOutcome(
                        fingerprint=item.fingerprint(),
                        reason=outcome.error
                        or "no live listings matched, even at the widest tolerance",
                    )
                )
                report(f"{item.position_label()}: nothing comparable listed")
                continue

            try:
                derivation = derive_market_estimate(
                    item,
                    outcome.comparables,
                    tolerance=outcome.tolerance,
                    min_comparables=self.settings.min_comparables,
                    max_age_hours=self.settings.max_listing_age_hours,
                    plausible_chaos=self.settings.plausible_chaos,
                    total_matching=outcome.total_matching,
                )
            except NoComparables as exc:
                state.record(
                    MapOutcome(fingerprint=item.fingerprint(), reason=str(exc))
                )
                continue
            derivation.requests_used = outcome.requests
            derivation.from_cache = outcome.from_cache
            state.record(MapOutcome(fingerprint=item.fingerprint(), derivation=derivation))
            report(
                f"{item.position_label()}: {len(derivation.comparables)} comparables "
                f"at {derivation.tolerance:g}x"
            )

        if stop is not None and stop() and not state.stopped_reason:
            state.stopped_reason = self._cancelled_reason(state, items)

        # Saved on every exit, cancelled or not. The requests already spent are
        # the expensive part of this mode, and the cache is what stops a resumed
        # run paying for them twice.
        self.cache.save()
        state.requests_used = self.budget.used
        report(state.stopped_reason or "finished")
        return state

    @staticmethod
    def _cancelled_reason(state: MarketRunState, items: Sequence[NormalizedMap]) -> str:
        """What a cancelled run says for itself.

        "Stopped by the user" told them what they already knew. What they need
        to know is that the requests they waited for bought something: how many
        maps came back priced, how many are left, and that pressing the button
        again picks up from here rather than starting over.
        """
        left = len(state.pending(items))
        return (
            f"Cancelled with {state.priced_count} of {len(items)} maps priced and "
            f"{left} left. Nothing already found was thrown away -- press "
            f"Continue pricing to carry on from here."
        )

    # ------------------------------------------------------------------
    def priced(
        self,
        items: Sequence[NormalizedMap],
        state: MarketRunState,
        strategy: str = "market",
    ) -> list[PricedMap]:
        """`PricedMap` rows for every item, priced or not, in the order given."""
        out: list[PricedMap] = []
        for item in items:
            outcome = state.get(item)
            if outcome is None or outcome.derivation is None:
                out.append(
                    PricedMap(
                        item=item,
                        estimate=None,
                        strategy=strategy,
                        source="market",
                        note=(outcome.reason if outcome else "not priced yet"),
                    )
                )
                continue
            out.append(
                self.service.price_from_market(
                    item,
                    outcome.derivation,
                    comparables=comparables_frame(outcome),
                    strategy=strategy,
                )
            )
        return out


@dataclass
class ModeComparison:
    """One map, priced both ways, and how far apart the two answers are."""

    item: NormalizedMap
    model_chaos: float | None
    market_chaos: float | None

    @property
    def gap_ratio(self) -> float | None:
        """Market / model. 1.0 is agreement; 2.0 is the market asking double."""
        if not self.model_chaos or not self.market_chaos:
            return None
        return self.market_chaos / self.model_chaos

    @property
    def disagreement(self) -> float:
        """How far apart, symmetrically, for sorting. 0.0 when either is missing."""
        ratio = self.gap_ratio
        if ratio is None or ratio <= 0:
            return 0.0
        return abs(ratio - 1.0) if ratio >= 1 else abs(1.0 / ratio - 1.0)


def compare_modes(
    model_priced: Sequence[PricedMap], market_priced: Sequence[PricedMap]
) -> list[ModeComparison]:
    """Pair the two modes' answers by item fingerprint, worst disagreement first.

    Where the model and the live market disagree is the genuinely interesting
    output of running both: it is either a map the model has never seen anything
    like, or a corner of the market that has moved since the model was trained.
    Either way the user wants to look at it, so it is sorted to the top rather
    than averaged away.
    """
    by_fingerprint = {p.item.fingerprint(): p for p in market_priced}
    rows: list[ModeComparison] = []
    for entry in model_priced:
        other = by_fingerprint.get(entry.item.fingerprint())
        rows.append(
            ModeComparison(
                item=entry.item,
                model_chaos=entry.estimate.fair_value_chaos if entry.estimate else None,
                market_chaos=(
                    other.estimate.fair_value_chaos if other and other.estimate else None
                ),
            )
        )
    rows.sort(key=lambda r: r.disagreement, reverse=True)
    return rows
