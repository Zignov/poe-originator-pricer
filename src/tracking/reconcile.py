"""What one poll of your own listings means for what we already knew.

Pure. No database, no network, no clock of its own -- `now` is passed in, so a
sequence of polls can be replayed in a test in microseconds and a bug in the
disappearance rules is reproducible rather than a thing that happened overnight.

The rules that carry the weight:

* **A re-price restarts the clock.** Selling ten minutes after a markdown is a
  ten-minute sale at the new price, not a three-day sale at the old one. So a
  price change moves `priced_at`, and every duration downstream measures from
  there.
* **A partial poll changes nothing.** A listing missing because it fell outside
  a truncated result window is indistinguishable from one that sold, and
  counting it as a sale would inject a fake fast sale into the very data the
  calibration is fitted on. Better to learn nothing this pass.
* **A disappearance is bracketed, never timed.** `last_seen` is the last moment
  it was there, `gone_first_missing` the first moment it was not. The truth is
  somewhere between, and nothing here pretends to know where.
* **Identity is a judgement, so it lives here** rather than in a unique index.
  Trade ids are matched first; a changed id with a matching reward fingerprint
  is the same map re-listed, not a sale and a new listing. Two live maps that
  cannot be told apart are flagged, not guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Sequence

from src.schema import MarketListing

APPEARED = "appeared"
REPRICED = "repriced"
GONE = "gone"
REAPPEARED = "reappeared"


@dataclass(frozen=True)
class TrackedRow:
    """A tracked listing reduced to what reconciling needs in order to decide."""

    id: int | None
    trade_listing_id: str | None
    fingerprint: str
    price_chaos: float | None
    priced_at: datetime
    first_seen: datetime
    last_seen: datetime
    status: str = "live"
    gone_first_missing: datetime | None = None
    ambiguous: bool = False
    #: Set by hand on the page for a map you pulled rather than sold. Reconciling
    #: ignores it -- a delisted map still disappears -- but the learning must
    #: not see it, which is the whole point of the tick box.
    exclude_from_learning: bool = False
    #: The snapshot row this was matched to, when there was one. The writer needs
    #: the item to fill in name, rewards and stash position on an insert;
    #: reconciling itself never reads it.
    listing: MarketListing | None = None

    def replace(self, **changes) -> "TrackedRow":
        return replace(self, **changes)


@dataclass(frozen=True)
class Event:
    fingerprint: str
    trade_listing_id: str | None
    at: datetime
    event: str
    old_price_chaos: float | None = None
    new_price_chaos: float | None = None
    row_id: int | None = None


@dataclass(frozen=True)
class Reconciliation:
    events: tuple[Event, ...] = ()
    live: tuple[TrackedRow, ...] = ()
    gone: tuple[TrackedRow, ...] = ()
    ambiguous: tuple[str, ...] = ()
    skipped_disappearance: bool = False


def _same_price(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) < 1e-6


def reconcile(
    previous: Sequence[TrackedRow],
    snapshot: Sequence[MarketListing],
    now: datetime,
    complete: bool,
    reappearance_window_hours: float = 6.0,
) -> Reconciliation:
    """Fold one snapshot of your listings into the rows we already hold."""
    events: list[Event] = []
    live: list[TrackedRow] = []
    gone: list[TrackedRow] = []

    by_id = {
        r.trade_listing_id: r
        for r in previous
        if r.trade_listing_id and r.status == "live"
    }
    unmatched_live = {r.fingerprint: r for r in previous if r.status == "live"}
    recently_gone = {
        r.fingerprint: r
        for r in previous
        if r.status == "gone"
        and r.gone_first_missing is not None
        and now - r.gone_first_missing <= timedelta(hours=reappearance_window_hours)
    }

    # Fingerprints appearing more than once in this snapshot cannot be told apart
    # from each other. Flag them; never guess which is which.
    seen: dict[str, int] = {}
    for listing in snapshot:
        fp = listing.item.fingerprint()
        seen[fp] = seen.get(fp, 0) + 1
    duplicated = {fp for fp, count in seen.items() if count > 1}

    matched_rows: set[int] = set()

    for listing in snapshot:
        fp = listing.item.fingerprint()
        ambiguous = fp in duplicated

        row = by_id.get(listing.listing_id)
        if row is None and not ambiguous:
            row = unmatched_live.get(fp)
        if row is not None and row.id is not None and row.id in matched_rows:
            row = None

        if row is not None:
            if row.id is not None:
                matched_rows.add(row.id)
            if _same_price(row.price_chaos, listing.price_chaos):
                live.append(
                    row.replace(
                        last_seen=now,
                        trade_listing_id=listing.listing_id or row.trade_listing_id,
                        ambiguous=ambiguous or row.ambiguous,
                        listing=listing,
                    )
                )
                continue
            events.append(
                Event(
                    fingerprint=fp,
                    trade_listing_id=listing.listing_id,
                    at=now,
                    event=REPRICED,
                    old_price_chaos=row.price_chaos,
                    new_price_chaos=listing.price_chaos,
                    row_id=row.id,
                )
            )
            live.append(
                row.replace(
                    trade_listing_id=listing.listing_id or row.trade_listing_id,
                    price_chaos=listing.price_chaos,
                    priced_at=now,
                    last_seen=now,
                    ambiguous=ambiguous or row.ambiguous,
                    listing=listing,
                )
            )
            continue

        # Not a live row. It may be one we called sold a moment ago -- and a sold
        # map cannot come back, so a quick return means it was never sold.
        revived = recently_gone.get(fp) if not ambiguous else None
        if revived is not None and revived.id not in matched_rows:
            if revived.id is not None:
                matched_rows.add(revived.id)
            events.append(
                Event(
                    fingerprint=fp,
                    trade_listing_id=listing.listing_id,
                    at=now,
                    event=REAPPEARED,
                    row_id=revived.id,
                )
            )
            live.append(
                revived.replace(
                    trade_listing_id=listing.listing_id,
                    price_chaos=listing.price_chaos,
                    last_seen=now,
                    status="live",
                    gone_first_missing=None,
                    listing=listing,
                )
            )
            continue

        events.append(
            Event(
                fingerprint=fp,
                trade_listing_id=listing.listing_id,
                at=now,
                event=APPEARED,
                new_price_chaos=listing.price_chaos,
            )
        )
        live.append(
            TrackedRow(
                id=None,
                trade_listing_id=listing.listing_id,
                fingerprint=fp,
                price_chaos=listing.price_chaos,
                priced_at=listing.indexed_at or now,
                first_seen=now,
                last_seen=now,
                status="live",
                ambiguous=ambiguous,
                listing=listing,
            )
        )

    if not complete:
        # Say nothing about what is missing: we do not know that it is missing.
        return Reconciliation(
            events=tuple(events),
            live=tuple(live),
            gone=(),
            ambiguous=tuple(sorted(duplicated)),
            skipped_disappearance=True,
        )

    for row in previous:
        if row.status != "live" or row.id in matched_rows:
            continue
        events.append(
            Event(
                fingerprint=row.fingerprint,
                trade_listing_id=row.trade_listing_id,
                at=now,
                event=GONE,
                old_price_chaos=row.price_chaos,
                row_id=row.id,
            )
        )
        gone.append(row.replace(status="gone", gone_first_missing=now))

    return Reconciliation(
        events=tuple(events),
        live=tuple(live),
        gone=tuple(gone),
        ambiguous=tuple(sorted(duplicated)),
        skipped_disappearance=False,
    )
