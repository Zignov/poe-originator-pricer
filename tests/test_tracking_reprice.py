"""What to re-price a map to when it has not sold."""
from __future__ import annotations

import pytest

from src.tracking.reprice import LadderConfig, recommend_ladder

CFG = LadderConfig(
    steps=((24, 0.92), (72, 0.85), (168, 0.78)),
    floor_ratio=0.75,
    stale_after_hours=24,
)


def test_a_fresh_listing_gets_no_recommendation():
    assert recommend_ladder(
        150.0, hours_listed=3, predicted_chaos=140.0, interval_low_chaos=120.0, cfg=CFG
    ) is None


def test_one_day_stale_steps_down_one_rung():
    rec = recommend_ladder(
        150.0, hours_listed=30, predicted_chaos=140.0, interval_low_chaos=100.0, cfg=CFG
    )
    assert rec.price_chaos == pytest.approx(138.0)
    assert rec.source == "ladder"
    assert "30 h" in rec.reason and "has not sold" in rec.reason


def test_a_week_stale_steps_down_furthest():
    rec = recommend_ladder(
        150.0, hours_listed=200, predicted_chaos=140.0, interval_low_chaos=90.0, cfg=CFG
    )
    assert rec.price_chaos == pytest.approx(117.0)


def test_the_floor_stops_a_stale_map_giving_itself_away():
    rec = recommend_ladder(
        110.0, hours_listed=200, predicted_chaos=140.0, interval_low_chaos=120.0,
        cfg=CFG,
    )
    assert rec.floored is True
    assert rec.price_chaos == pytest.approx(120.0), "never below the interval floor"
    assert "floor" in rec.reason


def test_already_at_the_floor_recommends_nothing_and_says_why():
    rec = recommend_ladder(
        120.0, hours_listed=400, predicted_chaos=140.0, interval_low_chaos=120.0,
        cfg=CFG,
    )
    assert rec.floored is True
    assert rec.price_chaos == pytest.approx(120.0)
    assert "may simply not want it" in rec.reason


def test_a_map_with_no_prediction_uses_the_ladder_without_a_floor():
    rec = recommend_ladder(
        150.0, hours_listed=30, predicted_chaos=None, interval_low_chaos=None, cfg=CFG
    )
    assert rec.price_chaos == pytest.approx(138.0)
    assert rec.floored is False


def test_the_ladder_labels_itself_a_fallback_not_a_measurement():
    rec = recommend_ladder(
        150.0, hours_listed=30, predicted_chaos=140.0, interval_low_chaos=100.0, cfg=CFG
    )
    assert "allback" in rec.reason, (
        "a fallback presented as a measurement is worse than no number at all"
    )
