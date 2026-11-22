"""Currency rates from GGG's own Currency Exchange API.

    GET https://web.poecdn.com/api/currency-exchange[/<change-id>]

Why this exists when `currency_source.py` already reads poe.ninja: **this one
reports trades that actually happened.** Every other price in this project is
an *asking* price -- what someone hoped to get -- and that is the central bias
the whole pricing layer has to work around. The Currency Exchange is the
in-game order book, so `volume_traded` is currency that changed hands and the
ratio bounds are the prices it changed hands at.

It is also the most defensible source in the stack on compliance grounds: GGG
documents it as a **Public API** needing no OAuth scope, where poe.ninja's own
API documentation page returns 403 to automated clients and states the API
"exists to run the poe.ninja website, not as a product". poe.ninja's number is
itself an aggregate of this feed, so reading it directly removes a hop and an
unlicensed dependency at once.

Shape, verified live on 2026-11-22:

    {"next_change_id": 1795309200,
     "markets": [{"league": "Covens",
                  "market_id": "<chaos>|<divine>",
                  "market_pair": ["<chaos>", "<divine>"],
                  "volume_traded": {"<chaos>": 13797022, "<divine>": 63631},
                  "lowest_ratio":  {"<chaos>": 200, "<divine>": 1},
                  "highest_ratio": {"<chaos>": 225, "<divine>": 1}, ...}]}

Two things are not obvious and both matter.

**The change id is a Unix timestamp, hour-aligned.** Paging from the start of
the feed would mean walking roughly 20,000 hourly pages to reach today -- the
first page served is July 2024, the Settlers league. But the id can simply be
*computed*: `(now // 3600) * 3600 - 3600` is last hour, and requesting it
returns that hour directly. So the current rate is one request, not a crawl.
GGG's documentation describes the feed as historical and says to wait for the
next hourly boundary, which is true and is why this never asks for the current
(incomplete) hour.

**Ratios are integer pairs, not decimals.** `{chaos: 200, divine: 1}` means 200
chaos to one divine. The rate is therefore `ratio[chaos] / ratio[other]`, and
for a cheap currency it inverts: `{chaos: 1, exalted: 2}` is one chaos for two
exalts, i.e. 0.5 chaos each.
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

import requests

from src.market.currency_source import ExchangeRateError, RateQuote
from src.market.rate_limit import RateLimiter

BASE = "https://web.poecdn.com/api/currency-exchange"

SOURCE_LABEL = "GGG Currency Exchange"

#: GGG's internal item ids for the currencies this project prices in. The
#: names are historical and do not match the orbs: an Exalted Orb adds a
#: modifier to a rare, a Divine Orb rerolls modifier values, a Chaos Orb
#: rerolls a rare outright.
CHAOS = "Metadata/Items/Currency/CurrencyRerollRare"
ITEM_IDS: dict[str, str] = {
    "divine": "Metadata/Items/Currency/CurrencyModValues",
    "exalted": "Metadata/Items/Currency/CurrencyAddModToRare",
}

#: How many whole hours back to try before giving up. A quiet hour in a small
#: league can simply have no trades in a given market, and a league that just
#: started has few hours behind it at all.
MAX_LOOKBACK_HOURS = 6

#: Never ask for the hour in progress: GGG publishes completed digests, so the
#: current hour is either absent or partial.
_SKIP_CURRENT_HOUR = 1

#: Conservative, because GGG publishes no rate-limit headers on this endpoint
#: (verified 2026-11-22: no X-Rate-Limit-* in the response). One request an
#: hour is all the data supports anyway -- the digests do not change in
#: between -- so this only bounds a retry loop.
_LIMITER = RateLimiter(min_interval=2.0)


#: Leagues known to have no Currency Exchange market, and when that was last
#: established. A private league never appears in these digests, and without
#: this every rate fetch for one would walk MAX_LOOKBACK_HOURS hours and spend
#: six requests to learn the same thing again. Re-checked after an hour,
#: because a new league does eventually get its first trade.
_NO_MARKET: dict[str, float] = {}
_NO_MARKET_TTL = 3600.0


def forget_missing_leagues() -> None:
    """Drop the negative cache. For tests, and for a manual retry."""
    _NO_MARKET.clear()


def _recently_missing(league: str, now: float) -> bool:
    seen = _NO_MARKET.get(league)
    if seen is None:
        return False
    if now - seen >= _NO_MARKET_TTL:
        del _NO_MARKET[league]
        return False
    return True


def hour_ids(now: float | None = None, count: int = MAX_LOOKBACK_HOURS) -> list[int]:
    """Completed hour boundaries, most recent first."""
    current = int(now if now is not None else time.time()) // 3600 * 3600
    return [current - (offset + _SKIP_CURRENT_HOUR) * 3600 for offset in range(count)]


def _ratio_to_chaos(entry: Mapping[str, Any], key: str, other: str) -> float | None:
    """Chaos per one unit of `other`, from one ratio block."""
    block = entry.get(key) or {}
    chaos_side = block.get(CHAOS)
    other_side = block.get(other)
    try:
        chaos_side = float(chaos_side)
        other_side = float(other_side)
    except (TypeError, ValueError):
        return None
    if other_side <= 0 or chaos_side <= 0:
        return None
    return chaos_side / other_side


def _volume_weighted(market: Mapping[str, Any], other: str) -> float | None:
    """Chaos per one unit of `other`, from what actually changed hands.

    `volume_traded` reports the totals moved on each side of the market over
    the hour, so dividing them gives the average ratio every executed trade
    settled at, weighted by size. That is a genuine mean of the hour's
    business, and it is the number this module should have been using from the
    start.
    """
    volume = market.get("volume_traded") or {}
    try:
        chaos_side = float(volume.get(CHAOS))
        other_side = float(volume.get(other))
    except (TypeError, ValueError):
        return None
    if chaos_side <= 0 or other_side <= 0:
        return None
    return chaos_side / other_side


def parse_markets(
    markets: Iterable[Mapping[str, Any]], league: str, wanted: Mapping[str, str] | None = None
) -> dict[str, RateQuote]:
    """Every rate this project cares about, out of one hourly digest.

    **Volume-weighted**: the hour's traded chaos divided by the hour's traded
    divine (or exalted), which is the average ratio the hour's trades actually
    settled at.

    This was originally the midpoint of `lowest_ratio` and `highest_ratio`, and
    that was wrong in a way worth recording, because it looked entirely
    reasonable. The two bounds are the single best and single worst trade of
    the hour, so one person executing one silly order moves the estimate by as
    much as the entire rest of the market. Measured across six consecutive
    hours of the Covens chaos/divine market on 2026-11-22:

        hour        midpoint   volume-weighted
        08:00          206.0             209.1
        07:00          125.0             211.0     <- low ratio of 33
        06:00          164.0             211.2     <- low ratio of 111
        05:00          209.5             213.0
        04:00          208.5             212.8
        03:00          207.5             212.0

    The midpoint swings 125-210 while the real market sits flat at 209-213. A
    rate fetched during the 07:00 hour stored 125, which then scaled every
    divine-denominated price in the app by 0.6 for as long as it stood.

    The bounds are still read, as a fallback for a market that reports no usable
    volume, and the quote keeps `volume` so a caller can judge how thin the
    hour was.
    """
    wanted = ITEM_IDS if wanted is None else wanted
    out: dict[str, RateQuote] = {}
    for market in markets:
        if market.get("league") != league:
            continue
        pair = set(market.get("market_pair") or ())
        if CHAOS not in pair:
            continue
        for name, item_id in wanted.items():
            if name in out or item_id not in pair:
                continue
            chaos = _volume_weighted(market, item_id)
            if chaos is None:
                # No usable volume: fall back to the midpoint of the bounds,
                # which is fragile (see the docstring) but is better than
                # having no rate at all for a market that did trade.
                low = _ratio_to_chaos(market, "lowest_ratio", item_id)
                high = _ratio_to_chaos(market, "highest_ratio", item_id)
                usable = [v for v in (low, high) if v is not None]
                if not usable:
                    continue
                chaos = sum(usable) / len(usable)
            # Volume is reported per side; the chaos side is the one that is
            # comparable across currencies, and it is what `thin` judges.
            # Parsed defensively for the same reason every other field here is:
            # this is a third-party payload, and a rate fetch that raises takes
            # the whole page down rather than degrading to a stored rate.
            try:
                volume = float((market.get("volume_traded") or {}).get(CHAOS) or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            out[name] = RateQuote(
                currency=name,
                league=league,
                chaos=chaos,
                volume=volume,
                source=SOURCE_LABEL,
            )
    return out


def fetch_rates(
    league: str,
    user_agent: str,
    session: requests.Session | None = None,
    limiter: RateLimiter | None = None,
    now: float | None = None,
) -> dict[str, RateQuote]:
    """Rates for `league` from the most recent hour that has any.

    Walks back an hour at a time, because a market with no trades in one hour
    simply is not in that hour's digest. Raises rather than returning an empty
    dict when nothing is found, so a caller cannot mistake "no trades" for
    "rate is zero".
    """
    session = session or requests.Session()
    limiter = _LIMITER if limiter is None else limiter
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    clock = time.time() if now is None else now

    if _recently_missing(league, clock):
        raise ExchangeRateError(
            f"league {league!r} had no Currency Exchange market when last checked; "
            "not re-walking the hourly digests yet"
        )

    last_error = "no completed hour held a market for this league"
    for change_id in hour_ids(now):
        limiter.acquire()
        try:
            resp = session.get(f"{BASE}/{change_id}", headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise ExchangeRateError(f"could not reach the Currency Exchange: {exc}") from exc

        limiter.update_from_headers(resp.headers)
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            if retry:
                try:
                    limiter.note_retry_after(float(retry))
                except ValueError:
                    pass
            raise ExchangeRateError("the Currency Exchange is rate-limiting this client")
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code} for hour {change_id}"
            continue

        try:
            payload = resp.json()
        except ValueError as exc:
            last_error = f"unreadable response for hour {change_id}: {exc}"
            continue

        rates = parse_markets(payload.get("markets") or (), league)
        if rates:
            _NO_MARKET.pop(league, None)
            return rates
        last_error = f"league {league!r} had no currency market in hour {change_id}"

    # Every completed hour we were willing to look at was served and none held
    # this league, so the league is not on the Currency Exchange rather than
    # the request having gone wrong. Remember that, so the next rate fetch
    # costs nothing instead of another six requests.
    _NO_MARKET[league] = clock
    raise ExchangeRateError(
        f"{last_error}. The Currency Exchange publishes completed hours only, and "
        f"private or very new leagues may have no trades in one."
    )
