"""Collector for the official pathofexile.com trade search API.

Verified live against 3.29 (league "Allflame") on 2026-08-23:
  POST https://www.pathofexile.com/api/trade/search/{league}
  GET  https://www.pathofexile.com/api/trade/fetch/{ids}?query={id}
Both respond without authentication. See docs/data-access.md for what was
checked and what remains uncertain.

This client:
  * identifies itself with a contact address in the User-Agent, as GGG asks;
  * obeys the rate-limit policy the server reports in response headers;
  * backs off on 429 instead of retrying immediately;
  * never attempts to bypass Cloudflare, authentication, or a rate limit.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Sequence

import requests

from src.market.cancel import CancelToken, is_cancelled
from src.market.collectors.base import MarketCollector, ProgressUpdate
from src.market.rate_limit import (
    OBSERVED_FETCH_POLICY,
    OBSERVED_SEARCH_POLICY,
    RateLimiter,
)
from src.parsing.item_parser import classify, parse_api_item
from src.parsing.price_parser import parse_api_price
from src.pricing.currency import CurrencyConverter
from src.schema import ORIGINATOR_IMPLICIT_STAT_ID, MarketListing

TRADE_BASE = "https://www.pathofexile.com/api/trade"
FETCH_BATCH = 10  # the endpoint accepts at most 10 ids per call


class TradeApiError(RuntimeError):
    pass


class CollectionCancelled(RuntimeError):
    """Raised in place of a request that a cancel arrived just in time to stop.

    Deliberately *not* a `TradeApiError`. The error path answers a rate limit or
    a 500 by sleeping and trying the next slice, which is exactly the wrong
    response to a cancel -- it would keep the run alive, one backoff at a time,
    after the user asked it to stop. Being a separate type means the caller has
    to say what a cancel means to it, and `collect()` says "stop the walk".
    """


class OfficialTradeApiCollector(MarketCollector):
    name = "official_trade_api"

    def __init__(
        self,
        league: str,
        user_agent: str,
        converter: CurrencyConverter,
        min_interval: float = 2.0,
        session: requests.Session | None = None,
        map_tier: int = 16,
        require_uncorrupted: bool = True,
        cancel: CancelToken | None = None,
        skip_ids: Callable[[Sequence[str]], set[str]] | None = None,
    ):
        self.league = league
        self.converter = converter
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted
        #: Given a page of search results, which ids are not worth fetching.
        #:
        #: A search hands back up to 100 ids for one request; a fetch costs one
        #: request per ten. So the fetch is ten times dearer per listing, and it
        #: was being spent re-reading rows already in the database -- measured at
        #: 61% of all fetch requests over a four-cycle run. Deciding what to skip
        #: needs the database, which this module must not know about, so the
        #: caller supplies it. `None` fetches everything, which is what the
        #: short-lived callers (bulk buy, live comparables) want.
        self.skip_ids = skip_ids
        #: Optional. Every other caller of this collector -- bulk buy, my
        #: listings, the market-comparison client -- runs short and inline, so
        #: `None` means "not cancellable" and costs those paths nothing.
        self.cancel = cancel
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json"}
        )
        # Seeded with the last policy GGG was seen to publish, so that anything
        # asking what a run will cost gets a real answer before the first
        # request rather than the `min_interval` placeholder. The first response
        # overwrites both with whatever the server actually says.
        #: Facts about the last `collect()` walk, for a caller that wants to
        #: pace itself. Empty until one has run.
        self.last_walk: dict[str, Any] = {}
        self.search_limiter = RateLimiter(
            "trade-search", min_interval=min_interval, policy=OBSERVED_SEARCH_POLICY
        )
        self.fetch_limiter = RateLimiter(
            "trade-fetch", min_interval=min_interval / 2, policy=OBSERVED_FETCH_POLICY
        )

    # -- cancellation ----------------------------------------------------
    def _stop(self) -> bool:
        return is_cancelled(self.cancel)

    def _wait(self, seconds: float) -> None:
        """Sleep, unless there is a cancel to notice instead.

        The two error backoffs on this path are ten and fifteen seconds, and a
        run that has just been rate limited is the run most likely to be
        cancelled -- so those are precisely the sleeps that must not be blind.
        """
        if self.cancel is not None:
            self.cancel.sleep(seconds)
        else:
            time.sleep(seconds)

    def available(self) -> tuple[bool, str]:
        try:
            resp = self.session.get(f"{TRADE_BASE}/data/leagues", timeout=20)
            if resp.status_code != 200:
                return False, f"leagues endpoint returned HTTP {resp.status_code}"
            leagues = {entry["id"] for entry in resp.json().get("result", [])}
            if self.league not in leagues:
                return False, f"league {self.league!r} not listed by GGG"
            return True, ""
        except requests.RequestException as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    def build_query(
        self,
        sort: str = "asc",
        sort_by: str = "price",
        status: str = "any",
        extra_stats: list[dict[str, Any]] | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        indexed: str | None = None,
        sale_type: str | None = None,
        pack_size: tuple[int | None, int | None] | None = None,
        quantity: tuple[int | None, int | None] | None = None,
        map_rarity: tuple[int | None, int | None] | None = None,
        collapse: bool = True,
        account: str | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {
            "map_filters": {
                "filters": {"map_tier": {"min": self.map_tier, "max": self.map_tier}}
            },
            # Rare only, and not negotiable per-slice. A magic map holds one
            # prefix and one suffix against a rare's three of each, so it carries
            # a fraction of the reward values and sells for a fraction of the
            # price -- in a 4,746-row sample, 18c average against a rare market
            # that starts around 30c. Mixed in, they teach the model that low
            # reward totals are normal. Key verified against
            # /api/trade/data/filters on 2026-08-25: type_filters.rarity, with
            # "rare" among the option ids.
            "type_filters": {"filters": {"rarity": {"option": "rare"}}},
        }
        if self.require_uncorrupted:
            filters["misc_filters"] = {"filters": {"corrupted": {"option": "false"}}}
        # map_iir is only ever used by the market-comparison mode, which asks for
        # maps that look like one particular map rather than for a spread of the
        # market. Verified live on 2026-08-25: a query carrying map_iiq, map_iir
        # and map_packsize windows together returned only listings whose printed
        # properties all sat inside them.
        for key, bounds in (
            ("map_packsize", pack_size),
            ("map_iiq", quantity),
            ("map_iir", map_rarity),
        ):
            if not bounds:
                continue
            spec: dict[str, Any] = {}
            if bounds[0] is not None:
                spec["min"] = bounds[0]
            if bounds[1] is not None:
                spec["max"] = bounds[1]
            if spec:
                filters["map_filters"]["filters"][key] = spec
        # trade_filters keys verified against /api/trade/data/filters:
        #   sale_type -> omitted means "Buyout or Fixed Price", which is what we
        #                want. Verified live: passing "priced_with_info" returns
        #                zero results for this query, so it is not used.
        #   price     -> {min, max} with no option is a Chaos Orb *equivalent*
        #                bound, converted by GGG's own rate. Verified live.
        #   indexed   -> 1hour | 3hours | 12hours | 1day | 3days | 1week | 2weeks
        #   collapse  -> "Collapse Listings by Account". ON BY DEFAULT, and it
        #                matters more than anything else here. Sellers routinely
        #                dump a whole tab at one flat price: without collapsing,
        #                a single account contributed 142 maps all listed at
        #                exactly 40 chaos. Inside such a block the price is
        #                constant while the rewards vary wildly, which teaches
        #                the model that the rewards do not affect price. Verified
        #                live: collapsing turned 3 unique accounts in the first
        #                10 results into 10.
        trade: dict[str, Any] = {}
        if collapse:
            trade["collapse"] = {"option": "true"}
        #   account -> "Seller Account", verified against /api/trade/data/filters.
        #              Restricting to one seller turns re-checking a known lot
        #              into a single search instead of a sweep of the market.
        if account:
            trade["account"] = {"input": account}
        if sale_type:
            trade["sale_type"] = {"option": sale_type}
        if indexed:
            trade["indexed"] = {"option": indexed}
        if price_min is not None or price_max is not None:
            price: dict[str, Any] = {}
            if price_min is not None:
                price["min"] = price_min
            if price_max is not None:
                price["max"] = price_max
            trade["price"] = price
        if trade:
            filters["trade_filters"] = {"filters": trade}

        stats: list[dict[str, Any]] = [
            {"type": "and", "filters": [{"id": ORIGINATOR_IMPLICIT_STAT_ID}]}
        ]
        stats.extend(extra_stats or [])
        return {
            "query": {"status": {"option": status}, "filters": filters, "stats": stats},
            "sort": {sort_by: sort},
        }

    def search(self, query: dict[str, Any]) -> tuple[str, list[str], int]:
        self.search_limiter.acquire(sleep=self._wait, should_stop=self._stop)
        # Checked *after* the limiter, not only before it. The limiter is where
        # the minutes go, so the overwhelmingly likely place for a cancel to
        # arrive is while it is waiting -- and a cancel that then let the
        # request it was waiting for go out anyway would be the one request the
        # user explicitly asked not to make.
        if self._stop():
            raise CollectionCancelled("cancelled before the search went out")
        resp = self.session.post(
            f"{TRADE_BASE}/search/{self.league}", json=query, timeout=30
        )
        self.search_limiter.update_from_headers(resp.headers)
        if resp.status_code == 429:
            self.search_limiter.note_retry_after(float(resp.headers.get("Retry-After", 60)))
            raise TradeApiError("rate limited by GGG on search; backing off")
        if resp.status_code != 200:
            raise TradeApiError(f"search returned HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return data["id"], data.get("result", []), data.get("total", 0)

    def fetch(self, ids: list[str], query_id: str) -> list[dict[str, Any]]:
        if not ids:
            return []
        self.fetch_limiter.acquire(sleep=self._wait, should_stop=self._stop)
        if self._stop():
            raise CollectionCancelled("cancelled before the fetch went out")
        url = f"{TRADE_BASE}/fetch/{','.join(ids)}"
        resp = self.session.get(url, params={"query": query_id}, timeout=30)
        self.fetch_limiter.update_from_headers(resp.headers)
        if resp.status_code == 429:
            self.fetch_limiter.note_retry_after(float(resp.headers.get("Retry-After", 60)))
            raise TradeApiError("rate limited by GGG on fetch; backing off")
        if resp.status_code != 200:
            raise TradeApiError(f"fetch returned HTTP {resp.status_code}: {resp.text[:200]}")
        return [r for r in resp.json().get("result", []) if r]

    # ------------------------------------------------------------------
    def collect(
        self,
        limit: int = 500,
        slices: list[dict[str, Any]] | None = None,
        progress=None,
        per_slice: int | None = None,
        max_per_seller: int = 8,
    ) -> Iterator[MarketListing]:
        """Walk the query slices, taking a fair share from each, up to `limit`.

        A single trade search returns at most 100 result ids, so several slices
        are used to reach a larger sample. Two caps keep the sample honest:

        `per_slice`
            Without it the whole budget goes to the first slices and the rarer
            reward combinations never get collected at all.

        `max_per_seller`
            Bulk dumps are the single biggest threat to this dataset. One account
            listing 142 maps at a flat 40 chaos contributes 142 rows in which the
            price never moves while the rewards vary wildly -- which is a direct
            lesson that the rewards do not matter. `collapse` handles most of it
            server-side; this is the backstop, applied across the whole run
            rather than per slice so a whale cannot re-enter through each one.

        A cancel is checked at the top of both loops -- between slices and
        between fetch batches -- because those are the two places the run is
        between requests rather than inside one. It ends the generator normally
        rather than raising through it, so the caller's `for` loop finishes and
        gets to flush what it has: everything read before a cancel is kept, and
        an exception thrown out of a generator mid-iteration is how that would
        quietly stop being true.

        **Ids are filtered before they are fetched**, in two stages, because a
        fetch costs ten times what a search does per listing:

        1. `seen` -- ids already taken this run. Every slice sorts by `indexed
           desc`, so a map that matches two slices is near the top of both and
           was being fetched twice. `dedupe` downstream caught it only when both
           copies landed in the same fifty-row batch.
        2. `skip_ids` -- ids the caller says are not worth re-reading, normally
           "already stored and seen recently".

        Measured over a four-cycle run before this existed: 61% of fetch
        requests returned rows that were already in the database.
        """
        slices = slices or default_slices()
        if per_slice is None:
            per_slice = max(20, -(-limit // max(len(slices), 1)))
        seller_counts: dict[str, int] = {}
        skipped_for_seller = 0
        produced = 0
        #: Ids taken this run, across every slice.
        seen: set[str] = set()
        #: Search results considered, and how many survived the filters. The
        #: ratio is the honest saturation signal -- it says whether the visible
        #: window has turned over since last time, and it costs no fetch to
        #: measure. The passive collector paces itself on it.
        ids_seen = 0
        ids_fresh = 0
        slice_count = len(slices)

        def report(message: str, slice_index: int) -> None:
            if progress:
                progress(
                    ProgressUpdate(
                        message=message,
                        collected=produced,
                        target=limit,
                        slice_index=slice_index,
                        slice_count=slice_count,
                        skipped_as_bulk=skipped_for_seller,
                    )
                )

        stopped = False
        for slice_index, spec in enumerate(slices):
            if produced >= limit or self._stop():
                stopped = stopped or self._stop()
                break
            taken_here = 0
            query = self.build_query(**spec)
            try:
                query_id, ids, total = self.search(query)
            except CollectionCancelled:
                stopped = True
                break
            except TradeApiError as exc:
                report(f"slice skipped: {exc}", slice_index)
                self._wait(10)
                continue

            ids_seen += len(ids)
            # Both filters run before `per_slice` is applied, so the budget is
            # spent on listings worth fetching rather than partly consumed by
            # ones that are about to be discarded.
            ids = [i for i in ids if i not in seen]
            if self.skip_ids is not None and ids:
                try:
                    skip = self.skip_ids(ids)
                except Exception:  # noqa: BLE001 - a filter that fails must not stop a run
                    skip = set()
                ids = [i for i in ids if i not in skip]
            ids_fresh += len(ids)

            take = min(len(ids), per_slice)
            report(
                f"slice {spec} -> {total} listings, {len(ids)} worth fetching, taking {take}",
                slice_index,
            )
            ids = ids[:take]
            seen.update(ids)

            for start in range(0, len(ids), FETCH_BATCH):
                if produced >= limit or taken_here >= per_slice or self._stop():
                    stopped = stopped or self._stop()
                    break
                batch = ids[start : start + FETCH_BATCH]
                try:
                    results = self.fetch(batch, query_id)
                except CollectionCancelled:
                    stopped = True
                    break
                except TradeApiError as exc:
                    report(f"fetch backoff: {exc}", slice_index)
                    self._wait(15)
                    continue
                for result in results:
                    listing = self.listing_from_result(result)
                    if listing is None:
                        continue
                    seller = listing.seller or "<unknown>"
                    if max_per_seller and seller_counts.get(seller, 0) >= max_per_seller:
                        skipped_for_seller += 1
                        continue
                    seller_counts[seller] = seller_counts.get(seller, 0) + 1
                    produced += 1
                    taken_here += 1
                    yield listing
                    if produced >= limit or taken_here >= per_slice:
                        break
                report(
                    f"collected {produced}/{limit}"
                    + (f" ({skipped_for_seller} skipped as bulk)" if skipped_for_seller else ""),
                    slice_index,
                )

        # Left on the collector rather than yielded, because the caller is
        # iterating listings and these are facts about the walk. `fresh_ratio`
        # is what the passive collector paces on: it is measured on search
        # results, so it says whether the market has moved without spending a
        # single fetch to find out.
        self.last_walk = {
            "ids_seen": ids_seen,
            "ids_fresh": ids_fresh,
            "fresh_ratio": (ids_fresh / ids_seen) if ids_seen else 1.0,
            "produced": produced,
            "skipped_as_bulk": skipped_for_seller,
            "cancelled": stopped,
        }

        # A cancelled run says so rather than reporting "finished". The two are
        # the same number of listings and completely different facts about the
        # sample: a finished walk covered every slice, a cancelled one stopped
        # somewhere in the stratification and is biased towards the slices it
        # got to.
        report(
            (f"cancelled: kept {produced} listings" if stopped
             else f"finished: {produced} listings from {slice_count} slices")
            + (f", {skipped_for_seller} skipped as bulk" if skipped_for_seller else "")
            + (f" ({ids_fresh}/{ids_seen} search results were new)" if ids_seen else ""),
            slice_count,
        )

    def listing_from_result(self, result: dict[str, Any]) -> MarketListing | None:
        item = result.get("item") or {}
        listing_info = result.get("listing") or {}
        nm = classify(
            parse_api_item(item),
            map_tier=self.map_tier,
            require_uncorrupted=self.require_uncorrupted,
        )
        if not nm.is_originator:
            return None

        price = parse_api_price(listing_info)
        indexed = listing_info.get("indexed")
        indexed_at = None
        if indexed:
            try:
                indexed_at = datetime.fromisoformat(indexed.replace("Z", "+00:00"))
            except ValueError:
                indexed_at = None

        return MarketListing(
            item=nm,
            listing_id=result.get("id"),
            captured_at=datetime.now(timezone.utc),
            indexed_at=indexed_at,
            league=item.get("league") or self.league,
            price_amount=price["price_amount"],
            price_currency=price["price_currency"],
            price_type=price["price_type"],
            price_chaos=self.converter.to_chaos(
                price["price_amount"], price["price_currency"]
            ),
            seller=(listing_info.get("account") or {}).get("name"),
            source=self.name,
            raw_data={"listing": listing_info, "item": item},
        )


#: Pseudo stat ids for the four "More" rewards, verified against
#: /api/trade/data/stats. These let a query be stratified by *reward*.
REWARD_STATS = {
    "more_currency": "pseudo.pseudo_map_more_currency_drops",
    "more_maps": "pseudo.pseudo_map_more_map_drops",
    "more_scarabs": "pseudo.pseudo_map_more_scarab_drops",
    "more_divination_cards": "pseudo.pseudo_map_more_card_drops",
}


def _stat_range(stat_id: str, low: int | None, high: int | None) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if low is not None:
        value["min"] = low
    if high is not None:
        value["max"] = high
    return {"type": "and", "filters": [{"id": stat_id, "value": value}]}


def default_slices() -> list[dict[str, Any]]:
    """Query slices designed so the sample is not biased in price.

    Two rules, both learned the hard way:

    1. **Never stratify on price.** An earlier version stepped through
       chaos-equivalent price bands and took an equal share from each. That makes
       the sampled price distribution roughly uniform *by construction*, which
       decouples price from the features -- the collected data showed a flat
       ~300c median at every level of every reward, and the model learned
       nothing. Stratifying on the target is sampling on the dependent variable.

    2. **Sort by `indexed`, not by price.** A trade search returns at most 100
       ids however many match, so whatever it sorts by decides which 100 you
       see. Sorting by price hands back only the cheapest or only the dearest.
       Sorting by listing time hands back the most recent, which is unbiased with
       respect to price.

    3. **Newest only, never oldest.** `{"indexed": "asc"}` returns the listings
       that have sat unsold longest -- a month-old ask is evidence that the price
       was wrong, not evidence of value. Including both ends also produced a
       bimodal age distribution with nothing in the middle, so the chronological
       train/test split ended up training on stale listings and testing on fresh
       ones: two different market regimes rather than one.

    So: one unstratified slice for the natural distribution of the current
    listing flow, plus reward-stratified slices so rarer combinations are
    represented at all, all taken newest-first.
    """
    slices: list[dict[str, Any]] = [
        # The natural flow of what is being listed right now.
        {"sort_by": "indexed", "sort": "desc"},
    ]

    reward_bands: dict[str, list[tuple[int | None, int | None]]] = {
        "more_currency": [(1, 50), (51, 100), (101, 150), (151, None)],
        "more_maps": [(1, 40), (41, 80), (81, None)],
        "more_scarabs": [(1, 50), (51, 100), (101, None)],
        "more_divination_cards": [(1, None)],
    }
    for feature, bands in reward_bands.items():
        stat_id = REWARD_STATS[feature]
        for low, high in bands:
            slices.append(
                {
                    "sort_by": "indexed",
                    "sort": "desc",
                    "extra_stats": [_stat_range(stat_id, low, high)],
                }
            )

    # Pack size and quantity are ordinary map filters rather than pseudo stats.
    for low, high in [(None, 32), (33, 40), (41, 48), (49, None)]:
        slices.append({"sort_by": "indexed", "sort": "desc", "pack_size": (low, high)})

    return slices
