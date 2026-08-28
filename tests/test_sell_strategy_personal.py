"""The personal factor composes with the strategy, and never hides itself."""
from __future__ import annotations

import pytest

from src.pricing.sell_strategy import StrategyMultipliers, suggested_price


def _multipliers(personal=1.0, source=""):
    return StrategyMultipliers(
        quick_sell=0.94, market=0.99, patient=1.06, source="config",
        personal=personal, personal_source=source,
    )


def test_without_a_personal_factor_nothing_changes():
    assert suggested_price(100.0, "market", _multipliers()) == pytest.approx(99.0)


def test_the_personal_factor_scales_the_suggested_price():
    m = _multipliers(personal=1.1, source="measured 1.10x")
    assert suggested_price(100.0, "market", m) == pytest.approx(108.9)


def test_it_composes_with_every_strategy():
    m = _multipliers(personal=0.9)
    assert suggested_price(100.0, "quick_sell", m) == pytest.approx(84.6)
    assert suggested_price(100.0, "patient", m) == pytest.approx(95.4)


def test_the_source_survives_into_as_dict_so_the_ui_can_show_it():
    m = _multipliers(personal=1.1, source="measured 1.10x against catboost-2026-08-24")
    payload = m.as_dict()
    assert payload["personal"] == 1.1
    assert "catboost" in payload["personal_source"]


def test_personal_is_not_selectable_as_a_strategy():
    with pytest.raises(ValueError):
        suggested_price(100.0, "personal", _multipliers())


def test_an_unknown_strategy_still_raises():
    with pytest.raises(ValueError):
        suggested_price(100.0, "nonsense", _multipliers())


def test_the_defaults_leave_every_existing_caller_untouched():
    """Constructed the old way, with no personal factor mentioned at all."""
    m = StrategyMultipliers(quick_sell=0.94, market=0.99, patient=1.06, source="config")
    assert m.personal == 1.0
    assert suggested_price(100.0, "market", m) == pytest.approx(99.0)
