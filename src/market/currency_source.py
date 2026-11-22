"""Currency rates from poe.ninja.

Verified live against league "Allflame" on 2026-08-24:
  GET https://poe.ninja/poe1/api/economy/exchange/current/overview
      ?league={league}&type=Currency

One request returns every currency, each as `primaryValue` (its price in chaos)
and `volumePrimaryValue` (chaos-equivalent traded per hour). Cross-checked
against what poe.ninja's own page displays: Divine Orb read `primaryValue`
210.9 and volume 9668224 against a rendered "211" and "9.7M".

Why this replaces reading GGG's exchange endpoint directly
----------------------------------------------------------
The previous version took GGG's live order book and tried to reduce it to a
price. That is the wrong shape of data for the question. A book is a snapshot of
what a few dozen people are asking right now; a rate is what the currency
actually changes hands at. Reading the first from the second requires guessing
which offers are real, and the guess was wrong in the direction that mattered.

Concretely, on the live exalted book:

    1, 2, 2.95, 3, 20, 25, 50, 50, 90, 150   (chaos per Exalted Orb)

the old estimator threw away the 1-3 cluster as bait and kept 20-90, returning
25. poe.ninja puts the Exalted Orb at **1.25 chaos** against 47k volume an hour.
The cluster that got discarded *was* the market, and the handful of 20-150 chaos
listings were the noise. Exalted Orbs trade in the tens of thousands per hour
in PoE 1; a currency that plentiful does not sell for 25 chaos, and no statistic
applied to ten listings was ever going to establish that. The fix was never a
better estimator over a worse source.

poe.ninja aggregates the in-game Currency Exchange over a window, so its number
is both smooth and backed by volume this module can report. It is one source
like any other -- the database keeps a timestamped history and Settings lets the
rate be typed over by hand.

The stash overview
------------------
poe.ninja also publishes `stash/current/currency/overview`, the older
pay/receive shape built from *trade site listings* rather than the exchange. It
disagrees, and not slightly: divine 189.2 against 210.9, exalted 3.02 against
1.25. Listings are what people ask; the exchange is what clears. `EXCHANGE` is
the default for that reason, and `STASH` stays reachable for comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import requests

from src.market.rate_limit import RateLimiter

POE_NINJA_BASE = "https://poe.ninja/poe1/api/economy"

#: Aggregated from the in-game Currency Exchange. What the currency clears at.
EXCHANGE = f"{POE_NINJA_BASE}/exchange/current/overview"

#: Built from trade-site listings. What people ask, which is a different number.
STASH = f"{POE_NINJA_BASE}/stash/current/currency/overview"

GGG_LEAGUES_URL = "https://www.pathofexile.com/api/trade/data/leagues"

#: poe.ninja's own ids, which are not the trade API's currency names -- these
#: are bare ("divine"), where GGG's exchange endpoint uses the same strings but
#: the two are only incidentally alike. Verified against the live payload.
CURRENCY_IDS = {"divine": "divine", "exalted": "exalted", "chaos": "chaos"}

#: Chaos-equivalent traded per hour below which a rate is worth doubting. Not a
#: gate -- the rate is still returned, and the caller is told. For scale, the
#: live Divine Orb turns over 9.7M an hour and the Exalted Orb 47k, so this only
#: fires on genuinely illiquid things.
THIN_VOLUME = 1000.0


class ExchangeRateError(RuntimeError):
    pass


@dataclass(frozen=True)
class RateQuote:
    """A rate plus enough context to judge whether to trust it."""

    currency: str
    league: str
    chaos: float
    volume: float = 0.0
    source: str = "poe.ninja exchange"

    def __float__(self) -> float:
        return self.chaos

    @property
    def thin(self) -> bool:
        return self.volume < THIN_VOLUME

    def summary(self) -> str:
        parts = [f"1 {self.currency} = {self.chaos:g} chaos", self.source]
        if self.volume:
            parts.append(f"{_compact(self.volume)} chaos/hour traded")
        if self.thin:
            parts.append("low volume, treat as approximate")
        return "; ".join(parts)


def _compact(value: float) -> str:
    """9668224 -> '9.7M', the way poe.ninja's own page writes it."""
    for cutoff, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= cutoff:
            return f"{value / cutoff:.1f}{suffix}"
    return f"{value:.0f}"


# ---------------------------------------------------------------------------
# Reading a payload, kept pure so it can be tested without the network.
# ---------------------------------------------------------------------------
def parse_overview(payload: Mapping[str, Any], league: str, source: str = EXCHANGE) -> dict[str, RateQuote]:
    """Every currency in an overview response, keyed by poe.ninja's id.

    Handles both shapes: the exchange overview keys a line by `id` and prices it
    with `primaryValue`, while the stash overview keys by `currencyTypeName` and
    prices with `chaosEquivalent`.
    """
    label = "poe.ninja exchange" if source == EXCHANGE else "poe.ninja stash"
    out: dict[str, RateQuote] = {}
    for line in payload.get("lines") or []:
        if not isinstance(line, dict):
            continue
        key = line.get("id") or _stash_id(line.get("currencyTypeName"))
        raw = line.get("primaryValue")
        if raw is None:
            raw = line.get("chaosEquivalent")
        if not key or raw is None:
            continue
        try:
            chaos = float(raw)
        except (TypeError, ValueError):
            continue
        if chaos <= 0:
            continue
        out[key] = RateQuote(
            currency=key,
            league=league,
            chaos=chaos,
            volume=float(line.get("volumePrimaryValue") or 0.0),
            source=label,
        )
    return out


def _stash_id(name: str | None) -> str:
    """"Divine Orb" -> "divine", so both payload shapes key the same way."""
    if not name:
        return ""
    return name.replace(" Orb", "").strip().lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# The network side.
# ---------------------------------------------------------------------------
#: poe.ninja is a community service running on donations. One request returns
#: every currency, so a refresh needs exactly one, and this keeps a button held
#: down from turning into a burst.
_LIMITER = RateLimiter("poe-ninja", min_interval=2.0)


def fetch_rates(
    league: str,
    user_agent: str,
    session: requests.Session | None = None,
    source: str = EXCHANGE,
    limiter: RateLimiter | None = None,
) -> dict[str, RateQuote]:
    """Every currency rate for a league, in one request.

    One call rather than one per currency: the old code asked GGG separately for
    divine and for exalted, which was two requests for two numbers that arrive
    together here.
    """
    session = session or requests.Session()
    limiter = _LIMITER if limiter is None else limiter
    limiter.acquire()
    try:
        resp = session.get(
            source,
            params={"league": league, "type": "Currency"},
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ExchangeRateError(f"could not reach poe.ninja: {exc}") from exc

    limiter.update_from_headers(resp.headers)
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        if retry:
            try:
                limiter.note_retry_after(float(retry))
            except ValueError:
                pass
        raise ExchangeRateError(
            "rate limited by poe.ninja"
            + (f"; wait {retry} seconds and try again" if retry else "")
        )
    if resp.status_code != 200:
        raise ExchangeRateError(f"poe.ninja returned HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ExchangeRateError(f"poe.ninja returned something that is not JSON: {exc}") from exc

    rates = parse_overview(payload, league, source)
    if not rates:
        # An unknown league is *not* an error to poe.ninja: it answers 200 with
        # an empty line list. Left alone that reads as "no rate today" rather
        # than "that league does not exist", which is the failure that hides
        # longest -- so it is named here.
        raise ExchangeRateError(
            f"poe.ninja returned no currency data for league {league!r}. "
            "Check the League setting -- an unrecognised league answers with an "
            "empty result rather than an error."
        )
    return rates


def fetch_rate_quote(
    league: str,
    user_agent: str,
    want: str,
    session: requests.Session | None = None,
    **kwargs: Any,
) -> RateQuote:
    """The chaos price of one currency, with its volume and source attached.

    GGG's own Currency Exchange is tried first and poe.ninja is the fallback.
    Both answer the same question and agree closely -- 210.5 against 213.3
    chaos per divine when this was wired up -- but only one of them is an
    officially documented public API reporting trades that actually executed,
    and poe.ninja is itself an aggregate of it. The fallback stays because
    poe.ninja covers currencies and private leagues the hourly digests may not,
    and because a single upstream for the number every stored price is
    normalised through is a bad idea whoever that upstream is.
    """
    quote = _official_quote(league, user_agent, want, session, **kwargs)
    if quote is not None:
        return quote

    rates = fetch_rates(league, user_agent, session, **kwargs)
    key = CURRENCY_IDS.get(want, want)
    quote = rates.get(key)
    if quote is None:
        raise ExchangeRateError(
            f"neither GGG's Currency Exchange nor poe.ninja has a {want!r} rate "
            f"for {league!r} (poe.ninja priced {len(rates)} other currencies)"
        )
    return quote


def _official_quote(
    league: str,
    user_agent: str,
    want: str,
    session: requests.Session | None = None,
    **kwargs: Any,
) -> RateQuote | None:
    """GGG's rate for one currency, or None to fall through to poe.ninja.

    Imported inside the function because `currency_exchange` imports
    `RateQuote` and `ExchangeRateError` from this module, and a module-level
    import either way round would be circular.

    Returns None on every failure rather than raising. A rate this project
    cannot get from GGG is not an error while a second source exists, and the
    caller's own error is better than one from a source it did not ask for.
    """
    if want not in _OFFICIAL_CURRENCIES:
        return None
    try:
        from src.market.currency_exchange import fetch_rates as fetch_official

        rates = fetch_official(
            league, user_agent, session, limiter=kwargs.get("limiter")
        )
    except Exception:
        return None
    return rates.get(want)


#: What the Currency Exchange digests are read for. Deliberately only the two
#: orbs this project needs: the digests carry ~1,900 markets an hour and
#: parsing all of them to answer one question would be wasteful.
_OFFICIAL_CURRENCIES = frozenset({"divine", "exalted"})


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
    exalt decision, and because it is small: at 1.25 chaos an exalt is close to
    free against a map worth 100, so a stale value here quietly turns the
    exalt-worthy verdict into a rubber stamp or a veto.
    """
    return fetch_chaos_rate(league, user_agent, "exalted", session, **kwargs)


def fetch_leagues(user_agent: str, session: requests.Session | None = None) -> list[str]:
    """League ids GGG is currently serving, in the order it lists them.

    Still GGG rather than poe.ninja: this drives the League setting, which also
    governs stash reads and market collection, so it should come from the
    authority on which leagues exist.
    """
    session = session or requests.Session()
    resp = session.get(
        GGG_LEAGUES_URL,
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
