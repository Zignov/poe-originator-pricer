"""Selections must survive a restart.

The complaint this answers: every picker on every page came up blank on each
launch -- the stash tab you always read, the character whose inventory you
always check, the pricing mode, the whole filter row -- because a Streamlit
widget's value lives in a session and a session dies with the browser tab.

The two halves are tested separately because only one of them is interesting.
`PrefStore` is a file, and a file either round-trips or it does not.
`remembered` is where the judgement lives: a remembered value is only worth
using if it is still *offered*, and most of the tests below are about the ways
it stops being offered -- a tab renamed, a league changed, an option list
edited between releases.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.ui import prefs


@pytest.fixture
def store(tmp_path) -> prefs.PrefStore:
    return prefs.PrefStore(tmp_path / "ui_prefs.json")


# -- the file ---------------------------------------------------------------


def test_a_missing_file_reads_as_no_preferences(store):
    """First launch. Not an error, just nothing remembered yet."""
    assert store.load() == {}


def test_a_value_survives_being_written_and_read_by_a_new_store(tmp_path):
    """The actual complaint: it must still be there after a restart."""
    path = tmp_path / "ui_prefs.json"
    prefs.PrefStore(path).save({"pricing_mode": "market"})
    assert prefs.PrefStore(path).load() == {"pricing_mode": "market"}


def test_the_parent_directory_is_created_if_it_is_missing(tmp_path):
    store = prefs.PrefStore(tmp_path / "nested" / "deeper" / "ui_prefs.json")
    store.save({"a": 1})
    assert store.load() == {"a": 1}


def test_saving_values_that_have_not_changed_does_not_rewrite_the_file(store):
    """Streamlit reruns the whole script on every keystroke.

    Without this the file would be rewritten several times a second while a
    number is being typed, for a page whose values are identical each time.
    """
    store.save({"a": 1})
    before = store.path.stat().st_mtime_ns
    store.save({"a": 1})
    assert store.path.stat().st_mtime_ns == before


def test_saving_a_changed_value_does_rewrite_the_file(store):
    store.save({"a": 1})
    store.save({"a": 2})
    assert store.load() == {"a": 2}


def test_a_corrupt_file_reads_as_no_preferences_rather_than_raising(store):
    """A half-written or hand-edited file must not stop the app from starting.

    These are conveniences. None of them is worth a traceback on launch.
    """
    store.path.write_text("{ this is not json", encoding="utf-8")
    assert store.load() == {}


def test_a_file_holding_something_other_than_an_object_is_ignored(store):
    store.path.write_text("[1, 2, 3]", encoding="utf-8")
    assert store.load() == {}


def test_no_temporary_files_are_left_beside_the_real_one(store):
    """The write is atomic, so it goes via a temp file in the same directory."""
    store.save({"a": 1})
    store.save({"a": 2})
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]


def test_forgetting_removes_the_file(store):
    store.save({"a": 1})
    store.forget()
    assert not store.path.exists()
    assert store.load() == {}


def test_forgetting_when_there_is_nothing_to_forget_is_not_an_error(store):
    store.forget()


# -- deciding whether a remembered value is still usable ---------------------


def test_nothing_remembered_falls_back_to_the_default():
    assert prefs.remembered(None, "model", options=["model", "market"]) == "model"


def test_a_remembered_choice_is_used_when_it_is_still_on_offer():
    assert prefs.remembered("market", "model", options=["model", "market"]) == "market"


def test_a_choice_that_is_no_longer_on_offer_falls_back_to_the_default():
    """An option list edited between releases must not wedge the widget."""
    assert prefs.remembered("bands", "model", options=["model", "market"]) == "model"


def test_remembered_tabs_keep_only_the_ones_that_still_exist():
    """A tab was renamed or deleted since last time; the rest still load."""
    assert prefs.remembered(
        ["Maps", "Gone"], ["Maps"], options=["Maps", "Currency"]
    ) == ["Maps"]


def test_remembered_tabs_that_have_all_vanished_fall_back_to_the_default():
    """Switching league is the ordinary way this happens.

    Last league's tab names match nothing in this one, and a multiselect left
    empty blocks the Analyze button -- so the default is better than the
    remainder.
    """
    assert prefs.remembered(
        ["Alpha", "Beta"], ["Maps"], options=["Maps", "Currency"]
    ) == ["Maps"]


def test_a_remembered_list_is_returned_in_the_order_the_options_are_offered():
    assert prefs.remembered(
        ["Currency", "Maps"], [], options=["Maps", "Currency"]
    ) == ["Maps", "Currency"]


def test_a_single_value_is_refused_where_a_list_is_expected():
    assert prefs.remembered("Maps", [], options=["Maps"]) == []


def test_a_tuple_option_is_matched_by_the_list_json_read_it_back_as():
    """Model Analysis picks an interaction, and an interaction is a pair.

    JSON has no tuple, so ("quantity", "pack_size") is stored and read back as
    a list. Streamlit compares a session value to its options with `==`, so the
    tuple itself has to come back -- a list would fail the check on the very
    next run.
    """
    options = [("quantity", "pack_size"), ("rarity", "pack_size")]
    assert prefs.remembered(["rarity", "pack_size"], options[0], options) == (
        "rarity",
        "pack_size",
    )


def test_a_tuple_option_that_no_longer_exists_falls_back_to_the_default():
    options = [("quantity", "pack_size")]
    assert prefs.remembered(["gone", "away"], options[0], options) == (
        "quantity",
        "pack_size",
    )


def test_a_remembered_number_inside_the_widgets_bounds_is_used():
    assert prefs.remembered(50.0, 0.0, bounds=(0.0, 5000.0)) == 50.0


def test_a_remembered_number_outside_the_widgets_bounds_is_refused():
    """Streamlit raises if a number_input starts outside min/max."""
    assert prefs.remembered(9000.0, 0.0, bounds=(0.0, 5000.0)) == 0.0
    assert prefs.remembered(-1.0, 0.0, bounds=(0.0, 5000.0)) == 0.0


def test_a_whole_number_is_accepted_where_a_decimal_is_expected():
    """JSON has one number type; 50.0 comes back as 50."""
    assert prefs.remembered(50, 0.0, bounds=(0.0, 100.0)) == 50.0
    assert isinstance(prefs.remembered(50, 0.0, bounds=(0.0, 100.0)), float)


def test_a_decimal_is_refused_where_a_whole_number_is_expected():
    """number_input infers int or float from its default; mixing them raises."""
    assert prefs.remembered(2.5, 500, bounds=(50, 5000)) == 500


def test_a_remembered_value_of_the_wrong_type_is_refused():
    assert prefs.remembered("yes", False) is False
    assert prefs.remembered("50", 0.0, bounds=(0.0, 100.0)) == 0.0


def test_a_boolean_is_refused_where_a_number_is_expected():
    """bool is an int in Python, so this needs saying out loud."""
    assert prefs.remembered(True, 0.0, bounds=(0.0, 100.0)) == 0.0


def test_a_number_is_refused_where_a_checkbox_is_expected():
    assert prefs.remembered(1, False) is False


def test_a_checkbox_remembers_being_ticked():
    assert prefs.remembered(True, False) is True


# -- the whole thing, through the real app ----------------------------------

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _stash_pricer(app):
    app.run()
    app.sidebar.radio[0].set_value("Stash Pricer").run()
    return app


def _picker(app):
    return next(r for r in app.radio if r.key == "stash_source_pick")


def test_the_source_picker_comes_back_where_you_left_it(monkeypatch, tmp_path):
    """End to end, and the reason any of this exists.

    Two AppTest instances are two launches of the program: nothing carries
    between them but the file.
    """
    monkeypatch.setattr(prefs, "PATH", tmp_path / "ui_prefs.json")

    first = _stash_pricer(AppTest.from_file(APP, default_timeout=120))
    assert _picker(first).value == "session"  # the built-in default
    _picker(first).set_value("csv").run()
    assert not first.exception

    second = _stash_pricer(AppTest.from_file(APP, default_timeout=120))
    assert not second.exception
    assert _picker(second).value == "csv"


def test_a_restart_with_nothing_remembered_uses_the_built_in_default(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(prefs, "PATH", tmp_path / "ui_prefs.json")
    app = _stash_pricer(AppTest.from_file(APP, default_timeout=120))
    assert not app.exception
    assert _picker(app).value == "session"
