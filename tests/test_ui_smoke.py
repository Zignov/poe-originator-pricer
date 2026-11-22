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
    "My Listings",
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


# ---------------------------------------------------------------------------
# Tab strips: the tabs that remember which one is open
# ---------------------------------------------------------------------------

#: The pages whose tabs used to be `st.tabs`, which rendered every pane on every
#: rerun. They are `tab_strip` now, which renders only the open one -- so a page
#: that renders says nothing about the other panels, and the two tests below
#: have to walk them to say what the two above used to say on their own.
TAB_STRIPS = [
    ("Market Data", "market_data_tab"),
    ("Model Analysis", "model_analysis_tab"),
]


def _tab_names(app):
    """The labels of the tabs on screen, in order.

    `tab_strip` is a real `st.tabs` since Streamlit 1.67, and AppTest's `Tab`
    element carries a label but no key, value or options -- so a strip is found
    by its labels and driven through session state rather than by a widget
    handle. Both pages walked below draw exactly one strip.
    """
    return [str(tab.label) for tab in app.tabs]


def _open_tab(app, key, name):
    """Open one tab by name and rerun.

    Assigning the widget key is what a click does; `tab_strip` mirrors it on
    the way back out, so the choice then survives leaving the page.
    """
    app.session_state[key] = name
    app.run()


def _walk_tabs(app, key):
    """Open each tab of the named strip in turn. Returns the ones it opened.

    Empty when the page drew no strip at all, which is a real state for both of
    these pages -- no trained model, and Model Analysis stops at a warning.
    """
    names = _tab_names(app)
    if not names:
        return []
    opened = []
    for name in names:
        _open_tab(app, key, name)
        assert not app.exception, f"{name} raised: {[str(e) for e in app.exception]}"
        opened.append(name)
    return opened


@pytest.mark.parametrize("page,key", TAB_STRIPS)
def test_every_tab_of_a_strip_renders(page, key):
    app = _run(page)
    if not _walk_tabs(app, key):
        pytest.skip(f"{page} drew no tab strip")


@pytest.mark.parametrize("page,key", TAB_STRIPS)
def test_every_tab_of_a_strip_renders_on_a_fresh_install(page, key, monkeypatch, tmp_path):
    """The empty-database case, tab by tab. See the fresh-install test above."""
    from src.config import Config

    empty = tmp_path / "empty.db"
    monkeypatch.setattr(Config, "db_path", property(lambda self: empty))

    app = _run(page)
    if not _walk_tabs(app, key):
        pytest.skip(f"{page} drew no tab strip on a fresh install")


def test_the_open_tab_survives_a_trip_to_another_page():
    """The complaint: st.tabs re-opened the first tab every time you came back.

    Leaving Market Data to check a price and returning put you on Collect again
    whatever you had been reading, which made the nav cost something every time
    it was used. Streamlit also drops a keyed widget's value as soon as one
    rerun goes by without that widget on screen -- which is exactly what leaving
    the page is -- so remembering has to survive that too, not merely a rerun.
    """
    app = _run("Market Data")
    assert "Import" in _tab_names(app), "the Market Data page drew no tab strip"
    _open_tab(app, "market_data_tab", "Import")
    assert not app.exception, [str(e) for e in app.exception]

    app.sidebar.radio[0].set_value("Dashboard").run()
    app.sidebar.radio[0].set_value("Market Data").run()
    assert not app.exception, [str(e) for e in app.exception]

    assert _tab_names(app), "the tab strip did not come back with the page"
    # Both halves matter. The mirror is what survived the trip; the widget key
    # is what the strip is actually showing now. A bug that restored the mirror
    # but reopened the first tab anyway -- which is what a plain keyed st.tabs
    # does on 1.67 -- passes the first and fails the second.
    assert app.session_state["tab_strip::market_data_tab"] == "Import"
    assert app.session_state["market_data_tab"] == "Import", (
        f"came back on {app.session_state['market_data_tab']!r} "
        "rather than the tab that was open"
    )


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


def test_the_inventory_source_is_offered_and_renders_without_credentials():
    """Picking it with no POESESSID must explain itself, not raise."""
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    # The source radio's options are keys; SOURCE_LABELS is only the format_func.
    app.radio[0].set_value("inventory").run()
    assert not app.exception, f"inventory source raised: {[str(e) for e in app.exception]}"
    said = " ".join(str(w.value) for w in app.warning) + " ".join(
        str(c.value) for c in app.caption
    )
    assert "POESESSID" in said


def test_the_overlay_panel_can_target_either_surface():
    """Both profiles must be reachable from the page, and neither may raise."""
    from src.overlay.profiles import INVENTORY_PROFILE, STASH_PROFILE

    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()

    targets = [r for r in app.radio if "Draw over" in str(r.label)]
    if not targets:
        pytest.skip("no trained model, so the overlay panel is not shown")

    labels = [str(o) for o in targets[0].options]
    assert any(INVENTORY_PROFILE.label in label for label in labels), labels
    assert any(STASH_PROFILE.label in label for label in labels), labels

    targets[0].set_value(INVENTORY_PROFILE).run()
    assert not app.exception, f"inventory target raised: {[str(e) for e in app.exception]}"
    buttons = [str(b.label) for b in app.button]
    assert any("inventory" in b.lower() for b in buttons), buttons


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


def test_every_tile_says_how_many_affix_slots_it_has_open():
    """The dots cannot tell "full" from "unreadable"; the tooltip has to.

    Both draw an empty corner, which is safe -- neither invites an Exalted Orb --
    but it leaves the tile unable to say which it is, so every tile carries the
    answer in words as well.
    """
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"

    html = _grid_html(app)
    assert html, "no grid rendered"
    filled = html.count("stash-cell filled ")
    assert filled, "no filled tiles"
    assert html.count("Free affix slots:") >= filled


def test_the_free_affix_legend_appears_exactly_when_a_badge_does():
    """A key for a mark nobody can see is one more thing to read past.

    Most stash payloads carry no affix structure at all, so on those tabs every
    tile is badgeless -- and the legend has to stay away rather than explaining
    dots that are not there.
    """
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"

    html = _grid_html(app)
    assert html, "no grid rendered"
    drew_a_badge = 'class="free-affix"' in html
    explained = any("One dot per affix slot" in str(c.value) for c in app.caption)
    assert explained == drew_a_badge


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

# ---------------------------------------------------------------------------
# The market run's escape hatch, on the page rather than in the pricer
# ---------------------------------------------------------------------------


def _radio_labels(node):
    """Labels of every radio at or below `node`."""
    out = []
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        children = list(children.values())
    for child in children or []:
        if type(child).__name__ == "Radio":
            out.append(str(getattr(child, "label", "")))
        out.extend(_radio_labels(child))
    return out


def test_the_source_picker_is_not_hidden_once_a_tab_is_loaded():
    """You cannot choose a source you cannot see.

    The picker used to sit inside the fold with the source-specific inputs, so
    the moment a tab was loaded the fold closed over all six options. The one
    that suffered was the newest -- reading a character's inventory looked like
    a feature with no button, because nothing on the page said it existed.
    """
    app = _stash_pricer_with_a_tab()
    hidden = [
        label
        for expander in _blocks_of_type(app.main, "expander")
        for label in _radio_labels(expander)
        if "come from" in label
    ]
    assert not hidden, f"the source picker folded away: {hidden}"
    assert any("come from" in label for label in _radio_labels(app.main))


def test_choosing_the_inventory_renames_the_primary_button():
    """"Analyze" is not what you look for when you want to read your backpack."""
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["poesessid"] = "x" * 32
    app.session_state["account_name"] = "Exile#1234"
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    app.radio[0].set_value("inventory").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"

    labels = _button_labels(app.main)
    assert "Read inventory" in labels, labels
    # And the way in is on the page, not behind the fold.
    assert "Load character list" in labels, labels


def test_the_inventory_option_survives_a_loaded_tab():
    """The whole complaint: a loaded tab must not hide the inventory source."""
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.session_state["poesessid"] = "x" * 32
    app.session_state["account_name"] = "Exile#1234"
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()

    source = [r for r in app.radio if "come from" in str(r.label)]
    assert source, "no source picker on a page with a tab loaded"
    offered = [str(o) for o in source[0].options]
    assert any("inventory" in o.lower() for o in offered), offered

    source[0].set_value("inventory").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"
    assert "Read inventory" in _button_labels(app.main)

def _held_open_job(items):
    """A real `MarketRunJob` parked on a thread that will not finish on its own.

    Faked at the thread rather than at the handle so the page sees exactly the
    object it sees in production -- `running`, `cancelling` and `state` all
    answering for real. The caller must set the returned event.
    """
    import threading

    from src.market.live_comparables import MapOutcome, MarketProgress, MarketRunState
    from src.market.run_job import MarketRunJob

    release = threading.Event()
    state = MarketRunState()
    state.record(MapOutcome(fingerprint=items[0].fingerprint(), reason="unsupported"))
    job = MarketRunJob(state=state, items=list(items))
    job.progress = MarketProgress(
        done=1,
        total=len(items),
        requests_used=4,
        request_budget=200,
        eta_seconds=90.0,
        message="pricing",
    )
    job._thread = threading.Thread(target=release.wait, daemon=True)
    job._thread.start()
    return job, release


def test_a_run_in_flight_offers_a_cancel_button_on_the_page():
    """The whole point: a run you can stop, from a page that is still drawing."""
    app = AppTest.from_file(APP, default_timeout=240)
    items = _demo_items()
    app.session_state["stash_items"] = items
    job, release = _held_open_job(items)
    app.session_state["market_run_job"] = job
    app.session_state["pricing_mode"] = "market"
    try:
        app.run()
        app.sidebar.radio[0].set_value("Stash Pricer").run()
        assert not app.exception, f"{[str(e) for e in app.exception]}"

        labels = [str(b.label) for b in app.button]
        assert "Cancel run" in labels, labels
        assert "Show results so far" in labels, labels
        # And the run has not been started over or restarted by the redraw.
        assert app.session_state["market_run_job"] is job
        assert not job.cancelled
    finally:
        release.set()
        job.join(timeout=5)


def test_the_page_never_offers_cancel_when_nothing_is_running():
    app = AppTest.from_file(APP, default_timeout=240)
    app.session_state["stash_items"] = _demo_items()
    app.session_state["pricing_mode"] = "market"
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    assert not app.exception, f"{[str(e) for e in app.exception]}"

    labels = [str(b.label) for b in app.button]
    assert "Cancel run" not in labels, labels
    assert "Price from live listings" in labels, labels


def test_the_nav_group_headings_sit_on_the_right_rows():
    """app.py's PAGES order and common.py's nth-child selectors must agree.

    The group headings are drawn by CSS keyed on position rather than by
    splitting the picker into three radios, so inserting a page shifts every
    heading below it. app.py says so in a comment; this makes the comment fail
    the build instead of being read after the fact.
    """
    import re

    from app import PAGES as ORDER

    root = Path(__file__).resolve().parent.parent
    css = (root / "src" / "ui" / "common.py").read_text(encoding="utf-8")
    headings = dict(
        (int(pos), label)
        for pos, label in re.findall(
            r'nth-child\((\d+)\)::before \{ content: "([^"]+)"', css
        )
    )
    names = list(ORDER)
    expected = {
        names.index("Dashboard") + 1: "Price",
        names.index("Market Data") + 1: "Market",
        names.index("Model Analysis") + 1: "Tool",
    }
    assert headings == expected
