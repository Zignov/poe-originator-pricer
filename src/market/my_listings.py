"""Your own live instant-buyout listings, read from the trade search.

The trade search takes a "Seller Account" filter, so re-checking everything you
have listed is one search plus a fetch per ten maps -- about twenty requests an
hour on a fifteen-minute cadence, against a sustained policy of thirty searches
per five minutes.

The query itself has a precedent: `src/market/bulk.py` already issues an
uncollapsed, account-scoped, newest-first search, for the same reason ("a
targeted query is a single search and one or two fetches rather than a sweep of
the market"). The difference is what happens to the results. Bulk parses them
into a thin `BulkListing` for buying and stores nothing; the tracker needs the
full `NormalizedMap`, because the reward features are what the fingerprint and
the model's estimate are both built from.

Two parameters are not preferences:

`collapse` is off. It means "collapse listings by account", so an
account-filtered search with it on plausibly returns a single row -- and every
other map you own would look sold. In this codebase "off" means the key is
omitted rather than sent as false: `build_query` emits it only when on,
`bulk.py` depends on that, and `test_live_search_link` pins the convention.

`status` is "any". With "online", logging out is indistinguishable from selling
the whole tab.

The fetch result also carries `listing.stash` -- name, x and y -- so a tracked
listing knows where the map physically sits without a second stash read. It
rides along in `MarketListing.raw_data`, which `listing_from_result` already
fills with the whole listing block.

And one rule matters more than any of them: **a snapshot that cannot be
enumerated is not a snapshot.** A search returns at most 100 ids however many
match, so past that a map that sold and a map that fell outside the window look
the same. The snapshot says so, and the reconciler declines to mark anything
sold from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.market.bulk import SEARCH_PAGE
from src.market.collectors.official_trade_api import (
    FETCH_BATCH,
    OfficialTradeApiCollector,
)
from src.schema import MarketListing


@dataclass
class MyListingsSnapshot:
    listings: list[MarketListing] = field(default_factory=list)
    total_reported: int = 0
    complete: bool = False
    error: str = ""


def fetch_my_listings(
    collector: OfficialTradeApiCollector,
    account: str,
    max_ids: int = SEARCH_PAGE,
) -> MyListingsSnapshot:
    """Every Originator Map `account` currently has listed for buyout."""
    if not account:
        raise ValueError("account name is required to read your own listings")

    query = collector.build_query(
        sort_by="indexed",
        sort="desc",
        status="any",
        collapse=False,
        account=account,
    )
    query_id, ids, total = collector.search(query)

    listings: list[MarketListing] = []
    for start in range(0, min(len(ids), max_ids), FETCH_BATCH):
        batch = ids[start : start + FETCH_BATCH]
        for result in collector.fetch(batch, query_id):
            listing = collector.listing_from_result(result)
            if listing is not None:
                listings.append(listing)

    return MyListingsSnapshot(
        listings=listings,
        total_reported=total,
        complete=total <= max_ids and len(ids) >= min(total, max_ids),
    )
