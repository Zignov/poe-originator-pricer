"""Live currency rates from the official bulk-exchange endpoint.

Verified live against 3.29 on 2026-08-23:
  POST https://www.pathofexile.com/api/trade/exchange/{league}
  body {"query": {"status": {...}, "have": ["chaos"], "want": ["divine"]},
        "sort": {"have": "asc"}, "engine": "new"}

The rate is never hardcoded permanently: this is one source, the database holds
a timestamped history, and the Settings page lets the user override it by hand.
"""
from __future__ import annotations

import statistics
from typing import Any

import requests

EXCHANGE_URL = "https://www.pathofexile.com/api/trade/exchange/{league}"


class ExchangeRateError(RuntimeError):
    pass


def fetch_divine_to_chaos(
    league: str, user_agent: str, session: requests.Session | None = None, sample: int = 10
) -> float:
    """Median chaos-per-divine across the cheapest live offers."""
    return fetch_chaos_rate(league, user_agent, "divine", session, sample)


def fetch_exalted_to_chaos(
    league: str, user_agent: str, session: requests.Session | None = None, sample: int = 10
) -> float:
    """Median chaos-per-exalted, used to price the orb an exalt costs.

    Fetched rather than assumed because it is the whole right-hand side of the
    exalt decision: a gain of 30 chaos is worth taking against a 9-chaos orb and
    not against a 40-chaos one, and the ratio moves through a league.
    """
    return fetch_chaos_rate(league, user_agent, "exalted", session, sample)


def fetch_chaos_rate(
    league: str,
    user_agent: str,
    want: str,
    session: requests.Session | None = None,
    sample: int = 10,
) -> float:
    """Median chaos per unit of `want`, from the cheapest live offers."""
    session = session or requests.Session()
    body: dict[str, Any] = {
        "query": {"status": {"option": "online"}, "have": ["chaos"], "want": [want]},
        "sort": {"have": "asc"},
        "engine": "new",
    }
    resp = session.post(
        EXCHANGE_URL.format(league=league),
        json=body,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code == 429:
        raise ExchangeRateError("rate limited by GGG on the exchange endpoint")
    if resp.status_code != 200:
        raise ExchangeRateError(f"exchange endpoint returned HTTP {resp.status_code}")

    rates: list[float] = []
    for entry in (resp.json().get("result") or {}).values():
        for offer in (entry.get("listing") or {}).get("offers") or []:
            have = offer.get("exchange") or {}
            offered = offer.get("item") or {}
            if have.get("currency") != "chaos" or offered.get("currency") != want:
                continue
            if not offered.get("amount"):
                continue
            rates.append(float(have["amount"]) / float(offered["amount"]))
            break
        if len(rates) >= sample:
            break

    if not rates:
        raise ExchangeRateError(f"no chaos-for-{want} offers returned")
    return float(statistics.median(rates))
