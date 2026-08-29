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


def test_a_standalone_retrain_button_is_offered_beside_the_collect_buttons():
    """Training has to be reachable without spending a single request.

    "Collect and retrain" cannot serve a cancelled run: a cancel skips the
    retrain on purpose -- stopping the API calls is not a request to start a
    training run -- and the listings the run did write are then sitting under a
    model that has never seen them. Without a button that only trains, the only
    way out of that state is to collect all over again.
    """
    app = _market_data_page()
    labels = [b.label for b in app.button]
    assert "Retrain on stored data" in labels

    button = next(b for b in app.button if b.label == "Retrain on stored data")
    assert "cancelled" in (button.help or "").lower(), (
        "the help text must say that a cancelled collection's listings are "
        "included -- that is the workflow the button exists for"
    )


def test_collect_and_retrain_is_the_primary_action():
    """It is the one that leaves the app in a coherent state, so it should be
    the one the eye lands on.

    Asserted against the rendered buttons rather than against the source text.
    The original read the module and looked for `type="primary"` within 120
    characters of each label, which measured how long the comments happened to
    be as much as which button was primary -- and broke the first time a
    docstring elsewhere in the file mentioned one of the labels. The question is
    which of the three buttons the page draws as primary, so that is what is
    asked.
    """
    buttons = {b.label: b.proto.type for b in _market_data_page().button}
    assert buttons["Collect and retrain"] == "primary", (
        "the paired action is the default, not the collect-only one"
    )
    assert buttons["Collect from trade API"] != "primary"
    assert buttons["Retrain on stored data"] != "primary"


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
