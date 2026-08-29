"""Wiring between collectors, the outlier pass and the database."""
from __future__ import annotations

from typing import Any, Iterable

from src.config import Config, user_agent
from src.database.repository import PRICING_COLUMNS, Repository
from src.market import outliers
from src.market.cancel import CancelToken, is_cancelled
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
    rows = repo.listing_rows(
        league=cfg.get("league"), include_outliers=True, columns=PRICING_COLUMNS
    )
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
    cancel: CancelToken | None = None,
) -> dict[str, Any]:
    """Collect up to `limit` listings, writing as it goes rather than at the end.

    `cancel` is optional and, when given, is the reason the flush structure
    below is the way it is. The generator can stop at any slice boundary, and
    everything after the loop -- the trailing flush and the outlier pass -- is
    written to run on that ending exactly as it runs on a full one. A cancelled
    run keeps what it paid for; that is the only thing that makes cancelling a
    reasonable thing to do rather than a way to throw away twenty minutes of
    rate-limited requests.

    Re-running outlier detection matters just as much as the flush. The flags
    are computed against the whole league's spread, so the rows a cancelled run
    added are unflagged -- and therefore treated as clean -- until the pass runs
    over them. It is a local query with no request cost, so there is no reason
    to skip it just because the run was cut short.
    """
    collector = OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=converter_for(cfg, repo),
        min_interval=cfg.get("http.min_request_interval_seconds", 2.0),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        cancel=cancel,
    )
    ok, why = collector.available()
    if not ok:
        return {"error": why, "inserted": 0, "updated": 0, "cancelled": is_cancelled(cancel)}

    batch: list[MarketListing] = []
    stats: dict[str, Any] = {"inserted": 0, "updated": 0}
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
    # Reported rather than inferred from the count: a cancelled run and a run
    # that simply found little look identical in `inserted`, and only one of
    # them means the sample is missing whole slices of the stratification.
    stats["cancelled"] = is_cancelled(cancel)
    return stats
