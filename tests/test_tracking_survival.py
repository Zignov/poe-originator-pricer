"""The censored fit: uses coarse data for what it is worth, and refuses to invent."""
from __future__ import annotations

import pytest

from src.tracking.censoring import Observation
from src.tracking.survival import fit_hazard

RATIO_EDGES = (0.0, 1.0, 1.2, 99.0)


def _sold(ratio, lo, hi):
    return Observation(
        price_chaos=100.0, predicted_chaos=100.0 / ratio, ratio=ratio,
        lo_hours=lo, hi_hours=hi, sold=True,
    )


def _live(ratio, hours):
    return Observation(
        price_chaos=100.0, predicted_chaos=100.0 / ratio, ratio=ratio,
        lo_hours=hours, hi_hours=None, sold=False,
    )


def test_no_data_refuses_rather_than_returning_a_flat_curve():
    fit = fit_hazard([], ratio_edges=RATIO_EDGES)
    assert fit.ok is False
    assert "not enough" in fit.reason.lower()
    assert fit.probability_within(1.0, 24) is None


def test_exact_brackets_reduce_to_the_empirical_proportion():
    """With no censoring ambiguity, the fit must not be cleverer than counting."""
    obs = [_sold(0.9, 0.5, 0.5) for _ in range(15)]
    obs += [_live(0.9, 100.0) for _ in range(5)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.ok
    assert fit.probability_within(0.9, 24) == pytest.approx(15 / 20, abs=0.02)


def test_a_wide_bracket_still_counts_as_a_sale_somewhere():
    obs = [_sold(0.9, 0.3, 9.0) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(0.9, 24) == pytest.approx(1.0, abs=0.05), (
        "every one of them sold; we only don't know when"
    )


def test_a_wide_bracket_is_agnostic_inside_itself():
    """A 20min-9h bracket must not claim the sale happened in the first hour."""
    obs = [_sold(0.9, 0.3, 9.0) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    within_one = fit.probability_within(0.9, 1.0)
    assert 0.0 < within_one < 0.9, f"claimed {within_one:.2f} sold within the hour"


def test_cheaper_maps_clear_faster_than_dearer_ones():
    obs = [_sold(0.9, 0.5, 0.6) for _ in range(20)]
    obs += [_live(1.5, 200.0) for _ in range(20)]
    obs += [_sold(1.5, 150.0, 160.0) for _ in range(2)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(0.9, 24) > fit.probability_within(1.5, 24)


def test_a_band_with_no_data_refuses_to_extrapolate_from_its_neighbours():
    obs = [_sold(0.9, 0.5, 0.6) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(1.5, 24) is None


def test_probability_is_monotone_in_time():
    obs = [_sold(0.9, 0.5, 2.0) for _ in range(10)]
    obs += [_sold(0.9, 20.0, 30.0) for _ in range(10)]
    obs += [_live(0.9, 200.0) for _ in range(10)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    values = [fit.probability_within(0.9, h) for h in (1, 3, 6, 12, 24, 48, 96)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))


def test_observations_without_a_ratio_are_ignored():
    obs = [Observation(100.0, None, None, 1.0, 2.0, True) for _ in range(30)]
    assert fit_hazard(obs, ratio_edges=RATIO_EDGES).ok is False


def test_a_late_sale_does_not_raise_the_odds_of_selling_early():
    """Ten maps that all took two days should not imply a fast first hour."""
    obs = [_sold(0.9, 40.0, 48.0) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(0.9, 1.0) == pytest.approx(0.0, abs=1e-6)
    assert fit.probability_within(0.9, 96.0) == pytest.approx(1.0, abs=0.05)
