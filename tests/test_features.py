"""Feature construction, recency weighting, target transform, time splits."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.database.repository import Repository
from src.ml.features import (
    OPTIONAL_FEATURES,
    feature_columns,
    map_to_row,
    recency_weights,
    rows_to_frame,
    target_inverse,
    target_transform,
)
from src.ml.validation import metrics, rolling_time_folds, smape, time_split
from src.parsing.item_parser import classify, parse_item_text
from src.schema import FEATURE_NAMES, MarketListing
from tests.conftest import T16_UNCORRUPTED_RARE


def test_the_seven_core_features_come_first_and_in_a_fixed_order():
    assert feature_columns() == list(FEATURE_NAMES)
    assert FEATURE_NAMES == (
        "quantity",
        "rarity",
        "pack_size",
        "more_currency",
        "more_maps",
        "more_scarabs",
        "more_divination_cards",
    )


def test_optional_features_are_appended_not_interleaved():
    columns = feature_columns(["item_level", "hour_of_day"])
    assert columns[:7] == list(FEATURE_NAMES)
    assert columns[7:] == ["item_level", "hour_of_day"]


def test_unknown_optional_feature_is_rejected():
    with pytest.raises(ValueError):
        feature_columns(["moon_phase"])


def test_every_declared_optional_feature_is_accepted():
    assert feature_columns(list(OPTIONAL_FEATURES))


def test_map_to_row_matches_the_training_column_order():
    item = classify(parse_item_text(T16_UNCORRUPTED_RARE))
    row = map_to_row(item, ["item_level"])
    assert list(row.columns) == feature_columns(["item_level"])
    assert row.iloc[0]["more_currency"] == 47
    assert row.iloc[0]["item_level"] == 84


def test_features_are_never_collapsed_into_one_score():
    item = classify(parse_item_text(T16_UNCORRUPTED_RARE))
    assert len(item.feature_vector()) == 7
    assert item.feature_vector() == [84, 134, 32, 47, 35, 0, 0]


# ---------------------------------------------------------------------------
def _frame_with_ages(hours: list[float]) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    return pd.DataFrame({"observed_at": [now - timedelta(hours=h) for h in hours]})


def test_recency_weights_follow_the_schedule():
    schedule = [[12, 1.0], [24, 0.9], [72, 0.7], [168, 0.4], [999999, 0.2]]
    frame = _frame_with_ages([1, 18, 48, 100, 500])
    weights = recency_weights(frame["observed_at"], schedule)
    assert list(weights) == [1.0, 0.9, 0.7, 0.4, 0.2]


def test_a_flat_schedule_weights_everything_equally():
    weights = recency_weights(_frame_with_ages([1, 500])["observed_at"], [[999999, 1.0]])
    assert set(weights) == {1.0}


def test_target_transform_round_trips():
    values = np.array([0.0, 1.0, 250.0, 100000.0])
    assert np.allclose(target_inverse(target_transform(values, "log1p"), "log1p"), values)
    assert np.allclose(target_inverse(target_transform(values, "raw"), "raw"), values)


def test_unknown_target_mode_is_rejected():
    with pytest.raises(ValueError):
        target_transform(np.array([1.0]), "sqrt")


# ---------------------------------------------------------------------------
def test_time_split_is_chronological_and_does_not_overlap():
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"observed_at": [now - timedelta(hours=h) for h in range(100, 0, -1)]})
    train, valid, test = time_split(frame)
    assert len(train) == 70 and len(valid) == 15 and len(test) == 15
    assert frame.loc[train, "observed_at"].max() <= frame.loc[valid, "observed_at"].min()
    assert frame.loc[valid, "observed_at"].max() <= frame.loc[test, "observed_at"].min()
    assert set(train) & set(test) == set()


def test_rolling_folds_always_train_on_the_past():
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"observed_at": [now - timedelta(hours=h) for h in range(200, 0, -1)]})
    folds = rolling_time_folds(frame, folds=4)
    assert len(folds) == 4
    for train_idx, eval_idx in folds:
        assert frame.loc[train_idx, "observed_at"].max() <= frame.loc[eval_idx, "observed_at"].min()


def test_metrics_report_the_required_set():
    y_true = np.array([100.0, 200.0, 400.0, 800.0])
    y_pred = np.array([110.0, 190.0, 420.0, 700.0])
    result = metrics(y_true, y_pred)
    for key in ("mae", "median_ae", "r2", "smape"):
        assert key in result
    assert result["mae"] == pytest.approx(35.0)


def test_smape_is_finite_when_a_prediction_is_zero():
    assert np.isfinite(smape(np.array([100.0]), np.array([0.0])))


def test_mape_is_omitted_when_an_actual_is_zero():
    assert "mape" not in metrics(np.array([0.0, 100.0]), np.array([1.0, 100.0]))


# ---------------------------------------------------------------------------
def test_rows_to_frame_builds_all_seven_columns(tmp_path):
    repo = Repository(tmp_path / "f.db")
    item = classify(parse_item_text(T16_UNCORRUPTED_RARE))
    repo.upsert_listings(
        [
            MarketListing(
                item=item,
                listing_id="a",
                league="Allflame",
                price_amount=9.0,
                price_currency="divine",
                price_chaos=1980.0,
                indexed_at=datetime.now(timezone.utc),
            )
        ]
    )
    frame = rows_to_frame(repo.listing_rows(), divine_to_chaos=220.0)
    for name in FEATURE_NAMES:
        assert name in frame.columns
    assert frame.iloc[0]["more_currency"] == 47
    assert frame.iloc[0]["price_chaos"] == 1980.0
    assert "hour_of_day" in frame.columns
    repo.close()


def test_training_timestamps_are_unix_seconds():
    """Regression: pandas 2 stores datetimes in ns and pandas 3 in us, so
    `astype("int64") // 10**9` is silently 1000x wrong on one of them."""
    import time

    from src.ml.train import _unix_seconds

    now = datetime.now(timezone.utc)
    seconds = _unix_seconds(pd.Series([now, now - timedelta(days=1)]))
    assert abs(int(seconds[0]) - time.time()) < 5
    assert int(seconds[0]) - int(seconds[1]) == pytest.approx(86400, abs=2)


def test_bulk_dumps_are_down_weighted():
    """A seller listing a whole tab at one flat price contributes many rows in
    which the price never moves while the rewards vary -- evidence against the
    rewards mattering. One such block must not outvote independent sellers."""
    from src.ml.features import bulk_listing_weights

    frame = pd.DataFrame(
        {
            "seller": ["whale"] * 100 + [f"person{i}" for i in range(10)],
            "price_chaos": [40.0] * 100 + [50.0 + i for i in range(10)],
        }
    )
    weights = bulk_listing_weights(frame)
    assert weights[:100].sum() == pytest.approx(10.0)   # 100 rows count as 10
    assert weights[100:].sum() == pytest.approx(10.0)   # 10 independent count as 10
    assert weights[0] < weights[-1]


def test_bulk_weighting_leaves_an_unconcentrated_dataset_alone():
    from src.ml.features import bulk_listing_weights

    frame = pd.DataFrame(
        {"seller": [f"p{i}" for i in range(20)], "price_chaos": [float(i) for i in range(20)]}
    )
    assert set(bulk_listing_weights(frame)) == {1.0}


def test_bulk_weighting_survives_missing_columns():
    from src.ml.features import bulk_listing_weights

    assert list(bulk_listing_weights(pd.DataFrame({"x": [1, 2]}))) == [1.0, 1.0]
    assert len(bulk_listing_weights(pd.DataFrame())) == 0


def test_a_single_snapshot_is_recognised_as_such():
    """indexed_at means different things depending on how the data was gathered:
    an observation time across sessions, but mere staleness within one."""
    from src.ml.validation import is_snapshot

    now = datetime.now(timezone.utc)
    snapshot = pd.DataFrame({"captured_at": [now - timedelta(minutes=m) for m in range(200)]})
    assert is_snapshot(snapshot) is True

    over_days = pd.DataFrame({"captured_at": [now - timedelta(days=d) for d in range(6)]})
    assert is_snapshot(over_days) is False


def test_grouped_split_never_puts_one_seller_on_both_sides():
    """A seller listing several maps at one flat price would otherwise let the
    model score well by recognising the seller rather than the rewards."""
    from src.ml.validation import grouped_split

    frame = pd.DataFrame({"seller": [f"s{i // 5}" for i in range(200)]})
    train, valid, test = grouped_split(frame)
    assert len(train) + len(valid) + len(test) == len(frame)
    sellers = lambda idx: set(frame.loc[idx, "seller"])  # noqa: E731
    assert not sellers(train) & sellers(test)
    assert not sellers(train) & sellers(valid)
    assert not sellers(valid) & sellers(test)


def test_choose_split_reports_which_one_it_used():
    from src.ml.validation import choose_split

    now = datetime.now(timezone.utc)
    snapshot = pd.DataFrame(
        {
            "captured_at": [now] * 100,
            "observed_at": [now - timedelta(hours=h) for h in range(100)],
            "seller": [f"s{i}" for i in range(100)],
        }
    )
    *_, kind = choose_split(snapshot)
    assert "snapshot" in kind

    spread = pd.DataFrame(
        {
            "captured_at": [now - timedelta(days=d % 5) for d in range(100)],
            "observed_at": [now - timedelta(hours=h) for h in range(100)],
            "seller": [f"s{i}" for i in range(100)],
        }
    )
    *_, kind = choose_split(spread)
    assert "chronological" in kind


def test_grouped_split_falls_back_when_there_are_too_few_groups():
    """One group means everything lands on one side and training gets nothing."""
    from src.ml.validation import grouped_split

    frame = pd.DataFrame({"seller": [None] * 120})
    train, valid, test = grouped_split(frame)
    assert len(train) > 0 and len(valid) > 0 and len(test) > 0
    assert len(train) + len(valid) + len(test) == len(frame)
