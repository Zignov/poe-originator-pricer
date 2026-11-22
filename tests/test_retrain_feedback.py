"""Retraining has to change what is on screen, not just what is on disk.

The Retrain button did train, and the new model did reach the cache -- and the
app still looked exactly as it had a moment earlier, because the script run that
did the training had already rendered the page from the old model and nothing
asked Streamlit to run it again. All the user got was a green box telling them
to reload the page themselves.

These tests pin the two halves of the fix: the page must render a queued flash,
and the button must queue one and rerun rather than printing a message into a
page that is already stale.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parent.parent / "app.py")

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest


def _settings_page(tab="Model"):
    app = AppTest.from_file(APP, default_timeout=120)
    # Only the open tab renders, and Retrain lives on Model. Seeded through the
    # mirror key tab_strip() restores from.
    app.session_state["tab_strip::settings_tab"] = tab
    app.run()
    app.sidebar.radio[0].set_value("Settings").run()
    return app


def test_settings_renders_a_queued_flash():
    """A flash queued before a rerun must actually appear on Settings.

    settings.py reruns in three places already; without show_flash() every
    message queued for those reruns is silently dropped.
    """
    app = AppTest.from_file(APP, default_timeout=120)
    app.run()
    app.session_state["_flash"] = ("success", "a queued message")
    app.sidebar.radio[0].set_value("Settings").run()
    assert not app.exception, [str(e) for e in app.exception]
    shown = [e.value for e in app.success]
    assert "a queued message" in shown, f"queued flash was dropped; success shown: {shown}"


def test_retrain_button_reruns_instead_of_asking_for_a_reload(monkeypatch):
    """Pressing Retrain must leave the page showing the new model.

    train() is faked: this is about what the button does with the result, and a
    real fit would put a minute of CatBoost into the suite for no extra signal.
    """
    import src.ui.settings as settings

    trained: list[bool] = []

    def fake_train(cfg, **kwargs):
        trained.append(True)
        return {"metrics": {}}

    monkeypatch.setattr(settings, "train", fake_train)

    app = _settings_page()
    buttons = [b for b in app.button if "Retrain" in (b.label or "")]
    assert buttons, "no Retrain button on the Settings page"
    buttons[0].click().run()

    assert not app.exception, [str(e) for e in app.exception]
    assert trained, "the button did not call train()"

    messages = [e.value for e in app.success]
    assert messages, "training succeeded but the page said nothing"
    joined = " ".join(messages)
    assert "reload" not in joined.lower(), (
        f"the button still asks the user to reload the page by hand: {joined!r}"
    )


def test_model_reload_stamp_actually_varies_the_cache_key():
    """The stamp has to reach Streamlit's cache key.

    Streamlit drops parameters whose name starts with an underscore from the
    key, so `_load_predictor(_stamp)` served one cached model for every stamp
    value and the counter that invalidate_model_cache() bumps did nothing.
    """
    import inspect

    from src.ui.common import _load_catalogue, _load_predictor

    for cached in (_load_predictor, _load_catalogue):
        wrapped = getattr(cached, "__wrapped__", cached)
        names = list(inspect.signature(wrapped).parameters)
        assert names, f"{wrapped.__name__} takes no stamp at all"
        assert not names[0].startswith("_"), (
            f"{wrapped.__name__}({names[0]}) -- a leading underscore is excluded "
            "from Streamlit's cache key, so the stamp is inert"
        )
