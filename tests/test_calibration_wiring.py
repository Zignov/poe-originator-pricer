"""The personal factor reaching the prices it is supposed to move.

Measuring the factor is one thing; letting it change a suggested price is
another, and it is the half that can go wrong quietly. These drive the seam
between the two.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.database.repository import Repository
from src.pricing.sell_strategy import StrategyMultipliers, suggested_price
from src.schema import MarketListing, NormalizedMap
from src.tracking.calibration import (
    CalibrationConfig,
    PersonalCalibration,
    apply_calibration,
)
from src.tracking.history import calibration_for_account, observations_for_account
from src.tracking.reconcile import reconcile

ACCOUNT, LEAGUE = "me#1234", "Allflame"
BASE = StrategyMultipliers(quick_sell=0.94, market=0.99, patient=1.06, source="config")


def _listing(quantity: int, price: float, name="Glyph Solace"):
    item = NormalizedMap(
        name=name, base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=quantity, rarity=140,
        pack_size=30, more_currency=47,
    )
    return MarketListing(
        item=item, listing_id=f"id-{quantity}", league=LEAGUE, price_amount=price,
        price_currency="chaos", price_chaos=price, price_type="~b/o", seller=ACCOUNT,
    )


def _seed(tmp_path, n_sold=10, n_live=10, sold_price=85.0, live_price=125.0,
          predicted=100.0, model_version="model-a"):
    """A history where cheap maps sold quickly and dear ones are still sitting."""
    repo = Repository(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    listed_at = now - timedelta(days=10)

    sold = [_listing(10 + i, sold_price) for i in range(n_sold)]
    live = [_listing(500 + i, live_price) for i in range(n_live)]

    repo.apply_reconciliation(
        ACCOUNT, LEAGUE, reconcile([], sold + live, listed_at, True)
    )
    rows = repo.tracked_rows_for_page(ACCOUNT, LEAGUE)
    repo.save_tracked_predictions(
        [
            {
                "id": row["id"], "predicted_chaos": predicted,
                "suggested_chaos": predicted, "interval_low_chaos": predicted * 0.8,
                "interval_high_chaos": predicted * 1.2, "model_version": model_version,
            }
            for row in rows
        ]
    )
    # The cheap ones vanish half an hour later; the dear ones stay listed.
    repo.apply_reconciliation(
        ACCOUNT,
        LEAGUE,
        reconcile(
            repo.live_tracked_rows(ACCOUNT, LEAGUE), live,
            listed_at + timedelta(minutes=30), True,
        ),
    )
    return repo


def _cfg():
    return CalibrationConfig(min_sold=20, clamp=(0.8, 1.3))


# -- the pure seam -----------------------------------------------------
def test_an_unapplied_calibration_leaves_the_multipliers_alone():
    refused = PersonalCalibration(source="not applied: 3 usable episodes")
    out = apply_calibration(BASE, refused, enabled=True, active_model_version="model-a")
    assert out.personal == 1.0
    assert suggested_price(100.0, "market", out) == pytest.approx(99.0)
    assert "not applied" in out.personal_source


def test_an_applied_calibration_moves_the_suggested_price():
    cal = PersonalCalibration(
        factor=1.05, source="measured 1.05x", model_version="model-a",
        sample=20, applied=True,
    )
    out = apply_calibration(BASE, cal, enabled=True, active_model_version="model-a")
    assert out.personal == 1.05
    assert suggested_price(100.0, "market", out) == pytest.approx(103.95)
    assert "1.05" in out.personal_source


def test_a_measurement_from_another_model_is_not_applied():
    cal = PersonalCalibration(
        factor=1.05, source="measured 1.05x", model_version="model-a",
        sample=20, applied=True,
    )
    out = apply_calibration(BASE, cal, enabled=True, active_model_version="model-b")
    assert out.personal == 1.0
    assert "model" in out.personal_source.lower()
    assert suggested_price(100.0, "market", out) == pytest.approx(99.0)


def test_turning_it_off_in_config_stops_it_dead():
    cal = PersonalCalibration(
        factor=1.05, source="measured 1.05x", model_version="model-a",
        sample=20, applied=True,
    )
    out = apply_calibration(BASE, cal, enabled=False, active_model_version="model-a")
    assert out.personal == 1.0
    assert "off" in out.personal_source.lower()


def test_the_strategy_multipliers_themselves_are_never_touched():
    cal = PersonalCalibration(
        factor=1.2, source="m", model_version="model-a", sample=20, applied=True
    )
    out = apply_calibration(BASE, cal, enabled=True, active_model_version="model-a")
    assert (out.quick_sell, out.market, out.patient) == (0.94, 0.99, 1.06)
    assert out.source == "config", "the observed-spread provenance must survive"


# -- reading a real history -------------------------------------------
def test_a_two_sided_history_produces_an_applied_factor(tmp_path):
    repo = _seed(tmp_path)
    cal = calibration_for_account(
        repo, ACCOUNT, LEAGUE, "model-a", _cfg(), datetime.now(timezone.utc)
    )
    assert cal.applied is True
    assert cal.factor == pytest.approx(1.05, abs=0.01), (
        "sold at 0.85x, sitting at 1.25x -> the clearing ratio is bracketed at 1.05"
    )
    assert cal.model_version == "model-a"


def test_a_thin_history_refuses(tmp_path):
    repo = _seed(tmp_path, n_sold=2, n_live=2)
    cal = calibration_for_account(
        repo, ACCOUNT, LEAGUE, "model-a", _cfg(), datetime.now(timezone.utc)
    )
    assert cal.applied is False
    assert "needed" in cal.source


def test_rows_you_marked_as_not_a_sale_are_kept_out_of_the_fit(tmp_path):
    """The checkbox on the page has to actually reach the learning."""
    repo = _seed(tmp_path)
    now = datetime.now(timezone.utc)
    before = observations_for_account(repo, ACCOUNT, LEAGUE, now)

    for row in repo.tracked_rows_for_page(ACCOUNT, LEAGUE, status="gone"):
        repo.set_exclude_from_learning(row["id"], True)

    after = observations_for_account(repo, ACCOUNT, LEAGUE, now)
    assert len(after) < len(before)
    assert all(not o.sold for o in after), "every sale was excluded"


def test_excluding_the_sold_side_makes_the_factor_refuse(tmp_path):
    repo = _seed(tmp_path)
    for row in repo.tracked_rows_for_page(ACCOUNT, LEAGUE, status="gone"):
        repo.set_exclude_from_learning(row["id"], True)
    cal = calibration_for_account(
        repo, ACCOUNT, LEAGUE, "model-a", _cfg(), datetime.now(timezone.utc)
    )
    assert cal.applied is False


def test_the_tracking_version_moves_when_a_poll_records_something(tmp_path):
    """The multiplier cache is keyed on this, so it must move or prices go stale."""
    repo = Repository(tmp_path / "t.db")
    assert repo.tracking_version() == 0
    repo.apply_reconciliation(
        ACCOUNT, LEAGUE,
        reconcile([], [_listing(1, 100.0)], datetime.now(timezone.utc), True),
    )
    assert repo.tracking_version() > 0


# -- end to end, through the running app -------------------------------
def test_the_factor_reaches_a_real_suggested_price(monkeypatch, tmp_path):
    """The whole point: a measured factor must move the prices pages show.

    Seeded against the *active* model version, because a measurement taken
    against another model is deliberately refused -- so this also proves the two
    version strings actually line up in practice.
    """
    st_testing = pytest.importorskip("streamlit.testing.v1")
    import streamlit as st
    from pathlib import Path

    from src.config import Config, load_config
    from src.ml.predict import Predictor

    predictor = Predictor.load(load_config())

    db = tmp_path / "cal.db"
    repo = _seed_at(db, model_version=predictor.version)
    repo.close()

    monkeypatch.setattr(Config, "db_path", property(lambda self: db))
    monkeypatch.setenv("POE_ACCOUNT_NAME", ACCOUNT)
    st.cache_data.clear()

    app = st_testing.AppTest.from_file(
        str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=180
    )
    app.run()
    assert not app.exception, [str(e) for e in app.exception]

    sidebar = " ".join(str(m.value) for m in app.sidebar.markdown)
    assert "Your prices" in sidebar, (
        "a price scaled by your own history must say so on every page"
    )
    assert "1.05" in sidebar, sidebar[-400:]
    st.cache_data.clear()


def _seed_at(db, model_version):
    """_seed, but against an explicit database path."""
    from datetime import datetime, timedelta, timezone

    repo = Repository(db)
    now = datetime.now(timezone.utc)
    listed_at = now - timedelta(days=10)
    sold = [_listing(10 + i, 85.0) for i in range(10)]
    live = [_listing(500 + i, 125.0) for i in range(10)]
    repo.apply_reconciliation(ACCOUNT, LEAGUE, reconcile([], sold + live, listed_at, True))
    repo.save_tracked_predictions([
        {"id": row["id"], "predicted_chaos": 100.0, "suggested_chaos": 100.0,
         "interval_low_chaos": 80.0, "interval_high_chaos": 120.0,
         "model_version": model_version}
        for row in repo.tracked_rows_for_page(ACCOUNT, LEAGUE)
    ])
    repo.apply_reconciliation(
        ACCOUNT, LEAGUE,
        reconcile(repo.live_tracked_rows(ACCOUNT, LEAGUE), live,
                  listed_at + timedelta(minutes=30), True),
    )
    return repo


def test_no_history_means_no_row_and_no_scaling(monkeypatch, tmp_path):
    """Negative control for the test above: an empty history must show nothing.

    Without this, 'Your prices' appearing would prove only that the sidebar
    renders, not that the factor did anything.
    """
    st_testing = pytest.importorskip("streamlit.testing.v1")
    import streamlit as st
    from pathlib import Path

    from src.config import Config

    db = tmp_path / "empty.db"
    Repository(db).close()
    monkeypatch.setattr(Config, "db_path", property(lambda self: db))
    monkeypatch.setenv("POE_ACCOUNT_NAME", ACCOUNT)
    st.cache_data.clear()

    app = st_testing.AppTest.from_file(
        str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=180
    )
    app.run()
    assert not app.exception, [str(e) for e in app.exception]
    sidebar = " ".join(str(m.value) for m in app.sidebar.markdown)
    assert "Your prices" not in sidebar, (
        "nothing was measured, so nothing may claim to have scaled a price"
    )
    st.cache_data.clear()
