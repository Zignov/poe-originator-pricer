"""Pricing a whole tab from one shared read of the market.

The mode's original shape was one search per map, and it did not scale: seventy
maps quantise to about sixty-five distinct buckets, so the bucket cache saves
almost nothing and each bucket pays a search plus up to three fetches. Against
GGG's published policy -- thirty searches and fifty fetches per five minutes --
that is the difference between a few minutes and an hour and a half.

Nothing here touches the network. The fake client from `test_market_comparison`
is reused rather than copied, so both paths are driven through the same
query-construction code and neither can drift away from the shape GGG sees.

The two properties worth protecting are in the first and last sections: the cost
of a pooled run does not grow with the number of maps, and a map the pool has
nothing like still gets a real search rather than a wrong price.
"""
from __future__ import annotations

from src.market.live_comparables import (
    DEFAULT_TOLERANCE_SCHEDULE,
    LiveComparable,
    LivePool,
    matches_window,
    reward_window,
    select_from_pool,
)
from src.market.rate_limit import (
    OBSERVED_FETCH_POLICY,
    OBSERVED_SEARCH_POLICY,
    RateLimiter,
)
from tests.test_market_comparison import (
    FakeTradeClient,
    _pricer,
    a_map,
    api_result,
    finder_for,
)


def comp(price: float = 50.0, **features) -> LiveComparable:
    base = {
        "quantity": 84,
        "rarity": 134,
        "pack_size": 32,
        "more_currency": 47,
        "more_maps": 35,
        "more_scarabs": 0,
        "more_divination_cards": 0,
    }
    base.update(features)
    return LiveComparable(features=base, price_chaos=price)


# ---------------------------------------------------------------------------
# The local half of the query
# ---------------------------------------------------------------------------
def test_a_listing_inside_every_window_is_a_comparable():
    item = a_map()
    window = reward_window(item, 1.0)
    assert matches_window(comp(**{k: v[0] for k, v in window.items() if v}), window)


def test_a_listing_outside_one_window_is_not():
    """One feature out of range is enough -- the query is an AND, not a score."""
    item = a_map()
    window = reward_window(item, 1.0)
    far = window["rarity"][1] + 1
    assert not matches_window(comp(rarity=far), window)


def test_a_reward_the_map_does_not_have_excludes_listings_that_do():
    """The local mirror of the `not` stat group, and the easiest thing to lose.

    A map with no scarab bonus must be compared only against maps with none.
    Pooling it in with maps carrying +50% still produces a price -- just one
    derived from maps that are not like yours, which is the failure this mode
    exists to avoid.
    """
    item = a_map(more_scarabs=0)
    window = reward_window(item, 1.0)
    assert window["more_scarabs"] is None
    assert matches_window(comp(more_scarabs=0), window)
    assert not matches_window(comp(more_scarabs=50), window)


# ---------------------------------------------------------------------------
# Selecting out of the pool
# ---------------------------------------------------------------------------
def test_the_pool_widens_through_the_same_schedule_the_searches_walk():
    """So a tolerance means the same thing whichever path produced it."""
    item = a_map()
    # Far enough out that only the 2.0 window reaches them.
    pool = [comp(price=40 + i, rarity=item.rarity + 30) for i in range(8)]

    found, tolerance = select_from_pool(item, pool, min_comparables=6)

    assert len(found) == 8
    assert tolerance == 2.0


def test_the_pool_answers_nothing_rather_than_answering_badly():
    """Too few comparables at the widest step is the cue to spend a search."""
    item = a_map()
    pool = [comp(price=40, rarity=item.rarity + 500)]

    found, _tolerance = select_from_pool(item, pool, min_comparables=6)

    assert found == []


def test_capping_the_pool_keeps_the_nearest_listings_not_the_first():
    item = a_map()
    near = [comp(price=40 + i, quantity=item.quantity) for i in range(6)]
    far = [comp(price=99, quantity=item.quantity + 9) for _ in range(6)]

    found, _ = select_from_pool(item, far + near, min_comparables=6, max_comparables=6)

    assert len(found) == 6
    assert all(c.features["quantity"] == item.quantity for c in found)


# ---------------------------------------------------------------------------
# Building the pool
# ---------------------------------------------------------------------------
def test_the_pool_reads_every_slice_and_keeps_each_listing_once():
    """Slices overlap by construction, and a duplicate would be counted twice.

    A map with both a currency and a scarab bonus is in more than one slice, so
    the union has to be taken by listing, not by concatenation -- otherwise the
    same ask carries several votes in the weighted quantile.
    """
    page = [api_result(f"dup{i}", 40 + i) for i in range(4)]
    client = FakeTradeClient([page])  # every slice returns the same four
    finder = finder_for([page], budget=500)
    finder.client = client

    pool = finder.build_pool(per_slice=10)

    assert len(client.searches) == 16
    assert len(pool) == 4
    assert pool.slices_used == 16


def test_the_pool_stops_cleanly_when_the_budget_runs_out():
    page = [api_result(f"a{i}", 40 + i) for i in range(4)]
    finder = finder_for([page], budget=3)

    pool = finder.build_pool(per_slice=10)

    assert pool.requests <= 3
    assert "budget" in pool.error


# ---------------------------------------------------------------------------
# What it costs -- the reason the pool exists
# ---------------------------------------------------------------------------
def test_a_pooled_run_costs_the_same_for_seventy_maps_as_for_twenty(tmp_path):
    """The property the whole change is for: cost flat in the number of maps."""
    page = [api_result(f"a{i}", 40 + i) for i in range(10)]

    used = []
    for count in (20, 70):
        items = [a_map(quantity=80 + i % 30) for i in range(count)]
        pricer = _pricer(FakeTradeClient([page]), tmp_path / f"run{count}", budget=400)
        state = pricer.run(items)
        used.append(state.requests_used)

    assert used[0] == used[1]


def test_pricing_a_tab_pools_instead_of_searching_once_per_map(tmp_path):
    items = [a_map(quantity=80 + i) for i in range(40)]
    page = [api_result(f"a{i}", 40 + i) for i in range(10)]
    client = FakeTradeClient([page])
    pricer = _pricer(client, tmp_path, budget=400)

    state = pricer.run(items)

    assert state.pool_size > 0
    # Sixteen slices, not forty maps' worth of searching.
    assert len(client.searches) <= 16 + state.pool_fallbacks
    assert state.priced_count > 0


def test_a_small_tab_is_not_slowed_down_by_pooling(tmp_path):
    """Three maps answer in three searches; sixteen would be pure overhead."""
    pricer = _pricer(FakeTradeClient([[api_result("a", 40)]]), tmp_path, budget=200)

    assert not pricer.should_pool(3)
    assert pricer.should_pool(70)
    assert pricer.plan(70).pooled
    assert not pricer.plan(3).pooled


def test_the_map_the_pool_cannot_answer_still_gets_a_search(tmp_path):
    """An unusual map is exactly the one worth spending a request on."""
    # Enough maps that pooling is the cheaper plan; below the crossover the
    # run searches per map anyway and there is no pool to fall back from.
    ordinary = [a_map() for _ in range(40)]
    odd = a_map(quantity=400, rarity=900, more_scarabs=77)
    page = [api_result(f"a{i}", 40 + i) for i in range(10)]
    client = FakeTradeClient([page])
    pricer = _pricer(client, tmp_path, budget=400)

    state = pricer.run([*ordinary, odd])

    assert state.pool_fallbacks >= 1
    assert len(client.searches) > 16


# ---------------------------------------------------------------------------
# The estimate that was wrong by eighteen times
# ---------------------------------------------------------------------------
def test_a_limiter_knows_the_policy_before_it_has_made_a_request():
    """The bug behind "about 6 minutes" for a run that took an hour and a half."""
    seeded = RateLimiter("search", min_interval=2.0, policy=OBSERVED_SEARCH_POLICY)
    unseeded = RateLimiter("search", min_interval=2.0)

    assert seeded.sustained_interval() == 36.0
    assert unseeded.sustained_interval() == 2.0


def test_the_server_still_overrules_the_seeded_guess():
    limiter = RateLimiter("search", min_interval=2.0, policy=OBSERVED_SEARCH_POLICY)
    limiter.update_from_headers({"X-Rate-Limit-Ip": "10:100:60"})

    assert [(r.max_hits, r.period) for r in limiter.rules] == [(10, 100.0)]


def test_requests_inside_the_burst_allowance_cost_nothing():
    limiter = RateLimiter("search", policy=OBSERVED_SEARCH_POLICY)
    assert limiter.seconds_for(5) == 0.0


def test_the_estimate_charges_the_rule_that_binds_not_the_slowest_one():
    """`count * sustained_interval` charged every request at the six-hour rate.

    Ninety searches sit far below the six-hundred that rule allows, so it does
    not bind at all; the five-minute rule does. The old arithmetic made twelve
    minutes look like fifty-four.
    """
    limiter = RateLimiter("search", policy=OBSERVED_SEARCH_POLICY)

    binding = limiter.seconds_for(90)

    assert binding == (90 - 30) / 30 * 300
    assert binding < 90 * limiter.sustained_interval()


def test_fetches_are_costed_on_their_own_policy():
    """Fifty per five minutes: on the per-map path they are the larger half."""
    fetch = RateLimiter("fetch", policy=OBSERVED_FETCH_POLICY)
    search = RateLimiter("search", policy=OBSERVED_SEARCH_POLICY)

    assert fetch.seconds_for(300) > search.seconds_for(100)


def test_the_plan_prefers_the_pool_for_a_full_tab(tmp_path):
    pricer = _pricer(FakeTradeClient([[api_result("a", 40)]]), tmp_path, budget=200)

    pooled = pricer._plan_for(70, True)
    per_map = pricer._plan_for(70, False)

    assert pooled.seconds < per_map.seconds
    assert pooled.searches == 16
    assert "shared read" in pooled.caption()


def test_an_empty_pool_is_falsey_and_costs_nothing():
    assert len(LivePool()) == 0
    assert LivePool().requests == 0
    assert not select_from_pool(a_map(), (), tolerance_schedule=DEFAULT_TOLERANCE_SCHEDULE)[0]
