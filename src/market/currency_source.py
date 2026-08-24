"""Live currency rates from the official bulk-exchange endpoint.

Verified live against 3.29 (league "Allflame") on 2026-08-24:
  POST https://www.pathofexile.com/api/trade/exchange/{league}
  body {"query": {"status": {...}, "have": ["chaos"], "want": ["divine"]},
        "sort": {"have": "asc"}, "engine": "new"}

Reading a *price* out of that response is the hard part. The first version took
the median of the ten cheapest offers, and reported both rates roughly ten chaos
under the market. Three separate defects, every one of them biased the same way:

1.  **Stock was ignored**, so a two-unit lowball weighed the same as an
    857-unit standing offer. This is the divine error almost exactly. Measured
    live: the same book reads 219 counting offers equally and 229 weighting by
    the stock behind them, against a market of about 230.

2.  **Bait offers were counted.** A live exalted book held offers at 1, 2, 2.95
    and 3 chaos beside the real cluster at 20-50. They exist to be seen rather
    than filled, and in a ten-offer book four of them move the median from 25 to
    22.5 -- and to 15 an hour later, off the same market. That instability is
    what made the number look random rather than merely wrong.

3.  **`sample=10` truncated the book.** The response arrives already sorted
    ascending by ratio, so slicing the first ten is not a sample of the market,
    it is the floor of it. The live divine book held eleven offers; the slice
    dropped the 240 off the top and never the 199 off the bottom.

What replaces it: the whole book, ratio outliers rejected against the book's own
centre, then a stock-weighted median of what survives. On the captured books
that reads 229 for divine and 25 for exalted.

**`status` stays "online", and that was measured rather than assumed.** Widening
to `"any"` looks like the obvious fix for a ten-offer book and is a trap. Live,
`any` returned 176 exalted offers but the response caps at 100, and because the
book is sorted ascending those 100 are the *cheapest* 100 -- a page running 1 to
10 chaos, median 4, against a real market of 25. `online` returns fewer offers
but returns the whole book, so it is never truncated and never one-sided. The
cost is a thin sample, which is what `RateQuote.thin` exists to report.

The rate is still never permanent -- this is one source, the database keeps a
timestamped history, and Settings lets the user type over it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Sequence

import requests

from src.market.rate_limit import RateLimiter

EXCHANGE_URL = "https://www.pathofexile.com/api/trade/exchange/{league}"
LEAGUES_URL = "https://www.pathofexile.com/api/trade/data/leagues"

#: See the module docstring: `any` returns more offers but the response caps at
#: 100 and the book is sorted cheapest-first, so a deep book comes back as its
#: own bottom quartile. `online` returns a complete book. Complete beats large.
DEFAULT_STATUS = "online"

#: An offer whose ratio is off the book's centre by more than this factor is not
#: a price. Set from live books: real spreads sat inside 1.2x (divine 199-240
#: around a 229 centre) while bait sat 10-20x away (exalted at 1c against a 25c
#: centre). Anything between the two is deliberately left alone -- this is meant
#: to cut obvious bait, not to tighten the market's real spread.
OUTLIER_FACTOR = 4.0

#: Below this many surviving offers the answer is still returned, but the caller
#: is told the book was thin, because that is when this estimate moves on its own.
THIN_BOOK = 5


class ExchangeRateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Offer:
    """One standing offer: `chaos` paid for `amount` of the wanted currency."""

    chaos: float
    amount: float
    stock: int = 0

    @property
    def ratio(self) -> float:
        return self.chaos / self.amount


@dataclass(frozen=True)
class RateQuote:
    """A rate plus enough of its working to judge whether to trust it."""

    currency: str
    league: str
    chaos: float
    offers_seen: int
    offers_used: int
    low: float
    high: float

    def __float__(self) -> float:
        return self.chaos

    @property
    def thin(self) -> bool:
        return self.offers_used < THIN_BOOK

    def summary(self) -> str:
        rejected = self.offers_seen - self.offers_used
        parts = [
            f"1 {self.currency} = {self.chaos:.1f} chaos",
            f"from {self.offers_used} offers spanning {self.low:.1f}-{self.high:.1f}",
        ]
        if rejected:
            parts.append(f"{rejected} rejected as bait")
        if self.thin:
            parts.append("thin book, treat as approximate")
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# The estimator, kept pure so it can be tested without the network.
# ---------------------------------------------------------------------------
def reject_bait(offers: Sequence[Offer], factor: float = OUTLIER_FACTOR) -> list[Offer]:
    """Drop offers whose ratio is nowhere near the rest of the book.

    Anchored on the plain count-median rather than the stock-weighted one on
    purpose: stock is the part of an offer that is free to inflate, and a live
    exalted book carried 1270 units behind a 2-chaos bait against a real centre
    of 25. Weighting by stock before the outliers are gone would let the bait
    pick the centre that decides which offers are bait.
    """
    usable = [o for o in offers if o.amount > 0 and o.chaos > 0]
    if len(usable) < 3:
        return usable
    centre = statistics.median(o.ratio for o in usable)
    if centre <= 0:
        return usable
    kept = [o for o in usable if centre / factor <= o.ratio <= centre * factor]
    return kept or usable


def weighted_median(offers: Sequence[Offer]) -> float:
    """Median ratio, weighting each offer by the stock standing behind it.

    An offer of two units and an offer of 857 are not equally informative about
    what a currency trades at, and treating them alike is what left the divine
    rate reading 219 against a stock-weighted 229.
    """
    if not offers:
        raise ExchangeRateError("no offers to take a rate from")
    ordered = sorted(offers, key=lambda o: o.ratio)
    weights = [max(float(o.stock), 1.0) for o in ordered]
    half = sum(weights) / 2.0
    running = 0.0
    for offer, weight in zip(ordered, weights):
        running += weight
        if running >= half:
            return offer.ratio
    return ordered[-1].ratio


def rate_from_offers(offers: Sequence[Offer], currency: str, league: str) -> RateQuote:
    kept = reject_bait(offers)
    if not kept:
        raise ExchangeRateError(f"no usable chaos-for-{currency} offers")
    ratios = [o.ratio for o in kept]
    return RateQuote(
        currency=currency,
        league=league,
        chaos=weighted_median(kept),
        offers_seen=len(offers),
        offers_used=len(kept),
        low=min(ratios),
        high=max(ratios),
    )


# ---------------------------------------------------------------------------
# The network side.
# ---------------------------------------------------------------------------
def parse_offers(payload: dict[str, Any], want: str) -> list[Offer]:
    """Every chaos-for-`want` offer in an exchange response.

    Every offer on a listing is read rather than just the first: a listing can
    carry several, and stopping at one threw away part of the book for no reason
    beyond how the original loop happened to be written.
    """
    offers: list[Offer] = []
    for entry in (payload.get("result") or {}).values():
        for offer in ((entry or {}).get("listing") or {}).get("offers") or []:
            have = offer.get("exchange") or {}
            offered = offer.get("item") or {}
            if have.get("currency") != "chaos" or offered.get("currency") != want:
                continue
            try:
                chaos = float(have.get("amount"))
                amount = float(offered.get("amount"))
            except (TypeError, ValueError):
                continue
            if chaos <= 0 or amount <= 0:
                continue
            offers.append(
                Offer(chaos=chaos, amount=amount, stock=int(offered.get("stock") or 0))
            )
    return offers


#: Shared so that refreshing both rates in a row counts as two requests against
#: one policy. The old code held no limiter at all, so pressing the two buttons
#: on the Market Data page could earn a five-minute ban between them.
_LIMITER = RateLimiter("trade-exchange", min_interval=6.0)


def fetch_rate_quote(
    league: str,
    user_agent: str,
    want: str,
    session: requests.Session | None = None,
    limiter: RateLimiter | None = None,
    status: str = DEFAULT_STATUS,
) -> RateQuote:
    """The live chaos price of one unit of `want`, with its working shown."""
    session = session or requests.Session()
    limiter = _LIMITER if limiter is None else limiter
    body: dict[str, Any] = {
        "query": {"status": {"option": status}, "have": ["chaos"], "want": [want]},
        "sort": {"have": "asc"},
        "engine": "new",
    }

    limiter.acquire()
    resp = session.post(
        EXCHANGE_URL.format(league=league),
        json=body,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=30,
    )
    limiter.update_from_headers(resp.headers)

    if resp.status_code == 429:
        # The old message said only "rate limited", which left a five-minute ban
        # looking like a broken button. GGG says how long; pass it on.
        retry = resp.headers.get("Retry-After")
        if retry:
            try:
                limiter.note_retry_after(float(retry))
            except ValueError:
                pass
        raise ExchangeRateError(
            "rate limited by GGG on the exchange endpoint"
            + (f"; wait {retry} seconds and try again" if retry else "")
        )
    if resp.status_code == 404:
        raise ExchangeRateError(
            f"league {league!r} is not one GGG is serving -- check the League setting"
        )
    if resp.status_code != 200:
        raise ExchangeRateError(f"exchange endpoint returned HTTP {resp.status_code}")

    offers = parse_offers(resp.json(), want)
    if not offers:
        raise ExchangeRateError(f"no chaos-for-{want} offers returned")
    return rate_from_offers(offers, currency=want, league=league)


def fetch_chaos_rate(
    league: str,
    user_agent: str,
    want: str,
    session: requests.Session | None = None,
    **kwargs: Any,
) -> float:
    """Chaos per unit of `want`, as a plain number."""
    return fetch_rate_quote(league, user_agent, want, session, **kwargs).chaos


def fetch_divine_to_chaos(
    league: str, user_agent: str, session: requests.Session | None = None, **kwargs: Any
) -> float:
    """Chaos per Divine Orb, the rate every stored price is normalised through."""
    return fetch_chaos_rate(league, user_agent, "divine", session, **kwargs)


def fetch_exalted_to_chaos(
    league: str, user_agent: str, session: requests.Session | None = None, **kwargs: Any
) -> float:
    """Chaos per Exalted Orb, the price of the orb an exalt costs.

    Fetched rather than assumed because it is the whole right-hand side of the
    exalt decision: a gain of 30 chaos is worth taking against a 9-chaos orb and
    not against a 40-chaos one, and the ratio moves through a league.
    """
    return fetch_chaos_rate(league, user_agent, "exalted", session, **kwargs)


def fetch_leagues(user_agent: str, session: requests.Session | None = None) -> list[str]:
    """League ids GGG is currently serving, in the order it lists them.

    Used to turn the League setting from a free-text box into a list, so that a
    typo stops being something the user only discovers as an empty result page.
    """
    session = session or requests.Session()
    resp = session.get(
        LEAGUES_URL,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise ExchangeRateError(f"leagues endpoint returned HTTP {resp.status_code}")
    seen: list[str] = []
    for entry in resp.json().get("result") or []:
        # The endpoint lists every realm, so the ids repeat across pc/xbox/sony.
        league_id = entry.get("id")
        if league_id and league_id not in seen:
            seen.append(league_id)
    return seen
