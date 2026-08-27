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

PAGES = [
    "Dashboard",
    "Stash Pricer",
    "Single Map",
    "Market Data",
    "Bulk Buy",
    "Model Analysis",
    "Settings",
]


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


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_on_a_fresh_install(page, monkeypatch, tmp_path):
    """Trained model present, listings database empty.

    That combination is not hypothetical: it is exactly what a downloaded
    release is on its first run, because the zip ships the model but not the
    33 MB database. It is also the one configuration the plain smoke test above
    never sees on a developer machine, where data/originator.db always exists --
    which is how Model Analysis shipped a KeyError on `price_chaos` that only
    the empty case could reach.
    """
    from src.config import Config

    empty = tmp_path / "empty.db"
    monkeypatch.setattr(Config, "db_path", property(lambda self: empty))

    app = _run(page)
    assert not app.exception, f"{page} raised on a fresh install: {[str(e) for e in app.exception]}"


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


def _demo_items():
    """The maps from data/demo_stash.json, parsed the way a stash source would."""
    import json

    from src.parsing.item_parser import classify, parse_api_item

    payload = json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "demo_stash.json").read_text(
            encoding="utf-8"
        )
    )
    return [
        classify(parse_api_item(item), map_tier=16, require_uncorrupted=True)
        for item in payload["items"]
    ]


def test_stash_pricer_renders_the_exalt_panel_for_real_maps():
    """The affix column and exalt verdict only execute when a tab is loaded."""
    app = AppTest.from_file(APP, default_timeout=180)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    assert not app.exception, f"before navigation: {app.exception}"
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"Stash Pricer raised: {[str(e) for e in app.exception]}"

    rendered = " ".join(str(m.value) for m in app.markdown) + " ".join(
        str(c.value) for c in app.caption
    )
    assert "Exalting this map" in rendered


def test_the_exaltable_filter_does_not_break_the_page():
    app = AppTest.from_file(APP, default_timeout=180)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    boxes = [c for c in app.checkbox if "Exaltable" in str(c.label)]
    if not boxes:
        pytest.skip("no trained model, so the controls are not shown")
    boxes[0].set_value(True).run()
    assert not app.exception, f"raised with the filter on: {[str(e) for e in app.exception]}"


def _grid_html(app):
    """The rendered stash-grid tiles, which carry the price on each one.

    Matches "stash-cell filled" rather than "stash-grid": the latter also appears
    in the page stylesheet, which never changes, so matching it compares the CSS
    to itself and passes no matter what the tiles say.
    """
    return next(
        (str(m.value) for m in app.markdown if "stash-cell filled" in str(m.value)), ""
    )


def test_the_stash_grid_follows_the_selling_strategy():
    """The grid used to paint fair value, so the strategy selector looked inert."""
    seen = {}
    for strategy in ("quick_sell", "market", "patient"):
        app = AppTest.from_file(APP, default_timeout=240)
        app.session_state["stash_items"] = _demo_items()
        app.run()
        app.sidebar.radio[0].set_value("Stash Pricer").run()
        boxes = [s for s in app.selectbox if "Selling strategy" in str(s.label)]
        if not boxes:
            pytest.skip("no trained model, so the controls are not shown")
        boxes[0].set_value(strategy).run()
        assert not app.exception, f"{strategy}: {[str(e) for e in app.exception]}"
        seen[strategy] = _grid_html(app)
        assert seen[strategy], f"{strategy}: no grid rendered"

    assert seen["quick_sell"] != seen["market"] != seen["patient"], (
        "the grid did not change between selling strategies"
    )
    assert seen["quick_sell"] != seen["patient"]


def test_the_grid_marks_exaltable_maps_with_a_ring():
    """Rings must actually reach the rendered tiles, not just the helper."""
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"

    html = _grid_html(app)
    assert html, "no grid rendered"
    rings = html.count("exalt-worth") + html.count("exalt-skip")
    plain = html.count("stash-cell filled ")
    assert plain, "no filled tiles"
    # Every demo map is full, corrupted or unreadable, so none should be ringed;
    # what matters is that the class machinery runs without marking those.
    assert rings <= plain, "more rings than tiles"


def test_every_priced_tile_carries_a_confidence_meter():
    """The meter has to reach the rendered tiles, not just the helper."""
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"

    html = _grid_html(app)
    assert html, "no grid rendered"
    filled = html.count("stash-cell filled ")
    assert filled, "no filled tiles"
    assert html.count('<span class="conf-meter">') == filled


def test_the_legend_explains_the_confidence_meter():
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    rendered = " ".join(str(m.value) for m in app.markdown)
    for level in ("high confidence", "medium confidence", "low confidence"):
        assert level in rendered


def test_the_legend_explains_both_rings():
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    # Search every markdown block: the page stylesheet also contains the string
    # "legend", so picking the first match tests the CSS rather than the key.
    rendered = " ".join(str(m.value) for m in app.markdown)
    assert "worth an exalt" in rendered
    assert "not worth the orb" in rendered
    assert "cannot be exalted" in rendered


def test_the_market_comparison_mode_renders_without_calling_the_api():
    """Switching to the slow mode must not start it.

    The whole point of the warning and the button is that a page rerun -- which
    Streamlit does on every widget touch -- costs nothing. A mode that fired a
    search on render would spend a tab's worth of requests before the user had
    agreed to any of them.
    """
    import src.market.collectors.official_trade_api as trade_api

    calls: list[str] = []
    original = trade_api.OfficialTradeApiCollector.search
    trade_api.OfficialTradeApiCollector.search = lambda self, query: calls.append("search")
    try:
        app = AppTest.from_file(APP, default_timeout=240)
        app.session_state["stash_items"] = _demo_items()
        app.run()
        app.sidebar.radio[0].set_value("Stash Pricer").run()
        modes = [r for r in app.radio if "Pricing mode" in str(r.label)]
        if not modes:
            pytest.skip("mode selector not rendered")
        modes[0].set_value("market").run()
        assert not app.exception, f"market mode raised: {[str(e) for e in app.exception]}"
        rendered = " ".join(str(m.value) for m in app.markdown)
        assert "calls the official trade API" in rendered, "no warning before the cost"
    finally:
        trade_api.OfficialTradeApiCollector.search = original

    assert calls == [], "the market mode called the trade API without being asked to"


def _blocks_of_type(node, kind, found=None):
    """Every block of `kind` in the rendered tree, depth-first."""
    found = [] if found is None else found
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        children = list(children.values())
    for child in children or []:
        if getattr(child, "type", "") == kind:
            found.append(child)
        _blocks_of_type(child, kind, found)
    return found


def _button_labels(node):
    """Labels of every button at or below `node`."""
    out = []
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        children = list(children.values())
    for child in children or []:
        if type(child).__name__ == "Button":
            out.append(str(getattr(child, "label", "")))
        out.extend(_button_labels(child))
    return out


def _stash_pricer_with_a_tab():
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"Stash Pricer raised: {[str(e) for e in app.exception]}"
    return app


def test_analyze_is_on_the_page_once_a_tab_is_loaded():
    """The button the page exists for must not vanish the moment it is used.

    With a tab loaded the source picker folds away and Analyze used to fold with
    it -- or, when the default source could not reach the stash API, never
    render at all. Either way the most important control on the page was gone
    exactly when re-reading the tab was the obvious next thing to do.
    """
    app = _stash_pricer_with_a_tab()
    labels = _button_labels(app.main)
    assert any("Analyze" in label for label in labels), labels


def test_analyze_does_not_live_inside_the_folding_picker():
    """Rendered outside the expander, or "always visible" lasts exactly one click.

    Streamlit runs an expander's body whether or not it is open, so a button
    inside one is present to this harness and invisible to the user. The
    assertion has to be about where it sits, not merely that it exists.
    """
    app = _stash_pricer_with_a_tab()
    hidden = [
        label
        for expander in _blocks_of_type(app.main, "expander")
        for label in _button_labels(expander)
        if "Analyze" in label
    ]
    assert not hidden, f"Analyze rendered inside the collapsed picker: {hidden}"
