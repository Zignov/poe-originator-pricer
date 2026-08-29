"""Alerts: only when the evidence is decisive, and thresholds you actually set."""
from __future__ import annotations

import pytest

from src.tracking.alerts import Thresholds, alerts_for, observed_thresholds
from src.tracking.censoring import Observation


def _sold(lo, hi, ratio=1.0):
    return Observation(100.0, 100.0 / ratio, ratio, lo, hi, True)


def test_thresholds_fall_back_to_config_when_there_is_little_history():
    t = observed_thresholds([_sold(1, 2)], fallback_fast=1.0, fallback_slow=24.0)
    assert (t.fast_hours, t.slow_hours) == (1.0, 24.0)
    assert t.source == "config"


def test_thresholds_come_from_your_own_distribution_once_there_is_enough():
    obs = [_sold(h, h) for h in range(1, 41)]
    t = observed_thresholds(obs, fallback_fast=1.0, fallback_slow=24.0, min_sold=20)
    assert t.source == "observed"
    assert t.fast_hours == pytest.approx(10.75, abs=1.0), "your own lower quartile"
    assert t.slow_hours == pytest.approx(30.25, abs=1.0), "your own upper quartile"


def test_a_decisive_fast_sale_raises_underpriced():
    row = {
        "id": 1, "name": "Rune Path", "price_chaos": 90.0, "predicted_chaos": 120.0,
        "status": "gone", "sold_lo_hours": 0.2, "sold_hi_hours": 0.6,
        "hours_listed": 0.6,
    }
    alerts = alerts_for([row], Thresholds(1.0, 24.0, "config"))
    assert [a.kind for a in alerts] == ["underpriced"]
    assert "36 min" in alerts[0].evidence


def test_a_straddling_bracket_makes_no_claim():
    """Sold somewhere between 20 minutes and 9 hours is not 'sold fast'."""
    row = {
        "id": 1, "name": "Rune Path", "price_chaos": 90.0, "predicted_chaos": 120.0,
        "status": "gone", "sold_lo_hours": 0.33, "sold_hi_hours": 9.0,
        "hours_listed": 9.0,
    }
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_a_fast_sale_above_the_model_is_not_underpriced():
    row = {
        "id": 1, "name": "Rune Path", "price_chaos": 150.0, "predicted_chaos": 120.0,
        "status": "gone", "sold_lo_hours": 0.2, "sold_hi_hours": 0.5,
        "hours_listed": 0.5,
    }
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_a_long_stale_listing_above_the_model_raises_overpriced():
    row = {
        "id": 2, "name": "Bleak Lands", "price_chaos": 200.0,
        "predicted_chaos": 140.0, "status": "live", "sold_lo_hours": None,
        "sold_hi_hours": None, "hours_listed": 60.0,
    }
    alerts = alerts_for([row], Thresholds(1.0, 24.0, "config"))
    assert [a.kind for a in alerts] == ["overpriced"]
    assert "60 h" in alerts[0].evidence


def test_a_stale_listing_at_or_below_the_model_is_not_flagged():
    row = {
        "id": 2, "name": "Bleak Lands", "price_chaos": 100.0,
        "predicted_chaos": 140.0, "status": "live", "sold_lo_hours": None,
        "sold_hi_hours": None, "hours_listed": 60.0,
    }
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_excluded_and_ambiguous_rows_never_alert():
    base = {
        "name": "X", "price_chaos": 90.0, "predicted_chaos": 120.0, "status": "gone",
        "sold_lo_hours": 0.2, "sold_hi_hours": 0.5, "hours_listed": 0.5,
    }
    rows = [
        dict(base, id=1, exclude_from_learning=1),
        dict(base, id=2, ambiguous=1),
    ]
    assert alerts_for(rows, Thresholds(1.0, 24.0, "config")) == []


def test_a_row_with_no_model_estimate_cannot_be_judged():
    row = {
        "id": 3, "name": "Rune Path", "price_chaos": 90.0, "predicted_chaos": None,
        "status": "gone", "sold_lo_hours": 0.2, "sold_hi_hours": 0.5,
        "hours_listed": 0.5,
    }
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_the_evidence_names_the_threshold_and_where_it_came_from():
    row = {
        "id": 2, "name": "Bleak Lands", "price_chaos": 200.0,
        "predicted_chaos": 140.0, "status": "live", "sold_lo_hours": None,
        "sold_hi_hours": None, "hours_listed": 60.0,
    }
    alert = alerts_for([row], Thresholds(1.0, 30.0, "observed"))[0]
    assert "30 h" in alert.evidence and "observed" in alert.evidence
