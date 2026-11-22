"""The reconciler: what one poll means for what we already knew."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.schema import MarketListing, NormalizedMap
from src.tracking.reconcile import TrackedRow, reconcile

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _map(name="Glyph Solace", quantity=90):
    return NormalizedMap(
        name=name, base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=quantity, rarity=140,
        pack_size=30, more_currency=47,
    )


def _listing(name="Glyph Solace", price=120.0, listing_id="abc", quantity=90):
    item = _map(name, quantity)
    return MarketListing(
        item=item, listing_id=listing_id, league="Allflame",
        price_amount=price, price_currency="chaos", price_chaos=price,
        price_type="~b/o", seller="me",
    )


def _row(fingerprint, price=120.0, listing_id="abc", at=T0, status="live"):
    return TrackedRow(
        id=1, trade_listing_id=listing_id, fingerprint=fingerprint,
        price_chaos=price, priced_at=at, first_seen=at, last_seen=at,
        status=status,
    )


def test_new_listing_appears():
    result = reconcile([], [_listing()], now=T0, complete=True)
    assert [e.event for e in result.events] == ["appeared"]
    assert len(result.live) == 1
    assert result.live[0].priced_at == T0


def test_unchanged_listing_only_refreshes_last_seen():
    row = _row(_map().fingerprint())
    later = T0 + timedelta(minutes=15)
    result = reconcile([row], [_listing()], now=later, complete=True)
    assert result.events == ()
    assert result.live[0].last_seen == later
    assert result.live[0].priced_at == T0


def test_reprice_emits_event_and_restarts_the_clock():
    row = _row(_map().fingerprint(), price=180.0)
    later = T0 + timedelta(hours=3)
    result = reconcile([row], [_listing(price=120.0)], now=later, complete=True)
    assert [e.event for e in result.events] == ["repriced"]
    assert result.events[0].old_price_chaos == 180.0
    assert result.events[0].new_price_chaos == 120.0
    assert result.live[0].priced_at == later, "the clock restarts at the new price"


def test_vanished_listing_is_gone_and_brackets_the_sale():
    row = _row(_map().fingerprint()).replace(last_seen=T0 + timedelta(minutes=20))
    later = T0 + timedelta(hours=9)
    result = reconcile([row], [], now=later, complete=True)
    assert [e.event for e in result.events] == ["gone"]
    gone = result.gone[0]
    assert gone.status == "gone"
    assert gone.last_seen == T0 + timedelta(minutes=20), "lower bound is untouched"
    assert gone.gone_first_missing == later, "upper bound is this poll"


def test_partial_poll_never_marks_anything_gone():
    row = _row(_map().fingerprint())
    result = reconcile([row], [], now=T0 + timedelta(hours=1), complete=False)
    assert result.events == ()
    assert result.gone == ()
    assert result.skipped_disappearance is True


def test_id_change_with_matching_fingerprint_is_a_reprice_not_a_sale():
    row = _row(_map().fingerprint(), price=180.0, listing_id="old")
    later = T0 + timedelta(hours=1)
    result = reconcile(
        [row], [_listing(price=120.0, listing_id="new")], now=later, complete=True
    )
    assert [e.event for e in result.events] == ["repriced"]
    assert result.live[0].trade_listing_id == "new"
    assert result.gone == ()


def test_reappearance_inside_the_window_undoes_the_sale():
    fp = _map().fingerprint()
    row = TrackedRow(
        id=1, trade_listing_id="abc", fingerprint=fp, price_chaos=120.0,
        priced_at=T0, first_seen=T0, last_seen=T0, status="gone",
        gone_first_missing=T0 + timedelta(hours=1),
    )
    result = reconcile([row], [_listing()], now=T0 + timedelta(hours=2), complete=True)
    assert [e.event for e in result.events] == ["reappeared"]
    assert result.live[0].status == "live"
    assert result.live[0].gone_first_missing is None, "it was never sold"


def test_reappearance_outside_the_window_is_a_different_map():
    fp = _map().fingerprint()
    row = TrackedRow(
        id=1, trade_listing_id="abc", fingerprint=fp, price_chaos=120.0,
        priced_at=T0, first_seen=T0, last_seen=T0, status="gone",
        gone_first_missing=T0 + timedelta(hours=1),
    )
    result = reconcile(
        [row], [_listing()], now=T0 + timedelta(days=3), complete=True,
        reappearance_window_hours=6.0,
    )
    assert [e.event for e in result.events] == ["appeared"]
    assert result.live[0].id is None, "a new row, not the sold one resurrected"


def test_two_identical_live_maps_are_flagged_not_guessed():
    a = _listing(listing_id="a")
    b = _listing(listing_id="b")
    result = reconcile([], [a, b], now=T0, complete=True)
    assert len(result.live) == 2
    assert all(r.ambiguous for r in result.live)
    assert result.ambiguous == (_map().fingerprint(),)


def test_distinct_maps_are_not_ambiguous():
    result = reconcile(
        [], [_listing(listing_id="a"), _listing(listing_id="b", quantity=70)],
        now=T0, complete=True,
    )
    assert not any(r.ambiguous for r in result.live)


# ---------------------------------------------------------------------------
# The age comes from GGG's own timestamp, not from how long we have watched
# ---------------------------------------------------------------------------
def _indexed_listing(indexed, price=120.0, listing_id="abc"):
    listing = _listing(price=price, listing_id=listing_id)
    return replace(listing, indexed_at=indexed)


def test_a_first_poll_dates_a_listing_from_the_api_not_from_the_poll():
    """The whole point of reading `listing.indexed`: no scan history needed.

    A map GGG says was listed three days ago is three days old on the first
    pass after the app opens, not newly born because that is when we looked.
    """
    listed = T0 - timedelta(hours=62)
    result = reconcile([], [_indexed_listing(listed)], now=T0, complete=True)
    assert result.live[0].priced_at == listed
    assert result.live[0].indexed_at == listed


def test_a_currency_rate_move_alone_is_not_a_reprice():
    """price_chaos is a conversion, not the ask.

    The divine rate is refreshed from the live market while the tracker runs, so
    a divine-priced listing's chaos figure moves without the seller touching
    anything. Calling that a re-price reset the clock to now and wiped the age
    off every such map on the page -- and split the episode, so the survival fit
    saw a fresh listing where there was a three-day-old one.
    """
    listed = T0 - timedelta(hours=62)
    row = _row(_map().fingerprint(), price=210.0, at=listed).replace(indexed_at=listed)
    later = T0 + timedelta(minutes=15)

    # Same listing, same `indexed`, 212 chaos to the divine instead of 210.
    result = reconcile([row], [_indexed_listing(listed, price=212.0)], later, True)

    assert result.events == (), "nobody repriced anything"
    assert result.live[0].priced_at == listed, "the clock did not restart"
    assert result.live[0].price_chaos == 212.0, "today's conversion is still carried"


def test_a_real_reprice_is_dated_from_when_ggg_reindexed_it():
    listed = T0 - timedelta(hours=62)
    reindexed = T0 - timedelta(hours=2)
    row = _row(_map().fingerprint(), price=180.0, at=listed).replace(indexed_at=listed)

    result = reconcile([row], [_indexed_listing(reindexed, price=120.0)], T0, True)

    assert [e.event for e in result.events] == ["repriced"]
    assert result.live[0].priced_at == reindexed, (
        "the new price went up when GGG re-indexed it, not when we noticed"
    )
    assert result.live[0].indexed_at == reindexed


def test_a_row_with_no_timestamp_picks_one_up_the_next_time_it_is_seen():
    """Rows written before this was carried are not stuck on the poll clock."""
    row = _row(_map().fingerprint(), price=120.0, at=T0)
    assert row.indexed_at is None
    listed = T0 - timedelta(hours=30)
    result = reconcile([row], [_indexed_listing(listed)], T0 + timedelta(minutes=5), True)
    assert result.live[0].indexed_at == listed


def test_without_timestamps_the_converted_price_still_decides():
    """No `indexed` on either side -- an import -- and the old rule applies."""
    row = _row(_map().fingerprint(), price=180.0)
    later = T0 + timedelta(hours=3)
    result = reconcile([row], [_listing(price=120.0)], now=later, complete=True)
    assert [e.event for e in result.events] == ["repriced"]
    assert result.live[0].priced_at == later
