"""Turning a listing's history into observations that admit what they don't know."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.tracking.censoring import observations_for
from src.tracking.reconcile import TrackedRow

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _row(price=120.0, priced_at=T0, last_seen=T0, status="live",
         gone_first_missing=None, predicted=100.0):
    row = TrackedRow(
        id=1, trade_listing_id="abc", fingerprint="fp", price_chaos=price,
        priced_at=priced_at, first_seen=T0, last_seen=last_seen, status=status,
        gone_first_missing=gone_first_missing,
    )
    return row, predicted


def _event(at, event, old=None, new=None):
    return {
        "at": at.isoformat(),
        "event": event,
        "old_price_chaos": old,
        "new_price_chaos": new,
    }


def test_a_live_listing_is_right_censored():
    row, predicted = _row(last_seen=T0 + timedelta(hours=5))
    obs = observations_for(
        row, [_event(T0, "appeared", new=120.0)], predicted, now=T0 + timedelta(hours=5)
    )
    assert len(obs) == 1
    assert obs[0].sold is False
    assert obs[0].lo_hours == pytest.approx(5.0)
    assert obs[0].hi_hours is None, "still live: no upper bound exists"


def test_a_sale_is_an_interval_not_a_number():
    row, predicted = _row(
        last_seen=T0 + timedelta(minutes=20), status="gone",
        gone_first_missing=T0 + timedelta(hours=9),
    )
    obs = observations_for(
        row, [_event(T0, "appeared", new=120.0)], predicted, now=T0 + timedelta(hours=10)
    )
    assert obs[0].sold is True
    assert obs[0].lo_hours == pytest.approx(20 / 60)
    assert obs[0].hi_hours == pytest.approx(9.0)


def test_the_ratio_is_the_ask_over_the_model():
    row, predicted = _row(price=120.0, predicted=100.0)
    obs = observations_for(
        row, [_event(T0, "appeared", new=120.0)], predicted, now=T0 + timedelta(hours=1)
    )
    assert obs[0].ratio == pytest.approx(1.2)


def test_a_markdown_makes_two_observations_and_the_first_is_a_non_sale():
    """The three hours it spent at 180c without selling is evidence in itself."""
    repriced_at = T0 + timedelta(hours=3)
    row, predicted = _row(
        price=120.0, priced_at=repriced_at,
        last_seen=repriced_at + timedelta(minutes=10), status="gone",
        gone_first_missing=repriced_at + timedelta(minutes=20),
    )
    events = [
        _event(T0, "appeared", new=180.0),
        _event(repriced_at, "repriced", old=180.0, new=120.0),
    ]
    obs = observations_for(row, events, predicted, now=repriced_at + timedelta(hours=1))

    assert len(obs) == 2
    first, second = obs
    assert first.price_chaos == 180.0 and first.sold is False
    assert first.lo_hours == pytest.approx(3.0), "it survived three hours at 180c"
    assert second.price_chaos == 120.0 and second.sold is True
    assert second.hi_hours == pytest.approx(20 / 60), "clock runs from the markdown"


def test_two_markdowns_make_three_observations():
    a, b = T0 + timedelta(hours=2), T0 + timedelta(hours=6)
    row, predicted = _row(price=100.0, priced_at=b, last_seen=b + timedelta(hours=1))
    events = [
        _event(T0, "appeared", new=180.0),
        _event(a, "repriced", old=180.0, new=140.0),
        _event(b, "repriced", old=140.0, new=100.0),
    ]
    obs = observations_for(row, events, predicted, now=b + timedelta(hours=1))
    assert [o.price_chaos for o in obs] == [180.0, 140.0, 100.0]
    assert [o.sold for o in obs] == [False, False, False]
    assert obs[0].lo_hours == pytest.approx(2.0)
    assert obs[1].lo_hours == pytest.approx(4.0)


def test_a_map_with_no_prediction_has_no_ratio_but_still_has_a_time():
    row, predicted = _row(predicted=None)
    obs = observations_for(
        row, [_event(T0, "appeared", new=120.0)], predicted, now=T0 + timedelta(hours=4)
    )
    assert obs[0].ratio is None
    assert obs[0].lo_hours == pytest.approx(4.0)


def test_a_reappearance_cancels_the_sale_it_followed():
    """A sold map cannot come back, so the 'gone' was a mistake, not an episode."""
    row, predicted = _row(last_seen=T0 + timedelta(hours=4))
    events = [
        _event(T0, "appeared", new=120.0),
        _event(T0 + timedelta(hours=1), "gone", old=120.0),
        _event(T0 + timedelta(hours=2), "reappeared"),
    ]
    obs = observations_for(row, events, predicted, now=T0 + timedelta(hours=4))
    assert len(obs) == 1
    assert obs[0].sold is False
