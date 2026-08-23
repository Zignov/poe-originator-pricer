"""End-to-end: train a small model, serialize it, predict, explain, compare."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from src.config import Config, load_config
from src.database.repository import Repository
from src.market.comparables import find_comparables, percentile_bands, value_category
from src.ml.predict import ModelNotTrained, ModelSchemaMismatch, Predictor
from src.ml.train import train
from src.pricing.currency import CurrencyConverter
from src.pricing.fair_value import PricingService
from src.pricing.sell_strategy import (
    StrategyMultipliers,
    estimate_multipliers,
    multipliers_from_config,
    round_for_listing,
    suggested_price,
)
from src.schema import MarketListing, NormalizedMap


def _synthetic_listings(n: int = 400) -> list[MarketListing]:
    """A deterministic fake market with a known price structure.

    The point is not realism; it is that the pipeline must recover a signal it
    was given, and must not need real market data to be testable.
    """
    rng = random.Random(11)
    now = datetime.now(timezone.utc)
    listings: list[MarketListing] = []
    for index in range(n):
        currency = rng.choice([0, 0, 35, 47, 47, 94])
        maps = rng.choice([0, 0, 35, 70, 105])
        scarabs = rng.choice([0, 0, 0, 35, 36])
        cards = rng.choice([0, 0, 0, 0, 18])
        pack = rng.randint(28, 58)
        quantity = rng.randint(75, 120)
        rarity = rng.randint(40, 160)

        chaos = (
            200
            + currency * 22
            + maps * 9
            + scarabs * 12
            + cards * 30
            + pack * 6
            + quantity * 2
            + rarity * 0.5
            + currency * maps * 0.05
        )
        chaos *= rng.uniform(0.9, 1.1)

        item = NormalizedMap(
            base_type="Map (Tier 16)",
            map_tier=16,
            item_level=rng.choice([83, 84, 85]),
            is_originator=True,
            supported=True,
            quantity=quantity,
            rarity=rarity,
            pack_size=pack,
            more_currency=currency,
            more_maps=maps,
            more_scarabs=scarabs,
            more_divination_cards=cards,
        )
        listings.append(
            MarketListing(
                item=item,
                listing_id=f"synthetic-{index}",
                league="TestLeague",
                indexed_at=now - timedelta(hours=n - index),
                price_amount=chaos / 220.0,
                price_currency="divine",
                price_chaos=chaos,
                seller=f"seller{index % 60}",
                source="synthetic",
            )
        )
    return listings


@pytest.fixture
def trained(tmp_path):
    cfg = load_config()
    cfg = Config(cfg.as_dict())
    cfg.set("league", "TestLeague")
    cfg.set("database.path", str((tmp_path / "p.db").as_posix()))
    cfg.set("model.directory", str(tmp_path.as_posix()))
    cfg.set("model.backends", ["sklearn_hgb"])
    # Config resolves paths relative to the repo root; override with absolutes.
    cfg.db_path_override = tmp_path / "p.db"

    repo = Repository(tmp_path / "p.db")
    repo.set_divine_rate("TestLeague", 220.0)
    repo.upsert_listings(_synthetic_listings())
    repo.close()

    class _Cfg(Config):
        @property
        def db_path(self):
            return tmp_path / "p.db"

        @property
        def model_dir(self):
            return tmp_path

    scoped = _Cfg(cfg.as_dict())
    meta = train(scoped, min_rows=50, log=lambda *_a: None)
    return scoped, meta


def test_training_produces_a_serialized_model_with_metadata(trained):
    cfg, meta = trained
    assert (cfg.model_dir / "originator_model.pkl").exists()
    assert (cfg.model_dir / "originator_model.json").exists()
    assert meta["dataset_size"] >= 300
    assert meta["feature_list"][:7] == [
        "quantity",
        "rarity",
        "pack_size",
        "more_currency",
        "more_maps",
        "more_scarabs",
        "more_divination_cards",
    ]
    assert meta["market_date_start"] and meta["market_date_end"]
    assert "validation" in meta["metrics"] and "test" in meta["metrics"]


def test_the_model_recovers_the_signal_it_was_trained_on(trained):
    cfg, meta = trained
    assert meta["metrics"]["test"]["r2"] > 0.85
    assert meta["metrics"]["test"]["smape"] < 20


def test_prediction_output_has_a_value_an_interval_and_a_confidence(trained):
    cfg, _ = trained
    predictor = Predictor.load(cfg)
    item = NormalizedMap(
        map_tier=16, is_originator=True, supported=True,
        quantity=84, rarity=134, pack_size=32, more_currency=47, more_maps=35,
    )
    estimate = predictor.estimate(item, 220.0)
    assert estimate.fair_value_chaos > 0
    assert estimate.interval_low_chaos < estimate.fair_value_chaos < estimate.interval_high_chaos
    assert estimate.confidence in ("High", "Medium", "Low")
    assert estimate.confidence_reasons
    assert estimate.model_version


def test_more_currency_raises_the_estimate(trained):
    cfg, _ = trained
    predictor = Predictor.load(cfg)
    base = dict(map_tier=16, is_originator=True, supported=True, quantity=90, rarity=100, pack_size=40)
    low = predictor.estimate(NormalizedMap(**base, more_currency=0), 220.0)
    high = predictor.estimate(NormalizedMap(**base, more_currency=94), 220.0)
    assert high.fair_value_chaos > low.fair_value_chaos


def test_a_model_with_a_stale_feature_schema_is_refused(trained):
    import pickle

    cfg, _ = trained
    path = cfg.model_dir / "originator_model.pkl"
    with open(path, "rb") as handle:
        payload = pickle.load(handle)
    payload["metadata"]["core_features"] = ["quantity", "rarity"]
    with pytest.raises(ModelSchemaMismatch):
        Predictor(payload)


def test_loading_without_a_trained_model_raises(tmp_path):
    class _Cfg(Config):
        @property
        def model_dir(self):
            return tmp_path

    with pytest.raises(ModelNotTrained):
        Predictor.load(_Cfg(load_config().as_dict()))


def test_explanation_numbers_come_from_the_model(trained):
    from src.ml.explain import explain_item, global_importance

    cfg, _ = trained
    predictor = Predictor.load(cfg)
    item = NormalizedMap(
        map_tier=16, is_originator=True, supported=True,
        quantity=84, rarity=134, pack_size=32, more_currency=47, more_maps=35,
    )
    result = explain_item(predictor, item)
    assert result["method"] in ("shap", "ablation")
    assert result["baseline_chaos"] > 0
    assert len(result["contributions"]) == len(predictor.columns)
    predicted = predictor.predict_chaos([item], 220.0)[0]
    assert result["total_chaos"] == pytest.approx(predicted, rel=0.02)

    importance = global_importance(predictor)
    assert not importance.empty
    assert set(importance["feature"]) == set(predictor.columns)


# ---------------------------------------------------------------------------
def test_comparable_search_ranks_by_similarity(trained):
    cfg, _ = trained
    repo = Repository(cfg.db_path)
    item = NormalizedMap(
        map_tier=16, is_originator=True, supported=True,
        quantity=84, rarity=134, pack_size=32, more_currency=47, more_maps=35,
    )
    comps = find_comparables(repo, item, league="TestLeague", limit=8, max_age_days=None)
    assert len(comps) == 8
    assert comps["distance"].is_monotonic_increasing
    # the nearest comparable should share the dominant rewards
    assert comps.iloc[0]["more_currency"] == 47
    repo.close()


def test_comparables_are_empty_when_nothing_matches(tmp_path):
    repo = Repository(tmp_path / "empty.db")
    comps = find_comparables(repo, NormalizedMap(), league="Nothing")
    assert comps.empty
    repo.close()


def test_percentile_bands_are_not_hardcoded():
    cheap = percentile_bands([1, 2, 3, 4, 5])
    rich = percentile_bands([1000, 2000, 3000, 4000, 5000])
    assert cheap["high"] < rich["low"]
    assert value_category(5000, rich) == "very high"
    assert value_category(1000, rich) == "low"


# ---------------------------------------------------------------------------
def test_selling_strategies_are_ordered():
    multipliers = multipliers_from_config({"quick_sell": 0.94, "market": 0.99, "patient": 1.06})
    fair = 1000.0
    quick = suggested_price(fair, "quick_sell", multipliers)
    market = suggested_price(fair, "market", multipliers)
    patient = suggested_price(fair, "patient", multipliers)
    assert quick < market < patient


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError):
        suggested_price(100.0, "instant", multipliers_from_config({}))


def test_multipliers_are_estimated_from_the_observed_spread():
    import pandas as pd

    rng = random.Random(3)
    frame = pd.DataFrame(
        {
            "predicted_chaos": [1000.0] * 200,
            "price_chaos": [1000.0 * rng.uniform(0.8, 1.3) for _ in range(200)],
        }
    )
    fallback = multipliers_from_config({})
    observed = estimate_multipliers(frame, fallback)
    assert observed.source == "observed-spread"
    assert observed.quick_sell < observed.market < observed.patient


def test_multipliers_fall_back_when_there_is_too_little_data():
    import pandas as pd

    fallback = StrategyMultipliers(0.94, 0.99, 1.06, "config")
    frame = pd.DataFrame({"predicted_chaos": [1000.0], "price_chaos": [1100.0]})
    assert estimate_multipliers(frame, fallback).source == "config"


def test_listing_prices_are_rounded_to_something_typeable():
    assert round_for_listing(9.83) == 9.8
    assert round_for_listing(24.3) == 24.5
    assert round_for_listing(143.2) == 143.0
    assert round_for_listing(0.734) == 0.73


# ---------------------------------------------------------------------------
def test_pricing_service_prices_a_tab(trained):
    cfg, _ = trained
    repo = Repository(cfg.db_path)
    service = PricingService(cfg, repo, Predictor.load(cfg), CurrencyConverter(220.0))
    items = [
        NormalizedMap(x=0, y=0, map_tier=16, is_originator=True, supported=True,
                      quantity=84, rarity=134, pack_size=32, more_currency=47, more_maps=35),
        NormalizedMap(x=1, y=0, map_tier=16, is_originator=True, supported=False,
                      unsupported_reason="Corrupted (model scope is uncorrupted only)"),
    ]
    priced = service.price_many(items)
    assert priced[0].supported and priced[1].supported is False
    assert priced[0].suggested_chaos > 0
    assert priced[0].note.startswith("~price ")

    frame = service.to_frame(priced)
    assert list(frame["Pos"]) == ["A1", "B1"]
    assert frame.loc[1, "Status"] == "Corrupted (model scope is uncorrupted only)"
    repo.close()


def test_a_wildly_off_observed_spread_is_rejected_rather_than_applied():
    """A median ask far from the estimate means the model is miscalibrated.
    Silently multiplying by 0.2 would hide that behind a plausible number."""
    import pandas as pd

    fallback = multipliers_from_config({"quick_sell": 0.94, "market": 0.99, "patient": 1.06})
    frame = pd.DataFrame(
        {"predicted_chaos": [1000.0] * 200, "price_chaos": [170.0] * 200}
    )
    result = estimate_multipliers(frame, fallback)
    assert result.market == fallback.market
    assert "miscalibrated" in result.source


def test_a_believable_observed_spread_is_still_applied():
    import pandas as pd

    rng = random.Random(5)
    frame = pd.DataFrame(
        {
            "predicted_chaos": [1000.0] * 200,
            "price_chaos": [1000.0 * rng.uniform(0.85, 1.25) for _ in range(200)],
        }
    )
    result = estimate_multipliers(frame, multipliers_from_config({}))
    assert result.source == "observed-spread"
