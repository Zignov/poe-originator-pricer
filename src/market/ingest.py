"""Wiring between collectors, the outlier pass and the database."""
from __future__ import annotations

from typing import Any, Iterable

from src.config import Config, user_agent
from src.database.repository import Repository
from src.market import outliers
from src.market.collectors.base import dedupe
from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.pricing.currency import CurrencyConverter
from src.schema import MarketListing


def converter_for(cfg: Config, repo: Repository) -> CurrencyConverter:
    stored = repo.get_divine_rate(cfg.get("league"))
    if stored:
        return CurrencyConverter(stored[0], stored[1])
    return CurrencyConverter(cfg.get("currency.divine_to_chaos"))


def store(cfg: Config, repo: Repository, listings: Iterable[MarketListing]) -> dict[str, Any]:
    """Persist listings, then re-run outlier flagging over the whole league."""
    result = repo.upsert_listings(dedupe(listings))
    result.update(refresh_outliers(cfg, repo))
    return result


def refresh_outliers(cfg: Config, repo: Repository) -> dict[str, Any]:
    rows = repo.listing_rows(league=cfg.get("league"), include_outliers=True)
    bounds = cfg.get("market.plausible_price_chaos", [5, 1000])
    decisions = outliers.evaluate(
        rows, max_age_days=None, plausible_chaos=(float(bounds[0]), float(bounds[1]))
    )
    repo.flag_outliers(decisions)
    return {"flagged": sum(1 for _, flag, _ in decisions if flag), "evaluated": len(decisions)}


def collect_from_trade_api(
    cfg: Config,
    repo: Repository,
    limit: int = 500,
    progress=None,
) -> dict[str, Any]:
    collector = OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=converter_for(cfg, repo),
        min_interval=cfg.get("http.min_request_interval_seconds", 2.0),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    ok, why = collector.available()
    if not ok:
        return {"error": why, "inserted": 0, "updated": 0}

    batch: list[MarketListing] = []
    stats = {"inserted": 0, "updated": 0}
    for listing in collector.collect(limit=limit, progress=progress):
        batch.append(listing)
        if len(batch) >= 50:
            partial = repo.upsert_listings(dedupe(batch))
            stats["inserted"] += partial["inserted"]
            stats["updated"] += partial["updated"]
            batch.clear()
    if batch:
        partial = repo.upsert_listings(dedupe(batch))
        stats["inserted"] += partial["inserted"]
        stats["updated"] += partial["updated"]

    stats.update(refresh_outliers(cfg, repo))
    return stats
