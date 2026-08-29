"""The file the Streamlit app and the overlay process meet at.

Everything here runs without a display: the contract is plain data on purpose,
so the half of the overlay that can be wrong about *prices* is testable
separately from the half that can be wrong about *pixels*.
"""
from __future__ import annotations

import json

import pytest

from src.ml.predict import Estimate
from src.overlay.state import (
    EXALT_NONE,
    EXALT_SKIP,
    EXALT_WORTH,
    OVERLAY_STATE_VERSION,
    OverlayState,
    OverlayStateError,
    StateWatcher,
    exalt_state,
    free_affix_slots,
    read_state,
    state_from_priced,
    tile_price,
    write_state,
)
from src.parsing.affixes import Affix, AffixSet
from src.pricing.display import PriceFormatter
from src.pricing.exalt import ExaltVerdict
from src.pricing.fair_value import PricedMap
from src.schema import NormalizedMap


def _map(x=0, y=0, name="Terror Solace", supported=True, **kwargs) -> NormalizedMap:
    return NormalizedMap(
        x=x, y=y, name=name, base_type="Map (Tier 16)", supported=supported, **kwargs
    )


def _estimate(value: float) -> Estimate:
    return Estimate(
        fair_value_chaos=value,
        interval_low_chaos=value * 0.8,
        interval_high_chaos=value * 1.2,
        confidence="High",
        confidence_score=0.9,
    )


def _priced(x=0, y=0, fair=100.0, suggested=99.0, supported=True, **kwargs) -> PricedMap:
    item = _map(x=x, y=y, supported=supported, **kwargs)
    return PricedMap(
        item=item,
        estimate=_estimate(fair) if supported else None,
        suggested_chaos=suggested if supported else None,
    )


FMT = PriceFormatter("chaos", 210.0)


# -- what a tile shows ------------------------------------------------------
def test_a_tile_shows_the_suggested_price_not_the_fair_value():
    """The web grid's rule, and the reason it exists: the strategy must move it."""
    assert tile_price(_priced(fair=100.0, suggested=94.0)) == 94.0


def test_a_tile_falls_back_to_fair_value_when_no_suggestion_was_made():
    assert tile_price(_priced(fair=100.0, suggested=None)) == 100.0


def test_the_web_grid_and_the_overlay_agree_on_the_number():
    from src.ui.stash_grid import _tile_price

    entry = _priced(fair=100.0, suggested=94.0)
    assert _tile_price(entry) == tile_price(entry)


# -- the three exalt states -------------------------------------------------
def test_a_profitable_exalt_is_marked_worth_it():
    assert exalt_state(ExaltVerdict(available=True, net_gain_chaos=25.0)) == EXALT_WORTH


def test_an_unprofitable_exalt_is_marked_skip():
    assert exalt_state(ExaltVerdict(available=True, net_gain_chaos=-3.0)) == EXALT_SKIP


def test_a_map_that_cannot_take_an_orb_gets_no_state_at_all():
    assert exalt_state(ExaltVerdict(available=False)) == EXALT_NONE
    assert exalt_state(None) == EXALT_NONE


# -- free affix slots -------------------------------------------------------
def _affixes(count: int, corrupted: bool = False, known: bool = True) -> AffixSet:
    """An affix set holding `count` affixes, filled the way the game fills them.

    Prefixes first and then suffixes, up to each slot's own limit, because
    `open_slots` is the sum of two independently-capped halves -- six affixes
    all in the prefix slot would leave three suffixes open on a map the parser
    would have refused to read in the first place.
    """
    max_prefixes = 4 if corrupted else 3
    slots = ["prefix"] * min(count, max_prefixes)
    slots += ["suffix"] * (count - len(slots))
    return AffixSet(
        affixes=tuple(
            Affix(name=f"Mod{i}", tier=f"{'P' if slot == 'prefix' else 'S'}{i}", slot=slot)
            for i, slot in enumerate(slots)
        ),
        known=known,
        corrupted=corrupted,
    )


def test_a_readable_affix_set_reports_the_slots_it_has_left():
    assert free_affix_slots(_affixes(2)) == 4


def test_an_unreadable_affix_set_reports_nothing_rather_than_zero():
    """`open_slots` is 0 whenever `known` is False, and 0 means "full" on a tile.

    Letting that through would turn "this payload has no affix structure" into a
    confident "this map cannot take another affix", which is exactly the reading
    that stops someone using an orb that would have paid for itself.
    """
    assert free_affix_slots(AffixSet(known=False)) is None
    assert AffixSet(known=False).open_slots == 0  # the trap this guards
    assert free_affix_slots(None) is None


def test_a_full_map_reports_zero_which_is_a_different_thing_from_nothing():
    assert free_affix_slots(_affixes(6)) == 0


def test_a_corrupted_map_counts_against_its_own_raised_ceiling():
    """Eight affixes, not six -- and the badge is what a corrupted 8-mod map has."""
    assert free_affix_slots(_affixes(2, corrupted=True)) == 6


# -- projection -------------------------------------------------------------
def test_a_priced_tab_becomes_one_tile_per_positioned_map():
    state = state_from_priced([_priced(x=1, y=2), _priced(x=3, y=4)], FMT)
    assert [(t.x, t.y) for t in state.tiles] == [(1, 2), (3, 4)]


def test_a_map_with_no_stash_position_is_dropped_rather_than_stacked_at_the_origin():
    """Clipboard-pasted maps are numbered in paste order, which is not a layout.

    Drawing them at (0,0) onward would put prices on maps they do not belong to,
    which is the one failure this feature must not have.
    """
    entry = _priced()
    entry.item.x = None
    entry.item.y = None
    assert state_from_priced([entry], FMT).tiles == ()


def test_an_unsupported_map_keeps_its_cell_and_loses_its_price():
    state = state_from_priced([_priced(supported=False, unsupported_reason="Corrupted")], FMT)
    tile = state.tiles[0]
    assert not tile.supported
    assert tile.price_chaos is None
    assert tile.label == "-"
    assert tile.unsupported_reason == "Corrupted"


def test_the_label_is_formatted_in_the_display_currency():
    state = state_from_priced([_priced(suggested=420.0)], PriceFormatter("divine", 210.0))
    assert state.tiles[0].label == "2d"
    assert state.tiles[0].price_chaos == 420.0


def test_item_size_comes_from_the_item_not_from_an_assumption():
    state = state_from_priced([_priced(width=2, height=1)], FMT)
    assert (state.tiles[0].width, state.tiles[0].height) == (2, 1)


def test_verdicts_are_matched_by_object_identity_like_the_web_grid():
    worth, skip = _priced(x=0), _priced(x=1)
    verdicts = {
        id(worth): ExaltVerdict(available=True, net_gain_chaos=10.0),
        id(skip): ExaltVerdict(available=True, net_gain_chaos=-10.0),
    }
    state = state_from_priced([worth, skip], FMT, verdicts=verdicts)
    assert [t.exalt for t in state.tiles] == [EXALT_WORTH, EXALT_SKIP]


def test_the_grid_grows_to_fit_a_tab_bigger_than_the_default():
    state = state_from_priced([_priced(x=20, y=19)], FMT, grid=(12, 12))
    assert state.grid == (21, 20)


def test_the_grid_never_shrinks_below_the_configured_size():
    assert state_from_priced([_priced(x=0, y=0)], FMT, grid=(12, 12)).grid == (12, 12)


def test_supported_prices_are_what_the_colour_bands_will_be_computed_over():
    state = state_from_priced(
        [_priced(x=0, suggested=10.0), _priced(x=1, supported=False), _priced(x=2, suggested=90.0)],
        FMT,
    )
    assert state.supported_prices == [10.0, 90.0]


# -- serialisation ----------------------------------------------------------
def test_a_state_survives_a_round_trip_through_json(tmp_path):
    original = state_from_priced(
        [_priced(x=2, y=3, suggested=88.0)],
        FMT,
        strategy="patient",
        league="Allflame",
        tab_names=["Originator Maps"],
    )
    path = write_state(original, tmp_path / "overlay_state.json")
    restored = read_state(path)
    assert restored == original


def test_the_free_slot_count_survives_a_state_file_round_trip(tmp_path):
    """The dots are drawn in another process, so the count has to cross the file."""
    full, open_two, unreadable = _priced(x=0), _priced(x=1), _priced(x=2)
    verdicts = {
        id(full): ExaltVerdict(available=False, affixes=_affixes(6)),
        id(open_two): ExaltVerdict(available=True, net_gain_chaos=9.0, affixes=_affixes(4)),
        id(unreadable): ExaltVerdict(available=False, affixes=AffixSet(known=False)),
    }
    original = state_from_priced([full, open_two, unreadable], FMT, verdicts=verdicts)
    assert [t.open_affixes for t in original.tiles] == [0, 2, None]

    restored = read_state(write_state(original, tmp_path / "state.json"))
    assert [t.open_affixes for t in restored.tiles] == [0, 2, None]
    assert restored == original


def test_the_count_comes_off_verdicts_that_could_not_be_judged_at_all():
    """`evaluate_exalt` attaches the affix set to its `available=False` returns too.

    Most maps in a real tab are corrupted or full, so if the count only survived
    on exaltable maps the badge would be missing from nearly every tile.
    """
    entry = _priced()
    verdict = ExaltVerdict(
        available=False, reason="corrupted maps cannot be modified",
        affixes=_affixes(3, corrupted=True),
    )
    state = state_from_priced([entry], FMT, verdicts={id(entry): verdict})
    assert state.tiles[0].open_affixes == 5


def test_an_explicit_count_wins_over_the_one_the_verdicts_carry():
    """The overlay's re-analysis has a model-free count and no verdicts at all."""
    entry = _priced()
    state = state_from_priced(
        [entry],
        FMT,
        verdicts={id(entry): ExaltVerdict(available=False, affixes=_affixes(6))},
        open_affixes={id(entry): 3},
    )
    assert state.tiles[0].open_affixes == 3


def test_a_tab_with_no_affix_information_at_all_carries_no_counts():
    assert state_from_priced([_priced()], FMT).tiles[0].open_affixes is None


def test_a_tile_carries_the_confidence_score_the_meter_is_drawn_from():
    state = state_from_priced([_priced()], FMT)
    assert state.tiles[0].confidence == "High"
    assert state.tiles[0].confidence_score == pytest.approx(0.9)


def test_an_unsupported_map_has_no_confidence_score_to_carry():
    state = state_from_priced([_priced(supported=False)], FMT)
    assert state.tiles[0].confidence_score is None


def test_a_state_file_written_before_the_score_existed_still_reads(tmp_path):
    """The label alone is enough to draw a meter -- see confidence_fill."""
    path = tmp_path / "state.json"
    payload = state_from_priced([_priced()], FMT).to_dict()
    for tile in payload["tiles"]:
        tile.pop("confidence_score")
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = read_state(path)
    assert restored.tiles[0].confidence == "High"
    assert restored.tiles[0].confidence_score is None


def test_writing_creates_the_directory_it_was_pointed_at(tmp_path):
    path = write_state(OverlayState(), tmp_path / "nested" / "state.json")
    assert path.exists()


def test_a_missing_file_says_how_to_produce_one(tmp_path):
    with pytest.raises(OverlayStateError, match="Stash Pricer"):
        read_state(tmp_path / "absent.json")


def test_a_file_that_is_not_json_is_refused_rather_than_guessed_at(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OverlayStateError, match="not valid JSON"):
        read_state(path)


def test_a_state_from_a_future_version_is_refused(tmp_path):
    """Better no tiles than tiles whose fields mean something else now."""
    path = tmp_path / "state.json"
    payload = OverlayState().to_dict()
    payload["version"] = OVERLAY_STATE_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OverlayStateError, match="version"):
        read_state(path)


def test_a_tile_missing_its_coordinates_is_reported_as_malformed(tmp_path):
    path = tmp_path / "state.json"
    payload = OverlayState().to_dict()
    payload["tiles"] = [{"label": "10c"}]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OverlayStateError, match="malformed tile"):
        read_state(path)


def test_writing_leaves_no_temporary_files_behind(tmp_path):
    write_state(OverlayState(), tmp_path / "state.json")
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


# -- watching ---------------------------------------------------------------
def test_the_watcher_reports_nothing_while_the_file_is_absent(tmp_path):
    assert StateWatcher(tmp_path / "state.json").poll() is None


def test_the_watcher_returns_the_state_once_and_then_stays_quiet(tmp_path):
    path = tmp_path / "state.json"
    write_state(state_from_priced([_priced()], FMT), path)
    watcher = StateWatcher(path)
    assert watcher.poll() is not None
    assert watcher.poll() is None


def test_the_watcher_notices_a_rewrite_even_within_one_clock_tick(tmp_path):
    """Two exports a second apart must both arrive, coarse mtimes or not."""
    path = tmp_path / "state.json"
    write_state(state_from_priced([_priced(x=0)], FMT), path)
    watcher = StateWatcher(path)
    watcher.poll()
    write_state(state_from_priced([_priced(x=0), _priced(x=1)], FMT), path)
    second = watcher.poll()
    assert second is not None and len(second.tiles) == 2


def test_the_watcher_keeps_the_last_good_state_when_the_file_goes_bad(tmp_path):
    path = tmp_path / "state.json"
    write_state(state_from_priced([_priced()], FMT), path)
    watcher = StateWatcher(path)
    watcher.poll()
    path.write_text("garbage", encoding="utf-8")
    assert watcher.poll() is None
    assert "not valid JSON" in watcher.error


def test_the_summary_line_names_the_tab_and_the_strategy():
    state = state_from_priced(
        [_priced()], FMT, strategy="quick_sell", tab_names=["Originator Maps"]
    )
    summary = state.summary()
    assert "Originator Maps" in summary and "quick_sell" in summary and "1 maps" in summary
