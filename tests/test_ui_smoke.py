"""Every Streamlit page must render without raising.

Uses Streamlit's own AppTest harness, which executes the real script in-process,
so an exception on any page fails the suite rather than showing up as a red box
in the browser.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parent.parent / "app.py")

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

PAGES = ["Dashboard", "Stash Pricer", "Single Map", "Market Data", "Model Analysis", "Settings"]


def _run(page: str):
    app = AppTest.from_file(APP, default_timeout=120)
    app.run()
    assert not app.exception, f"{page}: exception before navigation: {app.exception}"
    radio = app.sidebar.radio[0]
    radio.set_value(page).run()
    return app


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    app = _run(page)
    assert not app.exception, f"{page} raised: {[str(e) for e in app.exception]}"


def test_single_map_prices_a_manually_entered_map():
    app = _run("Single Map")
    if not app.number_input:
        pytest.skip("no trained model, so the input form is not shown")
    for widget in app.number_input:
        if widget.key == "sm_more_currency":
            widget.set_value(47)
        elif widget.key == "sm_quantity":
            widget.set_value(90)
        elif widget.key == "sm_pack_size":
            widget.set_value(40)
    app.run()
    assert not app.exception, [str(e) for e in app.exception]


def test_stash_pricer_accepts_pasted_item_text():
    from tests.conftest import T16_UNCORRUPTED_RARE

    app = AppTest.from_file(APP, default_timeout=120)
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception

    # The source radio's options are keys; SOURCE_LABELS is only the format_func.
    app.radio[0].set_value("clipboard").run()
    assert not app.exception
    areas = [a for a in app.text_area if a.key == "clipboard_text"]
    if not areas:
        pytest.skip("clipboard text area not rendered")
    areas[0].set_value(T16_UNCORRUPTED_RARE).run()
    assert not app.exception, [str(e) for e in app.exception]
