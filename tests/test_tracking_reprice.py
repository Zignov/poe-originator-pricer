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


# ----------------------------------------------------------------------
# The fit-derived recommendation, which replaces the ladder once there is
# enough of your own history to measure what actually clears.
from src.tracking.censoring import Observation  # noqa: E402
from src.tracking.reprice import recommend  # noqa: E402
from src.tracking.survival import fit_hazard  # noqa: E402

RATIO_EDGES = (0.0, 0.9, 1.05, 1.2, 99.0)


def _fit_where_cheap_clears():
    """Maps at 0.85x go quickly; maps at 1.3x sit for a week untouched."""
    obs = [Observation(85.0, 100.0, 0.85, 0.4, 0.5, True) for _ in range(20)]
    obs += [Observation(130.0, 100.0, 1.3, 200.0, None, False) for _ in range(20)]
    return fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)


def _fit_with_only_dear_maps():
    obs = [Observation(130.0, 100.0, 1.3, 200.0, None, False) for _ in range(20)]
    obs += [Observation(130.0, 100.0, 1.3, 150.0, 160.0, True) for _ in range(6)]
    return fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)


def test_the_recommendation_is_read_off_the_fit_when_there_is_one():
    rec = recommend(
        price_chaos=130.0, hours_listed=60, predicted_chaos=100.0,
        interval_low_chaos=50.0, fit=_fit_where_cheap_clears(),
        target_window_hours=24, target_probability=0.6, cfg=CFG,
    )
    assert rec.source == "fit"
    assert rec.floored is False
    assert rec.price_chaos < 130.0
    assert "clear" in rec.reason


def test_the_fit_recommendation_still_respects_the_floor():
    rec = recommend(
        price_chaos=130.0, hours_listed=60, predicted_chaos=100.0,
        interval_low_chaos=125.0, fit=_fit_where_cheap_clears(),
        target_window_hours=24, target_probability=0.6, cfg=CFG,
    )
    assert rec.floored is True
    assert rec.price_chaos == pytest.approx(125.0)


def test_an_unfitted_model_falls_back_to_the_ladder_and_says_so():
    empty = fit_hazard([], ratio_edges=RATIO_EDGES)
    rec = recommend(
        price_chaos=150.0, hours_listed=30, predicted_chaos=140.0,
        interval_low_chaos=100.0, fit=empty,
        target_window_hours=24, target_probability=0.6, cfg=CFG,
    )
    assert rec.source == "ladder"
    assert "allback" in rec.reason


def test_a_history_that_shows_nothing_clearing_falls_back_rather_than_inventing():
    rec = recommend(
        price_chaos=130.0, hours_listed=60, predicted_chaos=100.0,
        interval_low_chaos=50.0, fit=_fit_with_only_dear_maps(),
        target_window_hours=24, target_probability=0.6, cfg=CFG,
    )
    assert rec.source == "ladder", "no observed price clears in 24h; do not guess one"


def test_a_fresh_listing_gets_nothing_even_with_a_fit():
    assert recommend(
        price_chaos=130.0, hours_listed=2, predicted_chaos=100.0,
        interval_low_chaos=50.0, fit=_fit_where_cheap_clears(),
        target_window_hours=24, target_probability=0.6, cfg=CFG,
    ) is None


def test_the_reason_quotes_the_odds_it_used():
    rec = recommend(
        price_chaos=130.0, hours_listed=60, predicted_chaos=100.0,
        interval_low_chaos=50.0, fit=_fit_where_cheap_clears(),
        target_window_hours=24, target_probability=0.6, cfg=CFG,
    )
    assert "%" in rec.reason and "24" in rec.reason
