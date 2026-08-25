"""The Collect-and-retrain button on the Market Data page.

Collecting on its own changes no price. The model is a separate artefact and
nothing retrains it on a timer, so a collection run leaves every page showing
the old numbers over newer data -- a state that is invisible unless you go
looking. Pairing the two in one button is what stops that being the default
outcome.

These tests drive the page through Streamlit's own harness, so they exercise the
real button rather than a stand-in.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parent.parent / "app.py")

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest


def _market_data_page():
    app = AppTest.from_file(APP, default_timeout=240)
    app.run()
    app.sidebar.radio[0].set_value("Market Data").run()
    assert not app.exception, app.exception
    return app


def test_both_buttons_are_offered():
    labels = [b.label for b in _market_data_page().button]
    assert "Collect from trade API" in labels
    assert "Collect and retrain" in labels


def test_collect_and_retrain_is_the_primary_action():
    """It is the one that leaves the app in a coherent state, so it should be
    the one the eye lands on."""
    import src.ui.market_data as page

    source = Path(page.__file__).read_text(encoding="utf-8")
    combined = source.index("Collect and retrain")
    assert 'type="primary"' in source[combined : combined + 200], (
        "the paired action is the default, not the collect-only one"
    )
    collect_only = source.index('"Collect from trade API"')
    assert 'type="primary"' not in source[collect_only : collect_only + 120]


def test_retraining_is_wired_to_the_real_trainer(monkeypatch):
    """Guards the wiring, not the training.

    The failure this catches is a button that collects, reports success, and
    silently never retrains -- which looks identical to a working one.
    """
    import src.ui.market_data as page

    called = {}

    def fake_train(cfg, log=print, **kwargs):
        called["ran"] = True
        log("training...")
        return {}

    monkeypatch.setattr("src.ml.train.train", fake_train)
    monkeypatch.setattr(page, "invalidate_model_cache", lambda: called.setdefault("cache", True))
    page._retrain(object())
    assert called.get("ran"), "the button must reach the trainer"


def test_a_fresh_model_is_published_to_the_other_pages(monkeypatch):
    """Training writes a new file; without invalidating the cache the rest of
    the app keeps pricing with the model it loaded at startup."""
    import src.ui.market_data as page

    called = {}
    monkeypatch.setattr("src.ml.train.train", lambda cfg, log=print, **k: {})
    monkeypatch.setattr(page, "invalidate_model_cache", lambda: called.setdefault("cache", True))
    page._retrain(object())
    assert called.get("cache")


def test_not_enough_data_is_reported_without_losing_the_collection(monkeypatch):
    """The listings were still collected. Saying only "training failed" would
    imply the whole run was wasted."""
    import src.ui.market_data as page
    from src.ml.train import NotEnoughData

    def boom(cfg, log=print, **kwargs):
        raise NotEnoughData("only 3 usable listings")

    monkeypatch.setattr("src.ml.train.train", boom)
    errors: list[str] = []
    monkeypatch.setattr(page.st, "error", lambda msg, **k: errors.append(msg))
    monkeypatch.setattr(page.st, "success", lambda msg, **k: None)
    page._retrain(object())
    assert errors and "Collected, but not retrained" in errors[0]
