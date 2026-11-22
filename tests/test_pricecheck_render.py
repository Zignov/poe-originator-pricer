"""What the popup says, tested without a display.

The whole reason `src/pricecheck/render.py` is a separate module is that these
are the decisions worth pinning -- which word, which colour, where the window
goes -- and none of them need tkinter to check.
"""
from __future__ import annotations

import pytest

from src.ml.decision_bands import MARGINAL, REROLL, SELL
from src.schema import FEATURE_SHORT_LABELS
from src.pricecheck.render import (
    PriceCard,
    card_from_response,
    error_card,
    popup_position,
    status_caption,
    verdict_colour,
)
from src.ui import palette


def _response(**overrides):
    """A `/price/text` reply, in the shape src/service/core.py produces."""
    payload = {
        "supported": True,
        "note": "412c",
        "strategy": "market",
        "source": "model",
        "chaos": {
            "fair_value": 416.0,
            "suggested": 412.0,
            "low": 330.0,
            "high": 505.0,
            "market_reference": 400.0,
        },
        "display": {
            "unit": "c",
            "fair_value": 416.0,
            "suggested": 412.0,
            "formatted": "412c",
            "low": 330.0,
            "high": 505.0,
            "range": "330-505c",
        },
        "confidence": {
            "level": "High",
            "score": 0.88,
            "reasons": ["24 close comparables", "all within 3 days"],
        },
        "item": {
            "quantity": 94, "rarity": 131, "pack_size": 36,
            "more_currency": 47, "more_maps": 35, "more_scarabs": 0,
            "more_divination_cards": 0,
            "position": "C4", "tab": "Maps", "corrupted": False,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


# -- the happy path ---------------------------------------------------------
def test_a_priced_map_becomes_a_complete_card():
    card = card_from_response(_response())
    assert card.priced
    assert card.headline == "412c"
    assert card.verdict == SELL
    assert card.range_text == "330-505c"
    assert card.confidence == "High"
    assert card.confidence_fraction == pytest.approx(0.88)
    assert (FEATURE_SHORT_LABELS["quantity"], "94") in card.rolls
    assert card.note == "412c"


def test_the_rolls_keep_the_order_the_cli_prints_them_in():
    """So someone who reads one in a terminal can read one over the stash.

    Asserted against the schema rather than against frozen strings: GGG renames
    these between leagues -- Covens turned `Item Quantity` into `Atlas Drops`
    and `More Scarabs` into `More Threads` -- and a test holding the old words
    would fail for the rename rather than for the thing it is checking, which
    is the *order*.
    """
    labels = [label for label, _value in card_from_response(_response()).rolls]
    assert labels == [
        FEATURE_SHORT_LABELS[name]
        for name in (
            "quantity", "rarity", "pack_size", "more_currency",
            "more_maps", "more_scarabs", "more_divination_cards",
        )
    ]


def test_a_zero_roll_is_shown_rather_than_dropped():
    """A zero thread roll is why a map is cheap, and is information.

    Dropping zero rows would leave the reader unable to tell a roll that is
    absent from one the popup forgot.
    """
    card = card_from_response(_response())
    assert (FEATURE_SHORT_LABELS["more_scarabs"], "0") in card.rolls
    assert len(card.rolls) == 7


def test_the_fair_value_is_hidden_when_the_strategy_did_not_move_it():
    """At `market` the two figures agree, and printing both invites a hunt
    for a difference that is not there."""
    card = card_from_response(
        _response(display={"fair_value": 412.0, "suggested": 412.0})
    )
    assert card.fair_value_text == ""


def test_the_fair_value_is_shown_when_the_strategy_did_move_it():
    card = card_from_response(
        _response(
            strategy="quick_sell",
            display={"fair_value": 440.0, "suggested": 390.0, "formatted": "390c"},
        )
    )
    assert card.fair_value_text == "440c"
    assert card.strategy == "quick_sell"


# -- the verdict ------------------------------------------------------------
@pytest.mark.parametrize(
    "suggested,expected",
    [(12.0, REROLL), (29.9, REROLL), (30.0, MARGINAL), (49.9, MARGINAL), (50.0, SELL), (900.0, SELL)],
)
def test_the_verdict_follows_the_absolute_training_thresholds(suggested, expected):
    """And not a percentile band -- see the module docstring in render.py.

    One copied item is not a distribution. These are the same cuts the model's
    training floor already makes real, so the word means the same thing here as
    everywhere else in the app.
    """
    card = card_from_response(_response(chaos={"suggested": suggested}))
    assert card.verdict == expected


def test_each_verdict_gets_a_different_colour():
    colours = {verdict_colour(w) for w in (REROLL, MARGINAL, SELL)}
    assert len(colours) == 3


def test_the_verdict_colours_are_not_the_confidence_colours():
    """Two different questions must not be answered in one palette.

    A confident price for a junk map would otherwise be painted green, which
    reads as "sell this" rather than "this number is reliable".
    """
    verdicts = {verdict_colour(w) for w in (REROLL, MARGINAL, SELL)}
    assert not verdicts & set(palette.CONFIDENCE_COLOURS.values())


def test_a_recoloured_band_reaches_the_popup():
    """The palette is mutated in place, and this is what that buys.

    A user who re-colours a band on the Settings page gets it in the popup
    without this module knowing the Settings page exists.
    """
    original = palette.VALUE_COLOURS["medium"]
    try:
        palette.VALUE_COLOURS["medium"] = "#123456"
        assert verdict_colour(SELL) == "#123456"
    finally:
        palette.VALUE_COLOURS["medium"] = original


def test_an_unknown_verdict_still_has_a_colour():
    assert verdict_colour("") == palette.VALUE_COLOURS["unknown"]


# -- the unhappy paths ------------------------------------------------------
def test_an_unsupported_item_reports_the_reason_and_keeps_its_rolls():
    """The reason is the entire value of the popup in this case.

    "Corrupted maps are out of scope" is an answer; a blank popup is not. The
    rolls stay because the user is looking at the item and wants to see that
    the tool read it correctly even though it would not price it.
    """
    card = card_from_response(
        _response(supported=False, note="corrupted maps are out of scope")
    )
    assert not card.priced
    assert card.problem == "corrupted maps are out of scope"
    assert card.rolls, "an unsupported item should still show what was parsed"
    assert not card.actionable, "nothing for the user to fix about the item"


def test_an_unsupported_item_with_no_reason_still_says_something():
    card = card_from_response(_response(supported=False, note=None))
    assert card.problem
    assert "pricer" in card.problem


def test_a_service_failure_is_actionable_and_an_item_failure_is_not():
    """They are shown for different lengths of time, so they must differ here."""
    assert error_card("nothing is listening on 127.0.0.1:8777").actionable
    assert not card_from_response(_response(supported=False)).actionable


@pytest.mark.parametrize("payload", [None, [], "a string", 42, {}])
def test_a_malformed_reply_produces_a_card_rather_than_an_exception(payload):
    """A KeyError here would surface inside a Tk callback, in a process with
    no console -- an invisible crash in a tool whose job is to be visible."""
    card = card_from_response(payload)
    assert isinstance(card, PriceCard)
    assert card.problem


def test_a_reply_missing_its_display_block_still_renders():
    payload = _response()
    del payload["display"]
    del payload["confidence"]
    card = card_from_response(payload)
    assert isinstance(card, PriceCard)
    assert card.verdict == SELL  # chaos.suggested was still there


def test_a_corrupted_map_says_so():
    card = card_from_response(_response(item={"corrupted": True}))
    assert card.corrupted


def test_a_missing_confidence_gets_a_colour_and_no_bar():
    card = card_from_response(_response(confidence={"level": "", "score": None}))
    assert card.confidence_fraction is None
    assert card.confidence_colour  # something drawable, not an empty string


# -- positioning ------------------------------------------------------------
SCREEN = (1920, 1080)
SIZE = (304, 200)


def test_the_popup_sits_below_and_right_of_the_pointer():
    x, y = popup_position((500, 400), SIZE, SCREEN)
    assert x > 500 and y > 400


def test_it_flips_rather_than_clamps_at_the_right_edge():
    """Clamping would shove it back under the pointer, on top of the item the
    user is still hovering -- the one thing it must not cover."""
    x, _y = popup_position((1900, 400), SIZE, SCREEN)
    assert x + SIZE[0] <= 1900, "the popup still covers the pointer"


def test_it_flips_at_the_bottom_edge():
    _x, y = popup_position((500, 1070), SIZE, SCREEN)
    assert y + SIZE[1] <= 1070


def test_it_flips_in_a_corner():
    x, y = popup_position((1915, 1075), SIZE, SCREEN)
    assert x + SIZE[0] <= 1915
    assert y + SIZE[1] <= 1075


@pytest.mark.parametrize(
    "cursor", [(0, 0), (1920, 1080), (-10, -10), (960, 540), (1919, 1), (1, 1079)]
)
def test_it_is_never_off_screen(cursor):
    """The last resort, after the flip: off-screen is strictly worse than
    overlapping, because an off-screen popup is a hotkey that did nothing."""
    x, y = popup_position(cursor, SIZE, SCREEN)
    assert 0 <= x <= SCREEN[0] - SIZE[0]
    assert 0 <= y <= SCREEN[1] - SIZE[1]


def test_a_popup_bigger_than_the_screen_still_lands_somewhere_visible():
    x, y = popup_position((100, 100), (2000, 2000), (800, 600))
    assert x >= 0 and y >= 0


# -- the strip caption ------------------------------------------------------
def test_the_caption_leads_with_the_league():
    """The failure that looks like working software: a model trained on one
    league pricing maps from another. This is the only place it is visible."""
    caption = status_caption(
        {"league": "Covens", "model_version": "v7", "comparables": 2400}
    )
    assert caption.startswith("Covens")
    assert "v7" in caption


def test_a_model_error_replaces_the_model_summary():
    caption = status_caption(
        {"league": "Covens", "model_error": "no model has been trained yet"}
    )
    assert "no model has been trained yet" in caption
    assert "Covens" in caption


def test_no_service_is_said_plainly():
    assert "not connected" in status_caption(None)


def test_a_problem_wins_over_the_status():
    assert status_caption({"league": "Covens"}, "nothing is listening") == (
        "nothing is listening"
    )
