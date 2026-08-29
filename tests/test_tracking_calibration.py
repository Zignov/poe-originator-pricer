"""The personal factor: measured, clamped, bound to a model, and refusable."""
from __future__ import annotations

from src.tracking.calibration import CalibrationConfig, personal_factor
from src.tracking.censoring import Observation

CFG = CalibrationConfig(min_sold=20, clamp=(0.8, 1.3))


def _sold(ratio, lo=0.5, hi=0.6):
    return Observation(100.0 * ratio, 100.0, ratio, lo, hi, True)


def _unsold(ratio, hours=200.0):
    return Observation(100.0 * ratio, 100.0, ratio, hours, None, False)


def test_too_little_history_refuses_and_says_so():
    obs = [_sold(1.0) for _ in range(5)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is False
    assert cal.factor == 1.0
    assert "20" in cal.source


def test_fast_sales_alone_are_not_enough_because_they_fix_no_upper_bound():
    obs = [_sold(0.85) for _ in range(30)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is False
    assert "both sides" in cal.source


def test_stale_listings_alone_are_not_enough_either():
    obs = [_unsold(1.25) for _ in range(30)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is False


def test_a_two_sided_history_yields_a_factor_between_the_brackets():
    obs = [_sold(0.85) for _ in range(15)]
    obs += [_unsold(1.25) for _ in range(15)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is True
    assert 0.85 < cal.factor < 1.25
    assert cal.model_version == "v1"
    assert cal.sample == 30


def test_the_factor_is_clamped_and_the_clamp_is_visible():
    obs = [_sold(1.35) for _ in range(20)]
    obs += [_unsold(1.6) for _ in range(20)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is True
    assert cal.factor == 1.3
    assert "clamp" in cal.source


def test_a_wildly_out_of_range_measurement_is_refused_not_clamped():
    """Mirrors SANE_MARKET_RATIO: a factor this far off means something is wrong."""
    obs = [_sold(0.2) for _ in range(20)]
    obs += [_unsold(0.3) for _ in range(20)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is False
    assert "miscalibrat" in cal.source


def test_stale_maps_cheaper_than_sold_ones_is_not_something_to_calibrate_on():
    obs = [_sold(1.3) for _ in range(20)]
    obs += [_unsold(0.8) for _ in range(20)]
    cal = personal_factor(obs, "v1", CFG)
    assert cal.applied is False
    assert "does not explain" in cal.source


def test_the_calibration_records_which_model_it_was_measured_against():
    obs = [_sold(0.85) for _ in range(15)] + [_unsold(1.25) for _ in range(15)]
    cal = personal_factor(obs, "catboost-2026-08-24", CFG)
    assert cal.model_version == "catboost-2026-08-24"
    assert "catboost-2026-08-24" in cal.source


def test_a_measurement_does_not_survive_a_model_change():
    obs = [_sold(0.85) for _ in range(15)] + [_unsold(1.25) for _ in range(15)]
    cal = personal_factor(obs, "model-a", CFG)
    assert cal.applies_to("model-a") is True
    assert cal.applies_to("model-b") is False, (
        "the two corrections would stack, the second fitted on prices the "
        "first already moved"
    )
