"""The stale-listing view: which maps it selects, and what it lights up in game."""
from __future__ import annotations

#: Taken from the real config rather than hardcoded, because these tests run
#: the actual app.py, which reads config.yaml and queries for whatever league
#: is set there. A literal "Allflame" here meant the seeded rows stopped being
#: found the moment the user switched to a new league -- a test failing on a
#: config change rather than on a code change.
from src.config import load_config as _load_config

LEAGUE = _load_config().get("league") or "Standard"

from datetime import datetime, timedelta, timezone

import pytest

from src.ui.my_listings import (
    overlay_groups,
    overlay_state_for_tab,
    regex_for_stale,
    stale_rows,
)

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _row(name, hours, price=150.0, predicted=140.0, low=120.0, **extra):
    row = {
        "id": abs(hash(name)) % 10_000,
        "name": name,
        "base_type": "Terror Solace",
        "price_chaos": price,
        "predicted_chaos": predicted,
        "interval_low_chaos": low,
        "priced_at": (T0 - timedelta(hours=hours)).isoformat(),
        "stash_name": "Sell 3",
        "stash_x": 1,
        "stash_y": 2,
        "ambiguous": 0,
        "exclude_from_learning": 0,
    }
    row.update(extra)
    return row


def test_only_maps_past_the_threshold_are_stale():
    rows = [_row("Glyph Solace", 3), _row("Rune Path", 30), _row("Bleak Lands", 200)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    assert [s["name"] for s in stale] == ["Bleak Lands", "Rune Path"], "oldest first"


def test_each_stale_row_carries_hours_and_a_recommendation():
    stale = stale_rows([_row("Rune Path", 30)], now=T0, stale_after_hours=24)
    assert stale[0]["hours_listed"] == 30
    assert stale[0]["recommendation"].price_chaos < 150.0
    assert stale[0]["recommendation"].source == "ladder"


def test_the_regex_lights_the_stale_maps_and_not_the_fresh_ones():
    rows = [_row("Glyph Solace", 3), _row("Rune Path", 30)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    batches = regex_for_stale(stale, rows)
    assert batches.ok
    # The game's search is case-insensitive, and the builder emits lowercase.
    pattern = batches.pages[0].pattern.lower()
    assert "rune" in pattern or "path" in pattern
    assert "glyph" not in pattern and "solace" not in pattern


def test_no_stale_maps_produces_no_pattern():
    rows = [_row("Glyph Solace", 3)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    assert stale == []
    assert not regex_for_stale(stale, rows).ok


def test_overlay_groups_split_by_tab_because_the_overlay_draws_one_grid():
    rows = [
        _row("Rune Path", 30, stash_name="Sell 3"),
        _row("Bleak Lands", 40, stash_name="Sell 4"),
        _row("Crooked Path", 50, stash_name="Sell 3"),
    ]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    groups = overlay_groups(stale)
    assert sorted(groups) == ["Sell 3", "Sell 4"]
    assert len(groups["Sell 3"]) == 2


def test_a_listing_without_coordinates_is_unplaced_not_guessed():
    rows = [_row("Rune Path", 30, stash_x=None, stash_y=None)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    groups = overlay_groups(stale)
    assert groups == {}, "no cell is better than a guessed cell"


def test_the_overlay_tile_shows_the_recommended_price_not_the_current_ask():
    from src.pricing.display import PriceFormatter

    rows = [_row("Rune Path", 30, price=150.0, predicted=140.0, low=100.0)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    state = overlay_state_for_tab(stale, "Sell 3", PriceFormatter(unit="chaos"), "Allflame")

    assert len(state.tiles) == 1
    tile = state.tiles[0]
    assert (tile.x, tile.y) == (1, 2)
    assert tile.price_chaos == stale[0]["recommendation"].price_chaos
    assert tile.price_chaos < 150.0, "the point is to show what to change it to"
    assert state.tab_names == ("Sell 3",)


def test_the_overlay_export_is_not_marked_as_a_repeatable_stash_read():
    """Analyze re-reads a tab and re-prices it, which would wipe these numbers."""
    from src.pricing.display import PriceFormatter

    stale = stale_rows([_row("Rune Path", 30)], now=T0, stale_after_hours=24)
    state = overlay_state_for_tab(stale, "Sell 3", PriceFormatter(unit="chaos"), "Allflame")
    assert not state.source.repeatable


def test_the_page_body_renders_with_real_tracked_rows(monkeypatch, tmp_path):
    """The plain smoke test has no account name, so it stops at the early
    return and never reaches the table, the regex or the overlay block. This
    drives the actual body."""
    import pytest

    st_testing = pytest.importorskip("streamlit.testing.v1")
    from pathlib import Path

    from src.config import Config
    from src.database.repository import Repository
    from src.schema import MarketListing, NormalizedMap
    from src.tracking.reconcile import reconcile

    db = tmp_path / "tracked.db"
    monkeypatch.setattr(Config, "db_path", property(lambda self: db))
    monkeypatch.setenv("POE_ACCOUNT_NAME", "me#1234")

    repo = Repository(db)
    old = datetime.now(timezone.utc) - timedelta(hours=60)
    item = NormalizedMap(
        name="Rune Path", base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=90, rarity=140, pack_size=30,
    )
    listing = MarketListing(
        item=item, listing_id="abc", league=LEAGUE, price_amount=150.0,
        price_currency="chaos", price_chaos=150.0, price_type="~b/o", seller="me#1234",
        raw_data={"listing": {"stash": {"name": "Sell 3", "x": 1, "y": 2}}},
    )
    result = reconcile([], [listing], now=old, complete=True)
    repo.apply_reconciliation("me#1234", LEAGUE, result)
    repo.close()

    app = st_testing.AppTest.from_file(
        str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=120
    )
    app.run()
    app.sidebar.radio[0].set_value("My Listings").run()
    assert not app.exception, [str(e) for e in app.exception]

    # Prove it got past the "set your account name" early return: the staleness
    # control and the in-game search block only exist in the body.
    body = " ".join(str(m.value) for m in app.markdown) + " ".join(
        str(c.value) for c in app.code
    )
    assert app.select_slider, "the staleness control never rendered"
    assert "Find them in game" in body, body[:400]


def test_a_sold_row_carries_its_bracket_not_a_time():
    from src.ui.my_listings import alert_rows

    sold = [{
        "id": 1, "name": "Rune Path", "price_chaos": 90.0,
        "predicted_chaos": 120.0, "status": "gone",
        "priced_at": T0.isoformat(),
        "last_seen": (T0 + timedelta(minutes=20)).isoformat(),
        "gone_first_missing": (T0 + timedelta(hours=9)).isoformat(),
    }]
    rows = alert_rows([], sold, now=T0 + timedelta(hours=10))
    assert rows[0]["sold_lo_hours"] == pytest.approx(20 / 60)
    assert rows[0]["sold_hi_hours"] == pytest.approx(9.0)


def test_an_overnight_sale_raises_no_underpriced_alert():
    """The whole point of the decisive-bracket rule, end to end."""
    from src.tracking.alerts import Thresholds, alerts_for
    from src.ui.my_listings import alert_rows

    sold = [{
        "id": 1, "name": "Rune Path", "price_chaos": 90.0,
        "predicted_chaos": 120.0, "status": "gone",
        "priced_at": T0.isoformat(),
        "last_seen": (T0 + timedelta(minutes=20)).isoformat(),
        "gone_first_missing": (T0 + timedelta(hours=9)).isoformat(),
    }]
    rows = alert_rows([], sold, now=T0 + timedelta(hours=10))
    assert alerts_for(rows, Thresholds(1.0, 24.0, "config")) == []


def test_a_tight_bracket_does_raise_one():
    from src.tracking.alerts import Thresholds, alerts_for
    from src.ui.my_listings import alert_rows

    sold = [{
        "id": 1, "name": "Rune Path", "price_chaos": 90.0,
        "predicted_chaos": 120.0, "status": "gone",
        "priced_at": T0.isoformat(),
        "last_seen": (T0 + timedelta(minutes=20)).isoformat(),
        "gone_first_missing": (T0 + timedelta(minutes=35)).isoformat(),
    }]
    rows = alert_rows([], sold, now=T0 + timedelta(hours=10))
    alerts = alerts_for(rows, Thresholds(1.0, 24.0, "config"))
    assert [a.kind for a in alerts] == ["underpriced"]


def test_the_calibration_note_states_a_refusal_as_plainly_as_a_measurement():
    from src.tracking.calibration import PersonalCalibration
    from src.ui.my_listings import calibration_note

    refused = PersonalCalibration(
        factor=1.0, source="not applied: 8 usable episodes, 20 needed",
        model_version="v1", sample=8, applied=False,
    )
    note = calibration_note(refused)
    assert "not applied" in note and "20 needed" in note

    applied = PersonalCalibration(
        factor=1.08, source="measured 1.08x against catboost-2026-08-24",
        model_version="catboost-2026-08-24", sample=36, applied=True,
    )
    note = calibration_note(applied)
    assert "1.08" in note and "catboost-2026-08-24" in note
    assert "fair value is not" in note.lower()


# ---------------------------------------------------------------------------
# Turning the tracker on and off from the page it reports on
# ---------------------------------------------------------------------------
def test_the_tracker_is_switched_from_the_page_not_a_config_file():
    """The one control for the feature used to be a key in config.yaml, in a
    file the app never asks you to open."""
    from pathlib import Path

    page = Path("src/ui/my_listings.py").read_text()
    assert "st.toggle(" in page
    assert "listing_tracker.enabled" in page
    assert "Turn on `listing_tracker.enabled` in config" not in page, (
        "the instruction to hand-edit YAML should be gone, not sitting beside a toggle"
    )


def test_turning_it_off_cancels_the_running_poller():
    """Off has to mean the thread stops. A flag flipped under a live poller
    leaves it polling your account behind a screen that says it is not."""
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings._tracker_toggle)
    assert "poller.cancel()" in source
    assert 'st.session_state.pop("listing_poller"' in source


def test_the_choice_is_persisted_so_it_survives_a_restart():
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings._tracker_toggle)
    assert 'cfg.set("listing_tracker.enabled"' in source
    assert "cfg.save()" in source


def test_the_off_caption_says_what_is_lost_rather_than_only_that_it_is_off():
    """Polling gaps widen the bracket on anything that sells meanwhile, and
    that is the one thing a user cannot infer from an off switch."""
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings._tracker_toggle)
    assert "bracket" in source


# ---------------------------------------------------------------------------
# Staleness comes from the trade API's own timestamp, not from scan history
# ---------------------------------------------------------------------------
def test_the_age_is_read_from_the_api_timestamp_when_there_is_one():
    from src.ui.my_listings import hours_listed, listed_at

    row = _row("Rune Path", 1, indexed_at=(T0 - timedelta(hours=62)).isoformat())
    assert listed_at(row) == T0 - timedelta(hours=62)
    assert hours_listed(row, T0) == 62


def test_a_row_with_no_api_timestamp_falls_back_to_priced_at():
    """Imported rows, and rows written before the stamp was carried."""
    from src.ui.my_listings import hours_listed

    assert hours_listed(_row("Rune Path", 30), T0) == 30
    assert hours_listed(_row("Rune Path", 30, indexed_at=None), T0) == 30


def test_a_trailing_z_timestamp_is_understood():
    """GGG writes `2026-08-22T18:00:00Z`, which fromisoformat rejected before 3.11."""
    from src.ui.my_listings import listed_at

    row = _row("Rune Path", 1, indexed_at="2026-08-26T12:00:00Z")
    assert listed_at(row) == datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def test_a_map_listed_days_ago_is_stale_on_the_first_capture(tmp_path):
    """No scan history at all: one poll, and a map GGG says was listed 62 hours
    ago is 62 hours old on the page. This is the bug the API timestamp exists to
    fix -- ages inferred from our own scans made everything newly listed after
    every restart, so nothing was ever stale."""
    from dataclasses import replace

    from src.database.repository import Repository
    from src.schema import MarketListing, NormalizedMap
    from src.tracking.reconcile import reconcile
    from src.ui.my_listings import stale_rows

    now = datetime.now(timezone.utc)
    listed = now - timedelta(hours=62)
    item = NormalizedMap(
        name="Rune Path", base_type="Terror Solace", map_tier=16, item_level=84,
        is_originator=True, supported=True, quantity=90, rarity=140, pack_size=30,
    )
    listing = replace(
        MarketListing(
            item=item, listing_id="abc", league=LEAGUE, price_amount=150.0,
            price_currency="chaos", price_chaos=150.0, price_type="~b/o",
        ),
        indexed_at=listed,
    )

    repo = Repository(tmp_path / "first.db")
    # `now` is the moment of the one and only poll: nothing has been watched.
    repo.apply_reconciliation(
        "me", "Allflame", reconcile([], [listing], now=now, complete=True)
    )
    rows = repo.tracked_rows_for_page("me", "Allflame")

    stale = stale_rows(rows, now, stale_after_hours=24)
    assert len(stale) == 1, "62 hours listed is stale on the first look"
    assert stale[0]["hours_listed"] == pytest.approx(62, abs=0.01)


# ---------------------------------------------------------------------------
# The watcher has to be visible, and pressable
# ---------------------------------------------------------------------------
def test_the_status_line_redraws_itself_rather_than_waiting_for_a_click():
    """Streamlit runs the script only when a widget is touched, so a page left
    open said "last checked: never" however many passes ran behind it -- which
    is indistinguishable from a switch that does nothing."""
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings)
    assert "@st.fragment(run_every=" in source
    assert "WATCH_REFRESH_SECONDS" in source


def test_there_is_a_button_that_checks_now_and_says_what_happened():
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings._watch_status)
    assert "Check now" in source
    assert "_poll_now" in source

    poll_now = inspect.getsource(my_listings._poll_now)
    assert "poller.poke()" in poll_now, "the button makes a capture, it does not only ask"
    assert "wait_for_pass" in poll_now, "and it waits, so it can say what happened"
    assert "poller.start()" in poll_now, "and it revives a poller that has stopped"
    assert "poll_once" not in poll_now, (
        "the pass belongs on the poller's thread -- its SQLite connection does"
    )


def test_a_stopped_watcher_says_so_instead_of_showing_a_stale_success():
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings._watch_status)
    assert "poller.running" in source
    assert "has stopped" in source


def test_turning_it_off_clears_the_process_registry_too():
    """Off used to pop a session-state key while the process registry kept the
    thread, so the next rerun found it there and started it again."""
    import inspect

    from src.ui import my_listings

    source = inspect.getsource(my_listings._tracker_toggle)
    assert "stop_poller(account, league)" in source
