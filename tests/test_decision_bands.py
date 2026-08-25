"""Pricing scored and shown by the decision it feeds.

Two decisions, and they want different things from the model:

  under ~30c  the map gets rerolled and the number is never read;
  50c and up  the number is what you list it for, and it is money.

Optimising plain MAE over everything optimises the first one, because the
reroll pile is a third of the rows. These tests pin the pieces that stop that.
"""
from __future__ import annotations

import numpy as np

from src.ml.decision_bands import (
    REROLL_BELOW_CHAOS,
    WORTH_SELLING_CHAOS,
    apply_training_floor,
    decision_metrics,
    value_weights,
)


# -- the weighting ----------------------------------------------------------
def test_an_expensive_map_outweighs_a_cheap_one():
    light, heavy = value_weights(np.array([40.0, 400.0]))
    assert heavy > light


def test_the_weight_is_capped_so_a_few_top_listings_cannot_own_the_fit():
    """Uncapped and linear, the handful of 800c+ rows dominate everything and
    the share of junk correctly identified as junk fell from 88% to 71%."""
    assert value_weights(np.array([100_000.0]))[0] == value_weights(np.array([800.0]))[0]


def test_no_weight_is_zero():
    """A cheap row still says something about shape; it just says it quietly."""
    assert (value_weights(np.array([0.5, 1.0, 5.0])) > 0).all()


# -- the training floor -----------------------------------------------------
def test_the_reroll_pile_is_held_out_of_training():
    keep, note = apply_training_floor(np.array([5.0, 20.0, 60.0, 300.0]))
    assert list(keep) == [False, False, True, True]
    assert "30c" in note


def test_a_dataset_that_is_mostly_cheap_keeps_everything():
    """A worse model over the right band still beats no model. Someone whose
    league has crashed must not open the app to an untrainable dataset."""
    prices = np.array([5.0] * 90 + [200.0] * 10)
    keep, note = apply_training_floor(prices)
    assert keep.all()
    assert "skipped" in note


def test_the_floor_says_what_it_did():
    """Silently dropping a third of someone's data would be indefensible."""
    _, note = apply_training_floor(np.array([10.0] + [100.0] * 20))
    assert "1 listings under 30c" in note


# -- the metric -------------------------------------------------------------
def test_accuracy_is_scored_only_where_it_is_spent():
    """Errors below the sell threshold must not move the headline number."""
    truth = np.array([10.0, 12.0, 100.0])
    good_where_it_counts = np.array([90.0, 90.0, 100.0])  # hopeless on junk, exact on the sale
    assert decision_metrics(truth, good_where_it_counts)["sell_mape"] == 0.0


def test_a_sellable_map_predicted_into_the_reroll_pile_is_counted():
    """The mistake that costs currency: you destroy a map worth selling."""
    m = decision_metrics(np.array([200.0, 200.0]), np.array([10.0, 200.0]))
    assert m["thrown_away_pct"] == 50.0


def test_calling_everything_expensive_is_not_rewarded():
    """The counterweight. Without it, 'predict 500c always' scores perfectly on
    the sellable band."""
    m = decision_metrics(np.array([5.0, 8.0]), np.array([500.0, 500.0]))
    assert m["junk_found_pct"] == 0.0


def test_bias_is_reported_signed():
    """Direction is the whole point -- a model that is 20% low on every good map
    costs money in a way that a model 20% high does not."""
    m = decision_metrics(np.array([100.0, 100.0]), np.array([80.0, 80.0]))
    assert m["sell_bias"] == -20.0


def test_a_slice_with_no_sellable_maps_reports_nothing_rather_than_zero():
    """A silent 0.0% would read as a perfect model."""
    assert "sell_mape" not in decision_metrics(np.array([5.0]), np.array([5.0]))


def test_the_thresholds_do_not_overlap():
    assert REROLL_BELOW_CHAOS <= WORTH_SELLING_CHAOS


# -- what the app shows -----------------------------------------------------
def test_a_price_under_the_floor_is_shown_as_an_action_not_a_number():
    """The consequence of the training floor made visible.

    The model is no longer fitted below 30c, so it hands back roughly the same
    figure for every map down there -- a 0-reward map and a 40-quantity map both
    come back around 24c. Printed as "24.6c" that reads as a cheap price rather
    than as the absence of one.
    """
    from src.ml.decision_bands import REROLL, SELL, verdict

    assert verdict(24.6) == REROLL
    assert verdict(180.0) == SELL


def test_the_marginal_band_is_named_rather_than_forced_either_way():
    """Between 30c and 50c there is no confident answer, and inventing one in
    either direction would be worse than saying so."""
    from src.ml.decision_bands import MARGINAL, verdict

    assert verdict(40.0) == MARGINAL


def test_an_unpriced_map_gets_no_verdict():
    from src.ml.decision_bands import verdict

    assert verdict(None) == ""


def test_the_priced_row_carries_the_action():
    """It has to reach the table, not just exist as a function."""
    from src.pricing.fair_value import PricedMap
    from src.ml.predict import Estimate
    from src.pricing.display import PriceFormatter
    from src.schema import NormalizedMap

    item = NormalizedMap(name="x", is_originator=True, map_tier=16, supported=True)
    est = Estimate(fair_value_chaos=24.6, interval_low_chaos=20, interval_high_chaos=40,
                   confidence="Low", confidence_score=0.2)
    row = PricedMap(item=item, estimate=est).row(PriceFormatter("chaos", 200.0))
    assert row["Action"] == "Reroll"
