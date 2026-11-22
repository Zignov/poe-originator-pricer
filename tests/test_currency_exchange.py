"""GGG's Currency Exchange: the one source in this project reporting real trades.

Every other number here is an asking price. These digests are the in-game order
book, so the ratios are prices things actually sold at -- which is why this is
preferred over poe.ninja for the divine rate that every stored price is
normalised through.

The payload shapes below are the live 2026-11-22 response, trimmed.
"""
from __future__ import annotations

import pytest

from src.market.currency_exchange import (
    CHAOS,
    ITEM_IDS,
    hour_ids,
    parse_markets,
)
from src.market.currency_source import ExchangeRateError

DIVINE = ITEM_IDS["divine"]
EXALTED = ITEM_IDS["exalted"]


def _market(league, other, low, high, chaos_volume=0, other_volume=0):
    """One market as GGG serves it: ratios are integer pairs, not decimals.

    Volume defaults to zero so these tests exercise the **ratio** path, which
    is what they were written for and which is still the fallback. The live
    rate is volume-weighted -- see tests/test_exchange_rate_estimator.py for
    why, and for the hour where the midpoint of the bounds read 125 against a
    market trading at 211.
    """
    return {
        "league": league,
        "market_id": f"{CHAOS}|{other}",
        "market_pair": [CHAOS, other],
        "volume_traded": {CHAOS: chaos_volume, other: other_volume},
        "lowest_stock": {CHAOS: 1, other: 1},
        "highest_stock": {CHAOS: 1, other: 1},
        "lowest_ratio": {CHAOS: low[0], other: low[1]},
        "highest_ratio": {CHAOS: high[0], other: high[1]},
    }


# -- reading the ratios -----------------------------------------------------
def test_a_ratio_pair_becomes_chaos_per_unit():
    """{chaos: 200, divine: 1} is 200 chaos per divine, and the hour's midpoint."""
    rates = parse_markets([_market("Covens", DIVINE, (200, 1), (225, 1))], "Covens")
    assert rates["divine"].chaos == pytest.approx(212.5)
    assert rates["divine"].league == "Covens"
    assert "GGG" in rates["divine"].source


def test_a_cheap_currency_inverts_correctly():
    """{chaos: 1, exalted: 2} is one chaos for two exalts -- 0.5 chaos each.

    The direction is the trap: reading the pair the other way up would price an
    Exalted Orb at two chaos, which is the whole right-hand side of the exalt
    decision wrong by 4x.
    """
    rates = parse_markets([_market("Covens", EXALTED, (1, 2), (1, 1))], "Covens")
    assert rates["exalted"].chaos == pytest.approx(0.75)


def test_volume_comes_from_the_chaos_side():
    """Comparable across currencies, which is what the thinness check needs."""
    rates = parse_markets(
        [_market("Covens", DIVINE, (200, 1), (200, 1), chaos_volume=13_797_022)], "Covens"
    )
    assert rates["divine"].volume == pytest.approx(13_797_022)


def test_only_the_asked_for_league_is_read():
    """~1,900 markets an hour arrive across every league including private ones."""
    markets = [
        _market("Standard", DIVINE, (150, 1), (150, 1)),
        _market("Covens", DIVINE, (200, 1), (220, 1)),
        _market("Hardcore Covens", DIVINE, (300, 1), (300, 1)),
        _market("Some Private League (PL49303)", DIVINE, (999, 1), (999, 1)),
    ]
    rates = parse_markets(markets, "Covens")
    assert rates["divine"].chaos == pytest.approx(210.0)


def test_markets_that_are_not_priced_in_chaos_are_ignored():
    other = "Metadata/Items/Currency/CurrencyVaal"
    market = {
        "league": "Covens",
        "market_pair": [other, DIVINE],
        "volume_traded": {},
        "lowest_ratio": {other: 1, DIVINE: 1},
        "highest_ratio": {other: 1, DIVINE: 1},
    }
    assert parse_markets([market], "Covens") == {}


@pytest.mark.parametrize(
    "low,high",
    [((0, 1), (0, 1)), ((200, 0), (200, 0)), ((None, 1), (None, 1)), (("x", 1), ("x", 1))],
)
def test_unusable_ratios_fall_back_to_the_volume_rather_than_to_zero(low, high):
    """A zero rate would divide into every price on the page.

    The concern this was written for is unchanged; what satisfies it moved.
    The rate is now volume-weighted -- traded chaos over traded divine -- so a
    market whose *bounds* are unreadable still has the thing that actually
    matters, and only a market with neither is skipped. See
    tests/test_exchange_rate_estimator.py for why the bounds stopped being the
    primary source.
    """
    market = _market("Covens", DIVINE, low, high, chaos_volume=1_000_000, other_volume=5_000)
    quotes = parse_markets([market], "Covens")
    assert quotes["divine"].chaos == pytest.approx(200.0)


@pytest.mark.parametrize(
    "low,high",
    [((0, 1), (0, 1)), ((200, 0), (200, 0)), ((None, 1), (None, 1)), (("x", 1), ("x", 1))],
)
def test_a_market_with_neither_volume_nor_ratios_is_skipped(low, high):
    """Skipped, not defaulted -- a fabricated rate is worse than a missing one."""
    assert parse_markets([_market("Covens", DIVINE, low, high)], "Covens") == {}


def test_one_usable_bound_is_still_a_rate():
    """A market that only moved at one price still traded at that price."""
    market = _market("Covens", DIVINE, (200, 1), (0, 1))
    assert parse_markets([market], "Covens")["divine"].chaos == pytest.approx(200.0)


# -- picking which hour to ask for ------------------------------------------
def test_the_change_id_is_an_hour_aligned_timestamp():
    """The finding that makes this API usable at all.

    Paging from the start of the feed would be ~20,000 requests to reach today,
    because the first page served is July 2024. The id is simply a Unix
    timestamp, so the hour wanted can be computed and asked for directly.
    """
    ids = hour_ids(now=1795312800 + 125)  # mid-hour, deliberately not aligned
    assert all(i % 3600 == 0 for i in ids), "every id must be on an hour boundary"
    assert ids == sorted(ids, reverse=True), "most recent first"
    assert len(set(ids)) == len(ids)


def test_the_hour_in_progress_is_never_requested():
    """GGG publishes completed digests; the live hour is absent or partial."""
    now = 1795312800  # exactly on a boundary, the worst case for an off-by-one
    assert max(hour_ids(now=now)) <= now - 3600


def test_consecutive_ids_are_one_hour_apart():
    ids = hour_ids(now=1795312800, count=4)
    assert [a - b for a, b in zip(ids, ids[1:])] == [3600, 3600, 3600]


# -- failure behaviour ------------------------------------------------------
def test_no_trades_raises_rather_than_returning_nothing(monkeypatch):
    """"No market this hour" must not be read as "the rate is zero"."""
    import src.market.currency_exchange as mod

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {"markets": [_market("Standard", DIVINE, (150, 1), (150, 1))]}

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    with pytest.raises(ExchangeRateError, match="no currency market|completed hours"):
        mod.fetch_rates("Covens", "ua", session=_Session(), limiter=_NullLimiter())


def test_a_league_with_no_market_is_not_walked_again_and_again(monkeypatch):
    """A private league is never in these digests, and six requests say so.

    Without the negative cache every rate fetch for such a league would spend
    MAX_LOOKBACK_HOURS requests re-learning it -- on a shared per-IP budget
    that the user's own browser trade tab is also drawing from.
    """
    import src.market.currency_exchange as mod

    mod.forget_missing_leagues()

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {"markets": [_market("Standard", DIVINE, (150, 1), (150, 1))]}

    class _Session:
        def __init__(self):
            self.calls = 0

        def get(self, *a, **k):
            self.calls += 1
            return _Resp()

    session = _Session()
    now = 1795312800
    with pytest.raises(ExchangeRateError):
        mod.fetch_rates("Private (PL1)", "ua", session=session, limiter=_NullLimiter(), now=now)
    first = session.calls
    assert first == mod.MAX_LOOKBACK_HOURS, "the first attempt should try every hour"

    with pytest.raises(ExchangeRateError, match="last checked"):
        mod.fetch_rates("Private (PL1)", "ua", session=session, limiter=_NullLimiter(), now=now + 60)
    assert session.calls == first, "the second attempt must spend no requests at all"

    # ...but not forever: a new league does eventually get its first trade.
    with pytest.raises(ExchangeRateError):
        mod.fetch_rates(
            "Private (PL1)", "ua", session=session, limiter=_NullLimiter(), now=now + 3601
        )
    assert session.calls > first, "after the TTL it must look again"
    mod.forget_missing_leagues()


def test_the_poe_ninja_fallback_is_used_when_ggg_cannot_answer(monkeypatch):
    """A failure at GGG must fall through quietly, not break the rate."""
    import src.market.currency_source as source

    def _boom(*a, **k):
        raise RuntimeError("Currency Exchange unreachable")

    monkeypatch.setattr("src.market.currency_exchange.fetch_rates", _boom)
    assert source._official_quote("Covens", "ua", "divine") is None


def test_currencies_outside_the_two_orbs_skip_ggg_entirely():
    """The digests carry ~1,900 markets; parsing them for chaos itself is waste."""
    import src.market.currency_source as source

    assert source._official_quote("Covens", "ua", "chaos") is None


class _NullLimiter:
    def acquire(self):
        pass

    def update_from_headers(self, headers):
        pass

    def note_retry_after(self, seconds):
        pass
