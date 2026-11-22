"""Wiring between collectors, the outlier pass and the database."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
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


def restate_prices(cfg: Config, repo: Repository, divine_to_chaos: float) -> dict[str, Any]:
    """Re-derive every stored chaos price at a new rate, then re-flag outliers.

    The two halves belong together and were previously done apart, which is a
    quiet way to poison the training set.

    `price_chaos` for a divine-denominated listing is `amount * rate`, so
    changing the rate moves that listing's price -- and the outlier decision is
    a judgement *about* the price: whether it sits inside
    `market.plausible_price_chaos`, and whether it is wildly out of line with
    its peers. Restating prices without re-deciding leaves every flag answering
    a question about numbers that no longer exist.

    It is not hypothetical. The divine rate moved 210 -> 125 -> 209 -> 203 in
    one day. A listing at 35 divine was 4,375c under the bad rate and 7,113c
    under the good one; the 1,000c plausibility ceiling sits between those, and
    because nothing re-flagged it the row stayed marked clean and went into
    training -- a map with atlas 52, rarity 35 and pack 20, priced at 35 divine,
    teaching the model that weak rolls can be worth a fortune.

    Every caller that changes the rate should use this instead of calling
    `Repository.recompute_price_chaos` directly.
    """
    repo.recompute_price_chaos(divine_to_chaos)
    return refresh_outliers(cfg, repo)


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
    # Skip fetching anything already stored and seen inside this window. See
    # Repository.listing_ids_seen_since for why it is a window rather than
    # "already stored": a markdown is real information and has to get in
    # eventually. Set the window to 0 to fetch everything, as older releases did.
    refresh_hours = float(cfg.get("market.refetch_after_hours", 12) or 0)

    def skip_ids(ids):
        if refresh_hours <= 0:
            return set()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=refresh_hours)
        return repo.listing_ids_seen_since(ids, cutoff)

    collector = OfficialTradeApiCollector(
        league=cfg.get("league"),
        user_agent=user_agent(cfg),
        converter=converter_for(cfg, repo),
        min_interval=cfg.get("http.min_request_interval_seconds", 2.0),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        cancel=cancel,
        skip_ids=skip_ids,
    )
    ok, why = collector.available()
    if not ok:
        return {"error": why, "inserted": 0, "updated": 0, "cancelled": is_cancelled(cancel)}

    batch: list[MarketListing] = []
    stats: dict[str, Any] = {"inserted": 0, "updated": 0}
    #: Reward properties the parser did not recognise, counted across the run.
    #:
    #: This is the whole early-warning mechanism, and it exists because of what
    #: the Covens rename cost: `Item Quantity` became `Atlas Drops` with a new
    #: type id, the parser dropped it silently, and every map collected for a
    #: league read quantity 0. Nothing failed, nothing was logged, and the only
    #: symptom was prices sagging on exactly the maps worth the most. A name
    #: this run could not place is now counted and reported at the end.
    unknown_rewards: Counter[str] = Counter()
    for listing in collector.collect(limit=limit, progress=progress):
        unknown_rewards.update(listing.item.unknown_rewards)
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
    # Carried up so a caller that paces itself does not have to reach into the
    # collector. `fresh_ratio` is measured on search results, so it reports how
    # much the market moved without a fetch having been spent to find out.
    stats.update(
        {k: v for k, v in collector.last_walk.items() if k != "cancelled"}
    )
    # Reported rather than inferred from the count: a cancelled run and a run
    # that simply found little look identical in `inserted`, and only one of
    # them means the sample is missing whole slices of the stratification.
    stats["cancelled"] = is_cancelled(cancel)
    if unknown_rewards:
        stats["unknown_rewards"] = dict(unknown_rewards.most_common())
        if progress:
            names = ", ".join(
                f"{name} (on {count})" for name, count in unknown_rewards.most_common(6)
            )
            progress(
                f"WARNING: {len(unknown_rewards)} reward property name(s) this build "
                f"does not recognise: {names}. Those rewards were read as 0. "
                "This is what a league rename looks like -- add them to "
                "PROPERTY_TO_FEATURE in src/schema.py before training on this data."
            )
    return stats
