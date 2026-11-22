"""When a long collection run decides to refit the model.

The condition that is easy to leave out is the second one. A plain
`total > last_retrained` is true on any cycle that added a single listing, so
once the threshold was crossed the loop would retrain every twenty minutes,
all night, for a dozen new rows each time -- and each retrain overwrites the
model artifact.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/collect_loop.py")


@pytest.fixture(scope="module")
def loop():
    if not SCRIPT.exists():
        pytest.skip("collect_loop.py not in this checkout")
    spec = importlib.util.spec_from_file_location("collect_loop", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["collect_loop"] = module
    spec.loader.exec_module(module)
    return module


def test_nothing_retrains_when_the_feature_is_off(loop):
    assert not loop.should_retrain(99_999, 0, 0)


def test_below_the_threshold_nothing_happens(loop):
    assert not loop.should_retrain(2_499, 2_500, 0)


def test_the_first_crossing_retrains(loop):
    assert loop.should_retrain(2_500, 2_500, 0)


def test_a_trickle_after_a_retrain_does_not_retrain_again(loop):
    """The bug. Twelve new listings is not a reason to refit."""
    assert not loop.should_retrain(2_512, 2_500, 2_500)


@pytest.mark.parametrize("gained", [1, 50, 200, 249])
def test_growth_under_the_step_is_ignored(loop, gained):
    assert not loop.should_retrain(2_500 + gained, 2_500, 2_500)


@pytest.mark.parametrize("gained", [250, 400, 2_000])
def test_real_growth_does_retrain(loop, gained):
    assert loop.should_retrain(2_500 + gained, 2_500, 2_500)


def test_the_step_scales_with_a_large_threshold(loop):
    """A tenth of the target, so a big dataset is not refitted on a rounding
    error -- 250 out of 50,000 would be."""
    assert not loop.should_retrain(50_400, 50_000, 50_000)
    assert loop.should_retrain(55_000, 50_000, 50_000)


def test_the_floor_applies_to_a_small_threshold(loop):
    """A tenth of 500 is 50, which is too few rows to move a fit."""
    assert not loop.should_retrain(560, 500, 500)
    assert loop.should_retrain(750, 500, 500)


def test_the_stop_file_name_is_documented_where_a_user_will_look(loop):
    """It is the only way to stop a run that is not attached to a terminal."""
    assert loop.STOP_FILE == "collect_loop.stop"
    assert "collect_loop.stop" in (loop.__doc__ or "")


def test_a_rest_between_cycles_is_the_default(loop):
    """Hammering the same exhausted slices adds nothing and spends the limit."""
    assert loop.DEFAULT_REST_MINUTES >= 5
