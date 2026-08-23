"""Finding sellers listing many maps at once, for buying rather than modelling.

This is the inverse of the training collector, deliberately and on every axis.
Collection collapses listings by account, caps how many any one seller can
contribute, and never sorts by price -- all three exist to keep bulk dumps from
teaching the model that rewards do not affect price. A buyer wants exactly the
dumps that protects the model from, so this path uncollapses, removes the cap,
and filters on price.

Nothing here is stored. These listings never enter `originator_listings` and
never reach training, which keeps that protection intact: the maps in a bulk lot
are unrolled and priced in a flat block, so they carry no information about what
rewards are worth.

**Sorted newest-first, not cheapest-first.** Cheapest-first sorts you into the
floor of the market, which is picked over within seconds -- a live probe found
the hundred cheapest listings were all 1-2 chaos and already contested. Sorting
by listing time under a price ceiling instead returns what appeared in the last
minute inside a range worth buying, which is the difference between a lot you
can still get and one that is already gone.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from src.market.collectors.official_trade_api import (
    FETCH_BATCH,
    OfficialTradeApiCollector,
    TradeApiError,
)

#: A single trade search returns at most this many ids however many match.
SEARCH_PAGE = 100

#: Default ceiling, in chaos. Everything below this is worth buying on sight, so
#: the scan looks for what is new inside that range rather than what is cheapest.
DEFAULT_PRICE_MAX = 15.0

#: Fewest listings from one seller before it counts as a lot worth a trip.
DEFAULT_MIN_LOT = 5


@dataclass(frozen=True)
class BulkListing:
    listing_id: str
    seller: str
    price_amount: float | None
    price_currency: str | None
    price_chaos: float | None
    indexed_at: datetime | None
    stash_name: str = ""
    x: int | None = None
    y: int | None = None

    def age_minutes(self, now: datetime | None = None) -> float | None:
        if self.indexed_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.indexed_at).total_seconds() / 60.0


@dataclass
class BulkLot:
    """Everything one seller currently has listed inside the scanned range."""

    seller: str
    listings: list[BulkListing] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.listings)

    @property
    def prices(self) -> list[float]:
        return [x.price_chaos for x in self.listings if x.price_chaos is not None]

    @property
    def total_chaos(self) -> float:
        return float(sum(self.prices))

    @property
    def median_chaos(self) -> float | None:
        return float(statistics.median(self.prices)) if self.prices else None

    @property
    def min_chaos(self) -> float | None:
        return min(self.prices) if self.prices else None

    @property
    def max_chaos(self) -> float | None:
        return max(self.prices) if self.prices else None

    @property
    def flat_priced(self) -> bool:
        """A single price across the whole lot -- the signature of a tab dump."""
        return len(set(self.prices)) == 1 and self.count > 1

    def newest_age_minutes(self, now: datetime | None = None) -> float | None:
        ages = [a for a in (x.age_minutes(now) for x in self.listings) if a is not None]
        return min(ages) if ages else None

    def stash_tabs(self) -> list[str]:
        return sorted({x.stash_name for x in self.listings if x.stash_name})


@dataclass
class BulkScan:
    scanned_at: datetime
    lots: list[BulkLot] = field(default_factory=list)
    listings_seen: int = 0
    sellers_seen: int = 0
    total_matching: int = 0
    price_max: float = DEFAULT_PRICE_MAX
    error: str = ""

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.scanned_at).total_seconds()


def scan_bulk_lots(
    collector: OfficialTradeApiCollector,
    price_max: float = DEFAULT_PRICE_MAX,
    min_lot: int = DEFAULT_MIN_LOT,
    seller: str | None = None,
    progress=None,
) -> BulkScan:
    """One pass over what has just been listed below `price_max`.

    `seller` restricts the search to a single account, which is what makes
    re-checking one lot cheap: a targeted query is a single search and one or two
    fetches rather than a sweep of the market.
    """
    started = datetime.now(timezone.utc)
    query = collector.build_query(
        sort_by="indexed",
        sort="desc",
        collapse=False,
        price_max=price_max,
        account=seller,
    )

    try:
        query_id, ids, total = collector.search(query)
    except TradeApiError as exc:
        return BulkScan(scanned_at=started, error=str(exc), price_max=price_max)

    if progress:
        progress(f"{total} listings under {price_max:g}c; reading the newest {len(ids)}")

    listings: list[BulkListing] = []
    for start in range(0, len(ids), FETCH_BATCH):
        batch = ids[start : start + FETCH_BATCH]
        try:
            results = collector.fetch(batch, query_id)
        except TradeApiError as exc:
            if progress:
                progress(f"backing off: {exc}")
            continue
        listings.extend(_to_bulk_listing(r, collector) for r in results if r)
        if progress:
            progress(f"read {len(listings)}/{len(ids)}")

    lots: dict[str, BulkLot] = {}
    for listing in listings:
        if not listing.seller:
            continue
        lots.setdefault(listing.seller, BulkLot(seller=listing.seller)).listings.append(listing)

    kept = [lot for lot in lots.values() if lot.count >= min_lot]
    kept.sort(key=lambda lot: (-(lot.count), lot.median_chaos or 0.0))

    return BulkScan(
        scanned_at=started,
        lots=kept,
        listings_seen=len(listings),
        sellers_seen=len(lots),
        total_matching=total,
        price_max=price_max,
    )


def _to_bulk_listing(result: dict[str, Any], collector) -> BulkListing:
    info = result.get("listing") or {}
    price = info.get("price") or {}
    stash = info.get("stash") or {}
    amount = price.get("amount")
    currency = price.get("currency")
    return BulkListing(
        listing_id=result.get("id") or "",
        seller=(info.get("account") or {}).get("name") or "",
        price_amount=float(amount) if amount is not None else None,
        price_currency=currency,
        price_chaos=collector.converter.to_chaos(amount, currency),
        indexed_at=_parse_indexed(info.get("indexed")),
        stash_name=stash.get("name") or "",
        x=stash.get("x"),
        y=stash.get("y"),
    )


def _parse_indexed(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def summarise(scan: BulkScan, limit: int = 10) -> Sequence[dict[str, Any]]:
    """Rows for display, newest and largest lots first."""
    now = datetime.now(timezone.utc)
    return [
        {
            "Seller": lot.seller,
            "Maps": lot.count,
            "Each": lot.median_chaos,
            "Cheapest": lot.min_chaos,
            "Dearest": lot.max_chaos,
            "Lot total": lot.total_chaos,
            "Flat priced": lot.flat_priced,
            "Newest": lot.newest_age_minutes(now),
            "Tabs": ", ".join(lot.stash_tabs()),
        }
        for lot in scan.lots[:limit]
    ]
