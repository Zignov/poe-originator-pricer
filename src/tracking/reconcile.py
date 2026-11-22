"""What one poll of your own listings means for what we already knew.

Pure. No database, no network, no clock of its own -- `now` is passed in, so a
sequence of polls can be replayed in a test in microseconds and a bug in the
disappearance rules is reproducible rather than a thing that happened overnight.

The rules that carry the weight:

* **A re-price restarts the clock.** Selling ten minutes after a markdown is a
  ten-minute sale at the new price, not a three-day sale at the old one. So a
  price change moves `priced_at`, and every duration downstream measures from
  there.
* **The clock is GGG's, not ours.** `priced_at` is taken from the listing's own
  `indexed` timestamp -- the "listed X ago" the trade site prints -- and only
  falls back to the polling clock when the API did not supply one. That is what
  makes a map listed three days ago read as three days old on the *first* poll
  after the app starts, with no scan history behind it at all. Deriving the age
  from when we happened to first see it made every listing an hour old after
  every restart.
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
    #: GGG's own `listing.indexed` for the ask that is currently up -- the
    #: "listed X ago" the trade site prints. Authoritative, and available on the
    #: very first poll, which is why the page's ages are computed from it rather
    #: than from when this app first happened to look. `None` only for rows
    #: written before this was carried, or by an importer that has no such
    #: timestamp to give; every reader falls back to `priced_at` for those.
    indexed_at: datetime | None = None
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


def _repriced(row: TrackedRow, listing: MarketListing) -> bool:
    """Did the *seller* change the ask, or did only our conversion move?

    `price_chaos` is not what the seller typed. It is their ask run through the
    current divine/exalt rate, and that rate is refreshed from the live market
    while the tracker is running. So two polls either side of a rate refresh
    produce two different chaos figures for a listing nobody touched -- and
    comparing on that number alone called it a re-price, reset `priced_at` to
    the moment of the poll, and wiped the age off every divine-priced map on the
    page. It also cut the episode in two, so the survival fit saw a fresh live
    listing where there was a three-day-old one.

    GGG re-indexes a listing whenever its note changes, so `listing.indexed` is
    the authoritative answer to "has the ask changed": same timestamp, same ask,
    whatever the conversion says today. Only when one side has no timestamp to
    compare -- an importer, or a row written before this was carried -- does the
    converted price get the casting vote.
    """
    if _same_price(row.price_chaos, listing.price_chaos):
        return False
    if listing.indexed_at is not None and row.indexed_at is not None:
        return listing.indexed_at != row.indexed_at
    return True


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

        # GGG's timestamp for the ask now up. Kept fresh on every match, so a
        # row written before this existed -- or one whose first poll came from
        # an importer with no timestamp -- picks one up the next time it is seen
        # rather than being stuck on the polling clock forever.
        indexed_at = listing.indexed_at or (row.indexed_at if row else None)

        if row is not None:
            if row.id is not None:
                matched_rows.add(row.id)
            if not _repriced(row, listing):
                live.append(
                    row.replace(
                        last_seen=now,
                        trade_listing_id=listing.listing_id or row.trade_listing_id,
                        # The conversion may have moved even though the ask did
                        # not; carry today's number without calling it a change.
                        price_chaos=listing.price_chaos,
                        indexed_at=indexed_at,
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
                    # When GGG re-indexed it is when the new price went up. That
                    # is minutes to hours earlier than the poll that noticed,
                    # and on the slow cadence the difference is a quarter of an
                    # hour of a listing's life credited to the wrong price.
                    #
                    # `listing.indexed_at`, deliberately, not the carried-forward
                    # `indexed_at`: the latter falls back to the *old* stamp when
                    # this snapshot has none, and dating a new price from the old
                    # one's listing time would run the clock backwards.
                    priced_at=listing.indexed_at or now,
                    indexed_at=indexed_at,
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
                    indexed_at=listing.indexed_at or revived.indexed_at,
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
                # The whole point of the first poll after a restart: a map GGG
                # says was listed three days ago is three days old here, not
                # newly born because this is the first time we looked.
                priced_at=listing.indexed_at or now,
                indexed_at=listing.indexed_at,
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
