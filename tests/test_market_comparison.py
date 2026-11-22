"""The live market-comparison pricing mode.

**Nothing here touches the network.** Every test drives a `FakeTradeClient` that
returns recorded-shape payloads -- the same shape a real /fetch result has, taken
from the live response captured in conftest -- so the suite can assert on
tolerance widening, budget exhaustion and cache behaviour without spending a
single request against GGG.

The one thing that *is* real is query construction: the fake delegates
`build_query` to the actual collector, so a change that breaks the query breaks
these tests rather than quietly returning nothing at runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.market.collectors.official_trade_api import (
    REWARD_STATS,
    OfficialTradeApiCollector,
    TradeApiError,
)
from src.market.live_comparables import (
    BucketCache,
    CacheEntry,
    LiveComparable,
    LiveComparableFinder,
    RequestBudget,
    bucket_key,
    comparable_query,
    derive_market_estimate,
    describe_window,
    distance_weights,
    estimate_seconds,
    reward_window,
    score_market_confidence,
    to_live_comparable,
    weighted_quantile,
)
from src.market.rate_limit import RateLimiter
from src.parsing.item_parser import classify, parse_item_text
from src.pricing.currency import CurrencyConverter
from src.schema import ORIGINATOR_IMPLICIT_STAT_ID, NormalizedMap
from src.market.collectors.official_trade_api import QUANTITY_FILTER

#: The reference present for every fixture in this file. It has to move with
#: the real clock, not sit on a date: the paths that go through `MarketPricer`
#: age listings against `datetime.now()` rather than an injected one, so a
#: pinned NOW makes every fixture listing older by a day each day until it
#: crosses `max_listing_age_hours` and the freshness filter throws all of them
#: away. That failed the suite on a calendar boundary rather than on a change,
#: with each test reporting nothing priced instead of a stale fixture.
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------
def a_map(**overrides) -> NormalizedMap:
    """A supported T16 rare Originator Map with a plausible reward roll."""
    from tests.conftest import T16_UNCORRUPTED_RARE

    item = classify(
        parse_item_text(T16_UNCORRUPTED_RARE), map_tier=16, require_uncorrupted=True
    )
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def api_result(
    listing_id: str,
    price_amount: float,
    price_currency: str = "chaos",
    *,
    quantity: int = 84,
    rarity: int = 134,
    pack_size: int = 32,
    more_currency: int = 47,
    more_maps: int = 35,
    age_hours: float = 1.0,
    seller: str = "Exile#1234",
) -> dict:
    """One /fetch result, in the shape a live 3.29 response returns."""
    properties = [
        {"name": "Item Quantity", "values": [[f"+{quantity}%", 1]], "type": 2},
        {"name": "Item Rarity", "values": [[f"+{rarity}%", 1]], "type": 3},
        {"name": "Monster Pack Size", "values": [[f"+{pack_size}%", 1]], "type": 4},
    ]
    if more_maps:
        properties.append({"name": "More Maps", "values": [[f"+{more_maps}%", 1]], "type": 88})
    if more_currency:
        properties.append(
            {"name": "More Currency", "values": [[f"+{more_currency}%", 1]], "type": 90}
        )
    return {
        "id": listing_id,
        "listing": {
            "indexed": (NOW - timedelta(hours=age_hours)).isoformat().replace("+00:00", "Z"),
            "price": {"type": "~b/o", "amount": price_amount, "currency": price_currency},
            "account": {"name": seller},
        },
        "item": {
            "league": "Allflame",
            "name": "Terror Solace",
            "baseType": "Map (Tier 16)",
            "typeLine": "Map (Tier 16)",
            "rarity": "Rare",
            "ilvl": 84,
            "identified": True,
            "properties": properties,
            "implicitMods": [
                {"description": "Area is Influenced by the Originator's Memories"}
            ],
            "extended": {"hashes": {"implicit": [["implicit.stat_2696470877", [0]]]}},
            "frameType": 2,
        },
    }


class FakeTradeClient:
    """A trade client that answers from a script instead of from GGG.

    `pages` is one entry per search call: either a list of /fetch results, or a
    `TradeApiError` to raise. Query construction is delegated to the real
    collector so the fake cannot drift away from the shape the API sees.
    """

    def __init__(self, pages, map_tier: int = 16, require_uncorrupted: bool = True):
        self.converter = CurrencyConverter(210.0)
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted
        self._collector = OfficialTradeApiCollector(
            league="Allflame",
            user_agent="test",
            converter=self.converter,
            map_tier=map_tier,
            require_uncorrupted=require_uncorrupted,
        )
        self.search_limiter = self._collector.search_limiter
        self.pages = list(pages)
        self.searches: list[dict] = []
        self.fetches: list[list[str]] = []
        self._current: dict[str, dict] = {}

    def build_query(self, **kwargs):
        return self._collector.build_query(**kwargs)

    def search(self, query):
        self.searches.append(query)
        page = self.pages[min(len(self.searches) - 1, len(self.pages) - 1)]
        if isinstance(page, Exception):
            raise page
        self._current = {r["id"]: r for r in page}
        return f"query-{len(self.searches)}", list(self._current), len(page)

    def fetch(self, ids, query_id):
        self.fetches.append(list(ids))
        return [self._current[i] for i in ids if i in self._current]


def finder_for(pages, budget: int = 100, **kwargs) -> LiveComparableFinder:
    return LiveComparableFinder(
        client=FakeTradeClient(pages),
        cache=BucketCache(ttl_minutes=30, now=lambda: NOW),
        budget=RequestBudget(limit=budget),
        league="Allflame",
        **kwargs,
    )


def comps(prices, **overrides) -> list[LiveComparable]:
    converter = CurrencyConverter(210.0)
    return [
        to_live_comparable(api_result(f"l{i}", price, **overrides), converter, 16, True)
        for i, price in enumerate(prices)
    ]


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------
def test_the_window_is_centred_on_the_map_and_scaled_per_feature():
    item = a_map()
    window = reward_window(item, 1.0)
    # quantity 84 with a base half-width of 10; rarity 134 with 20.
    assert window["quantity"] == (74, 94)
    assert window["rarity"] == (114, 154)
    assert window["pack_size"] == (27, 37)
    assert window["more_currency"] == (35, 59)


def test_widening_the_tolerance_widens_every_window():
    item = a_map()
    tight = reward_window(item, 1.0)
    wide = reward_window(item, 2.0)
    for name, bounds in tight.items():
        if bounds is None:
            assert wide[name] is None, f"{name} appeared out of nowhere when widening"
            continue
        assert wide[name][0] <= bounds[0] and wide[name][1] >= bounds[1], name
    assert wide["quantity"] == (64, 104)


def test_a_reward_the_map_does_not_have_becomes_an_exclusion_not_a_range():
    """Verified live: the `not` group and the matching `and` group partition the
    market (492 vs 152 listings on more_scarabs), so a map with no scarab bonus
    can be priced against maps that also have none rather than pooled with maps
    carrying +50%."""
    item = a_map()
    assert item.more_scarabs == 0
    query = comparable_query(FakeTradeClient([]).build_query, item, 1.0)
    groups = query["query"]["stats"]
    scarab = [
        g
        for g in groups
        if any(f["id"] == REWARD_STATS["more_scarabs"] for f in g["filters"])
    ]
    assert scarab and scarab[0]["type"] == "not"
    assert scarab[0]["filters"][0]["value"] == {"min": 1}


def test_a_reward_the_map_has_becomes_a_bounded_range():
    query = comparable_query(FakeTradeClient([]).build_query, a_map(), 1.0)
    currency = [
        g
        for g in query["query"]["stats"]
        if any(f["id"] == REWARD_STATS["more_currency"] for f in g["filters"])
    ][0]
    assert currency["type"] == "and"
    assert currency["filters"][0]["value"] == {"min": 35, "max": 59}


def test_the_query_keeps_the_scope_the_originator_implicit_and_the_sort():
    query = comparable_query(FakeTradeClient([]).build_query, a_map(), 1.0)
    filters = query["query"]["filters"]
    assert filters["map_filters"]["filters"]["map_tier"] == {"min": 16, "max": 16}
    assert filters["map_filters"]["filters"][QUANTITY_FILTER] == {"min": 74, "max": 94}
    assert filters["map_filters"]["filters"]["map_iir"] == {"min": 114, "max": 154}
    assert filters["map_filters"]["filters"]["map_packsize"] == {"min": 27, "max": 37}
    assert filters["type_filters"]["filters"]["rarity"] == {"option": "rare"}
    assert filters["misc_filters"]["filters"]["corrupted"] == {"option": "false"}
    assert filters["trade_filters"]["filters"]["collapse"] == {"option": "true"}
    assert any(
        f["id"] == ORIGINATOR_IMPLICIT_STAT_ID for f in query["query"]["stats"][0]["filters"]
    )
    # A search returns at most 100 ids however many match, so the sort decides
    # which 100 arrive. Sorting by price would return one end of the very
    # distribution being measured.
    assert query["sort"] == {"indexed": "desc"}
    assert query["query"]["status"] == {"option": "online"}


def test_the_window_can_be_read_by_a_human():
    text = describe_window(reward_window(a_map(), 1.0))
    assert "qty 74-94" in text
    assert "no scarabs" in text


# ---------------------------------------------------------------------------
# Tolerance widening
# ---------------------------------------------------------------------------
def test_it_stops_at_the_first_tolerance_that_returns_enough():
    finder = finder_for([[api_result(f"a{i}", 40 + i) for i in range(8)]], min_comparables=6)
    outcome = finder.find(a_map())
    assert len(finder.client.searches) == 1
    assert outcome.tolerance == 1.0
    assert len(outcome.comparables) == 8


def test_it_widens_when_too_few_come_back_and_records_which_step_answered():
    finder = finder_for(
        [
            [api_result("a0", 40)],
            [api_result(f"b{i}", 40 + i) for i in range(7)],
        ],
        min_comparables=6,
    )
    outcome = finder.find(a_map())
    assert len(finder.client.searches) == 2
    assert outcome.tolerance == 2.0, "the tolerance that answered must be recorded"
    assert len(outcome.comparables) == 7
    # And the second search really was wider than the first.
    first, second = (
        q["query"]["filters"]["map_filters"]["filters"][QUANTITY_FILTER]
        for q in finder.client.searches
    )
    assert second["min"] < first["min"] and second["max"] > first["max"]


def test_widening_is_bounded_by_the_schedule():
    finder = finder_for([[]], min_comparables=6, tolerance_schedule=(1.0, 2.0, 4.0))
    outcome = finder.find(a_map())
    assert len(finder.client.searches) == 3, "must not widen forever"
    assert outcome.comparables == []


def test_a_rate_limit_error_stops_the_widening_rather_than_escalating():
    finder = finder_for([TradeApiError("rate limited by GGG on search; backing off")])
    outcome = finder.find(a_map())
    assert len(finder.client.searches) == 1
    assert "rate limited" in outcome.error


# ---------------------------------------------------------------------------
# Bucket cache
# ---------------------------------------------------------------------------
def test_near_identical_maps_share_a_bucket_and_cost_one_query():
    finder = finder_for([[api_result(f"a{i}", 40 + i) for i in range(8)]])
    first = finder.find(a_map())
    second = finder.find(a_map(quantity=82, rarity=136))
    assert len(finder.client.searches) == 1, "a second query was paid for the same bucket"
    assert second.from_cache and second.requests == 0
    assert [c.listing_id for c in second.comparables] == [
        c.listing_id for c in first.comparables
    ]


def test_a_genuinely_different_map_does_not_reuse_the_bucket():
    finder = finder_for([[api_result(f"a{i}", 40 + i) for i in range(8)]])
    finder.find(a_map())
    finder.find(a_map(quantity=140, more_currency=150, more_scarabs=60))
    assert len(finder.client.searches) == 2


def test_the_bucket_key_carries_the_scope():
    item = a_map()
    assert bucket_key(item, league="Allflame", map_tier=16) != bucket_key(
        item, league="Standard", map_tier=16
    )
    assert bucket_key(item, league="A", map_tier=16) != bucket_key(item, league="A", map_tier=15)


def test_a_stale_bucket_is_not_reused():
    """Live prices go stale. An hour-old answer presented as the current market
    is worse than saying nothing, so the TTL is a correctness rule."""
    clock = {"now": NOW}
    cache = BucketCache(ttl_minutes=30, now=lambda: clock["now"])
    cache.put(NOW.isoformat(), CacheEntry(stored_at=NOW, tolerance=1.0, comparables=comps([40])))
    assert cache.get(NOW.isoformat()) is not None
    clock["now"] = NOW + timedelta(minutes=31)
    assert cache.get(NOW.isoformat()) is None


def test_the_cache_survives_a_restart_which_is_what_makes_a_run_resumable(tmp_path):
    path = tmp_path / "cache.json"
    cache = BucketCache(ttl_minutes=30, path=path, now=lambda: NOW)
    cache.put("k", CacheEntry(stored_at=NOW, tolerance=2.0, comparables=comps([40, 50])))
    cache.save()

    reopened = BucketCache(ttl_minutes=30, path=path, now=lambda: NOW)
    entry = reopened.get("k")
    assert entry is not None
    assert entry.tolerance == 2.0
    assert [c.price_chaos for c in entry.comparables] == [40.0, 50.0]
    assert entry.comparables[0].indexed_at is not None


def test_an_expired_entry_is_not_written_back_to_disk(tmp_path):
    path = tmp_path / "cache.json"
    clock = {"now": NOW}
    cache = BucketCache(ttl_minutes=30, path=path, now=lambda: clock["now"])
    cache.put("k", CacheEntry(stored_at=NOW, tolerance=1.0, comparables=comps([40])))
    clock["now"] = NOW + timedelta(hours=2)
    cache.save()
    assert BucketCache(ttl_minutes=30, path=path, now=lambda: clock["now"]).get("k") is None


def test_a_corrupt_cache_file_is_ignored_rather_than_fatal(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(BucketCache(ttl_minutes=30, path=path, now=lambda: NOW)) == 0


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
def test_the_budget_stops_the_run_before_the_search_not_after():
    finder = finder_for([[api_result("a0", 40)]], budget=0)
    outcome = finder.find(a_map())
    assert finder.client.searches == [], "a request was sent with no budget for it"
    assert outcome.budget_exhausted


def test_the_budget_covers_fetches_as_well_as_searches():
    finder = finder_for([[api_result(f"a{i}", 40 + i) for i in range(25)]], budget=2)
    outcome = finder.find(a_map())
    assert finder.client.fetches == [[f"a{i}" for i in range(10)]]
    assert outcome.budget_exhausted
    # One search plus one fetch, and not a third request.
    assert finder.budget.used == 2


def test_budget_accounting_is_exact():
    budget = RequestBudget(limit=3)
    assert budget.can_spend(3) and not budget.can_spend(4)
    budget.spend(3)
    assert budget.exhausted and budget.remaining == 0


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------
def _pricer(client, tmp_path, budget: int):
    """A pricer over a fake client, with a file-backed cache under tmp_path."""
    from src.config import Config, _DEFAULTS
    from src.database.repository import Repository
    from src.pricing.fair_value import PricingService
    from src.pricing.market_comparison import (
        MarketComparisonPricer,
        MarketComparisonSettings,
    )

    cfg = Config({**_DEFAULTS, "league": "Allflame"})
    repo = Repository(tmp_path / "test.db")
    service = PricingService(cfg, repo, None, CurrencyConverter(210.0))
    settings = MarketComparisonSettings(
        request_budget=budget, cache_path=str(tmp_path / "cache.json")
    )
    return MarketComparisonPricer(
        service=service,
        client=client,
        settings=settings,
        cache=BucketCache(ttl_minutes=30, path=tmp_path / "cache.json", now=lambda: NOW),
        budget=RequestBudget(limit=budget),
    )


def test_a_run_that_hits_the_budget_keeps_what_it_already_priced(tmp_path):
    items = [a_map(), a_map(quantity=140, more_currency=150, more_scarabs=60)]
    client = FakeTradeClient([[api_result(f"a{i}", 40 + i) for i in range(8)]])
    # Two requests is exactly one map's worth: one search, one fetch.
    pricer = _pricer(client, tmp_path, budget=2)
    state = pricer.run(items)

    assert state.priced_count == 1
    assert len(state.pending(items)) == 1
    assert "budget" in state.stopped_reason


def test_re_running_continues_rather_than_starting_over(tmp_path):
    items = [a_map(), a_map(quantity=140, more_currency=150, more_scarabs=60)]
    client = FakeTradeClient([[api_result(f"a{i}", 40 + i) for i in range(8)]])
    state = _pricer(client, tmp_path, budget=2).run(items)
    assert state.priced_count == 1
    searches_after_first = len(client.searches)

    # A fresh pricer, a fresh budget, the same state and the same cache on disk.
    state = _pricer(client, tmp_path, budget=10).run(items, state=state)
    assert state.priced_count == 2
    assert not state.pending(items)
    assert len(client.searches) == searches_after_first + 1, (
        "the already-priced map was searched for again"
    )


def test_a_second_run_over_the_same_tab_costs_nothing_from_the_disk_cache(tmp_path):
    items = [a_map()]
    client = FakeTradeClient([[api_result(f"a{i}", 40 + i) for i in range(8)]])
    _pricer(client, tmp_path, budget=10).run(items)
    assert len(client.searches) == 1

    # New run, new state -- but the bucket is still in the file.
    second = _pricer(client, tmp_path, budget=10)
    state = second.run(items)
    assert state.priced_count == 1
    assert len(client.searches) == 1, "the cached bucket was re-fetched"
    assert second.budget.used == 0


def test_unsupported_maps_are_recorded_without_spending_a_request(tmp_path):
    from tests.conftest import T6_MAGIC_ORIGINATOR

    magic = classify(parse_item_text(T6_MAGIC_ORIGINATOR), map_tier=16, require_uncorrupted=True)
    assert not magic.supported
    client = FakeTradeClient([[api_result("a0", 40)]])
    state = _pricer(client, tmp_path, budget=10).run([magic])
    assert client.searches == []
    assert state.priced_count == 0
    assert state.get(magic).reason


# ---------------------------------------------------------------------------
# Price derivation
# ---------------------------------------------------------------------------
def test_the_price_is_a_weighted_median_so_a_distant_comparable_cannot_set_it():
    item = a_map()
    near = comps([100, 100, 100])
    far = comps([400, 400, 400], quantity=140, rarity=200, pack_size=48, more_currency=140)
    result = derive_market_estimate(item, near + far, tolerance=1.0, now=NOW)
    assert result.fair_value_chaos == 100.0, (
        "the maps that look nothing like this one set the price"
    )
    nearest = result.comparables[0]
    assert nearest.weight > result.comparables[-1].weight


def test_weights_fall_away_with_distance():
    weights = distance_weights([0.0, 1.0, 2.0])
    assert weights[0] == 1.0
    assert weights[1] == pytest.approx(0.5)
    assert weights[2] == pytest.approx(0.2)


def test_a_weighted_quantile_always_returns_a_price_some_listing_actually_carries():
    values = [10.0, 20.0, 30.0, 40.0]
    weights = [1.0, 1.0, 1.0, 1.0]
    for q in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert weighted_quantile(values, weights, q) in values


def test_the_interval_comes_from_the_spread_of_the_comparables():
    item = a_map()
    tight = derive_market_estimate(item, comps([70] * 6), tolerance=1.0, now=NOW)
    wide = derive_market_estimate(
        item, comps([30, 45, 60, 80, 110, 150]), tolerance=1.0, now=NOW
    )
    tight_width = tight.interval_high_chaos - tight.interval_low_chaos
    wide_width = wide.interval_high_chaos - wide.interval_low_chaos
    assert tight_width == 0.0
    assert wide_width > 0.0
    # And the band is genuinely inside the observed prices, not a fabricated
    # multiple of the estimate.
    assert wide.interval_low_chaos in [c.price_chaos for c in wide.comparables]
    assert wide.interval_high_chaos in [c.price_chaos for c in wide.comparables]


def test_a_parked_absurd_ask_is_discarded_with_a_reason():
    item = a_map()
    result = derive_market_estimate(
        item, comps([60, 62, 64, 66, 68, 5000]), tolerance=1.0, now=NOW
    )
    assert 5000.0 not in [c.price_chaos for c in result.comparables]
    assert any("plausible range" in why for _, why in result.discarded)


def test_a_peer_test_mirrors_the_stored_listing_outlier_pass():
    item = a_map()
    result = derive_market_estimate(
        item, comps([60, 61, 62, 63, 64, 900]), tolerance=1.0, now=NOW
    )
    assert 900.0 not in [c.price_chaos for c in result.comparables]
    assert any("peers" in why for _, why in result.discarded)


def test_stale_listings_are_dropped_before_the_price_is_derived():
    item = a_map()
    fresh = comps([60, 62, 64], age_hours=1.0)
    stale = comps([300, 320, 340], age_hours=200.0)
    result = derive_market_estimate(
        item, fresh + stale, tolerance=1.0, max_age_hours=48.0, now=NOW
    )
    assert len(result.comparables) == 3
    assert all("days ago" in why for _, why in result.discarded)


def test_nothing_usable_is_an_error_rather_than_a_made_up_price():
    from src.market.live_comparables import NoComparables

    with pytest.raises(NoComparables):
        derive_market_estimate(a_map(), comps([2, 3]), tolerance=1.0, now=NOW)


def test_prices_in_other_currencies_go_through_the_converter():
    """Conversion is not reinvented here -- it is the same CurrencyConverter the
    collector uses, so a divine ask and a chaos ask are comparable at all."""
    converter = CurrencyConverter(210.0)
    comp = to_live_comparable(api_result("a0", 2.0, "divine"), converter, 16, True)
    assert comp.price_chaos == 420.0


def test_a_listing_that_is_not_an_originator_map_is_not_a_comparable():
    result = api_result("a0", 40)
    result["item"]["implicitMods"] = []
    result["item"]["extended"] = {}
    assert to_live_comparable(result, CurrencyConverter(210.0), 16, True) is None


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------
def _confidence(comparables, **kwargs):
    defaults = dict(fair=60.0, low=55.0, high=65.0, tolerance=1.0, now=NOW)
    defaults.update(kwargs)
    return score_market_confidence(comparables, **defaults)


def test_plenty_of_close_fresh_agreeing_comparables_scores_high():
    level, score, _ = _confidence(comps([60] * 12, age_hours=1.0))
    assert level == "High" and score >= 0.7


def test_confidence_degrades_when_there_are_few_comparables():
    many = _confidence(comps([60] * 12))[1]
    few = _confidence(comps([60] * 2))[1]
    assert few < many
    assert any("only 2 live comparables" in r for r in _confidence(comps([60] * 2))[2])


def test_confidence_degrades_when_the_tolerance_had_to_be_widened():
    tight = _confidence(comps([60] * 12), tolerance=1.0)[1]
    wide = _confidence(comps([60] * 12), tolerance=4.0)[1]
    assert wide < tight
    assert any("widened" in r for r in _confidence(comps([60] * 12), tolerance=4.0)[2])


def test_confidence_degrades_when_the_listings_are_stale():
    fresh = _confidence(comps([60] * 12, age_hours=1.0))[1]
    stale = _confidence(comps([60] * 12, age_hours=40.0))[1]
    assert stale < fresh
    assert any("old" in r for r in _confidence(comps([60] * 12, age_hours=40.0))[2])


def test_confidence_degrades_when_the_comparables_disagree():
    agreeing = _confidence(comps([60] * 12), low=58.0, high=62.0)[1]
    scattered = _confidence(comps([60] * 12), low=20.0, high=200.0)[1]
    assert scattered < agreeing
    assert any(
        "disagree" in r for r in _confidence(comps([60] * 12), low=20.0, high=200.0)[2]
    )


def test_every_way_of_being_wrong_at_once_scores_low():
    level, _, _ = _confidence(
        comps([60] * 2, age_hours=47.0), tolerance=4.0, low=10.0, high=300.0
    )
    assert level == "Low"


def test_the_confidence_labels_match_the_model_modes_thresholds():
    """Both modes' labels sit in the same column, so 'Medium' has to mean the
    same thing in each."""
    import inspect

    from src.ml import confidence as model_confidence

    source = inspect.getsource(model_confidence.score_confidence)
    assert "score >= 0.7" in source and "score >= 0.45" in source


# ---------------------------------------------------------------------------
# Cost reporting
# ---------------------------------------------------------------------------
def test_the_eta_uses_the_sustained_rate_not_the_burst_rate():
    """GGG's live search policy on 2026-08-25. The first rule allows a request
    every two seconds; the third allows thirty every five minutes, which is ten.
    Quoting the burst rate would promise a one-minute run that takes ten."""
    limiter = RateLimiter("search")
    limiter.update_from_headers(
        {"X-Rate-Limit-Ip": "5:10:60,15:60:300,30:300:1800,600:21600:3600"}
    )
    assert limiter.sustained_interval() == pytest.approx(36.0)
    assert estimate_seconds(10, 10.0) == 100.0


def test_the_configured_guess_is_used_until_a_policy_is_known():
    assert RateLimiter("search", min_interval=2.0).sustained_interval() == 2.0


# ---------------------------------------------------------------------------
# The seam into PricedMap
# ---------------------------------------------------------------------------
def test_a_market_price_drops_into_the_same_priced_map_shape(tmp_path):
    items = [a_map()]
    client = FakeTradeClient([[api_result(f"a{i}", 40 + i) for i in range(8)]])
    pricer = _pricer(client, tmp_path, budget=10)
    state = pricer.run(items)
    priced = pricer.priced(items, state)[0]

    assert priced.supported and priced.source == "market"
    assert priced.estimate.fair_value_chaos > 0
    assert priced.estimate.confidence in ("High", "Medium", "Low")
    assert priced.note.startswith("~price")
    row = priced.row(pricer.service.formatter)
    assert row["Fair Value"] and row["Confidence"] in ("High", "Medium", "Low")
    # The evidence travels with the price. That is the point of the mode.
    assert len(priced.comparables) == 8
    assert {"distance", "weight", "seller", "age_hours"} <= set(priced.comparables.columns)


def test_the_two_modes_are_reported_side_by_side_not_merged(tmp_path):
    from src.pricing.market_comparison import compare_modes

    item = a_map()
    client = FakeTradeClient([[api_result(f"a{i}", 100) for i in range(8)]])
    pricer = _pricer(client, tmp_path, budget=10)
    market = pricer.priced([item], pricer.run([item]))

    model = [
        pricer.service.price_from_market(
            item,
            type(
                "Stub",
                (),
                {
                    "fair_value_chaos": 50.0,
                    "interval_low_chaos": 45.0,
                    "interval_high_chaos": 55.0,
                    "confidence": "High",
                    "confidence_score": 0.8,
                    "confidence_reasons": [],
                    "tolerance": 1.0,
                },
            )(),
        )
    ]
    rows = compare_modes(model, market)
    assert rows[0].model_chaos == 50.0
    assert rows[0].market_chaos == 100.0
    assert rows[0].gap_ratio == pytest.approx(2.0)
    assert rows[0].disagreement == pytest.approx(1.0)
