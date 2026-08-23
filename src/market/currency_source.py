"""Live divine/chaos rate from the official bulk-exchange endpoint.

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
    session = session or requests.Session()
    body: dict[str, Any] = {
        "query": {"status": {"option": "online"}, "have": ["chaos"], "want": ["divine"]},
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
            want = offer.get("item") or {}
            if have.get("currency") != "chaos" or want.get("currency") != "divine":
                continue
            if not want.get("amount"):
                continue
            rates.append(float(have["amount"]) / float(want["amount"]))
            break
        if len(rates) >= sample:
            break

    if not rates:
        raise ExchangeRateError("no chaos-for-divine offers returned")
    return float(statistics.median(rates))
