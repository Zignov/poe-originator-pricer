"""What the overlay draws, tested without drawing anything.

The overlay's whole claim is that it shows the same thing as the web grid, in
the same colours, on the right pixels. That claim is entirely inside
build_draw_list, so it can be checked on a machine with no display -- which is
the reason the tkinter layer holds no decisions at all.
"""
from __future__ import annotations

import pytest

from src.market.comparables import percentile_bands, value_category
from src.overlay.calibration import Calibration
from src.overlay.layout import (
    MUTED_FILL,
    MUTED_LABEL,
    RING_SKIP,
    RING_WORTH,
    TileDraw,
    build_draw_list,
    font_size_for,
    geometry_string,
    ring_colour,
    window_geometry,
)
from src.overlay.state import (
    EXALT_NONE,
    EXALT_SKIP,
    EXALT_WORTH,
    OverlayState,
    OverlayTile,
)
from src.ui.palette import CONFIDENCE_COLOURS, VALUE_COLOURS

CAL = Calibration(origin=(100, 200), cell_px=50.0, grid=(12, 12))


def _tile(x=0, y=0, price=100.0, **kwargs) -> OverlayTile:
    return OverlayTile(x=x, y=y, price_chaos=price, label=f"{price:.0f}c", **kwargs)


def _state(*tiles: OverlayTile) -> OverlayState:
    return OverlayState(tiles=tiles, grid=(12, 12))


# -- what gets drawn --------------------------------------------------------
def test_an_empty_cell_draws_nothing_at_all():
    """The game has to show through, or the tab becomes unreadable."""
    assert build_draw_list(_state(), CAL) == []


def test_one_rectangle_per_occupied_cell():
    draws = build_draw_list(_state(_tile(x=0), _tile(x=5, y=7)), CAL)
    assert [(d.left, d.top) for d in draws] == [(100, 200), (350, 550)]


def test_a_tile_carries_the_label_the_app_formatted():
    draws = build_draw_list(_state(_tile(price=94.0)), CAL)
    assert draws[0].text == "94c"


def test_a_multi_cell_item_gets_a_rectangle_of_its_own_size():
    draws = build_draw_list(_state(_tile(width=2, height=3)), CAL)
    assert (draws[0].width, draws[0].height) == (100, 150)


def test_an_unsupported_map_is_a_muted_dash_rather_than_a_gap():
    """A gap reads as 'the overlay has not loaded', which is a different thing."""
    draws = build_draw_list(_state(OverlayTile(x=0, y=0, supported=False)), CAL)
    assert draws[0].fill == MUTED_FILL
    assert draws[0].text == MUTED_LABEL


# -- colour bands -----------------------------------------------------------
def test_colours_come_from_the_same_percentile_bands_as_the_web_grid():
    prices = [10.0, 20.0, 40.0, 80.0, 160.0, 320.0, 640.0, 1280.0]
    state = _state(*[_tile(x=i, price=p) for i, p in enumerate(prices)])
    bands = percentile_bands(prices)
    expected = [VALUE_COLOURS[value_category(p, bands)] for p in prices]
    assert [d.fill for d in build_draw_list(state, CAL)] == expected


def test_the_bands_are_percentiles_of_this_tab_not_fixed_thresholds():
    """A cheap tab still has a dearest map, and it is still coloured as one."""
    cheap = _state(*[_tile(x=i, price=p) for i, p in enumerate([1.0, 2.0, 3.0, 99.0])])
    assert build_draw_list(cheap, CAL)[-1].fill == VALUE_COLOURS["very high"]


def test_unsupported_maps_do_not_drag_the_bands_around():
    supported = [_tile(x=0, price=10.0), _tile(x=1, price=100.0)]
    with_junk = [*supported, OverlayTile(x=2, y=0, supported=False)]
    assert [d.fill for d in build_draw_list(_state(*with_junk), CAL)][:2] == [
        d.fill for d in build_draw_list(_state(*supported), CAL)
    ]


# -- exalt rings ------------------------------------------------------------
def test_a_map_worth_an_orb_gets_the_cyan_ring():
    assert ring_colour(EXALT_WORTH) == RING_WORTH


def test_a_map_not_worth_the_orb_gets_the_muted_ring():
    assert ring_colour(EXALT_SKIP) == RING_SKIP


def test_a_map_that_cannot_take_an_orb_gets_no_ring():
    assert ring_colour(EXALT_NONE) == ""


def test_the_ring_travels_with_the_tile_into_the_draw_list():
    draws = build_draw_list(_state(_tile(exalt=EXALT_WORTH)), CAL)
    assert draws[0].ring == RING_WORTH


# -- window placement -------------------------------------------------------
def test_the_window_covers_exactly_the_calibrated_grid():
    assert window_geometry(CAL) == (600, 600, 100, 200)


def test_the_window_follows_the_calibration_onto_a_negative_monitor():
    cal = Calibration(origin=(-1920, 0), cell_px=50.0, grid=(12, 12))
    assert window_geometry(cal) == (600, 600, -1920, 0)


def test_a_negative_offset_is_written_the_way_tk_reads_as_a_coordinate():
    """`-1920` in a Tk geometry means 1920 from the *right* edge, not x=-1920."""
    assert geometry_string(600, 600, -1920, -40) == "600x600+-1920+-40"


def test_a_positive_offset_looks_ordinary():
    assert geometry_string(600, 600, 100, 200) == "600x600+100+200"


def test_rectangles_are_shifted_into_window_coordinates_once():
    draw = TileDraw(left=350, top=550, right=400, bottom=600, fill="#fff", text="1c")
    moved = draw.relative_to((100, 200))
    assert (moved.left, moved.top, moved.right, moved.bottom) == (250, 350, 300, 400)


# -- text -------------------------------------------------------------------
@pytest.mark.parametrize("cell_px,expected", [(20.0, 6), (46.0, 11), (200.0, 16)])
def test_the_font_scales_with_the_cell_and_stays_within_sane_bounds(cell_px, expected):
    assert font_size_for(cell_px) == expected


# -- the confidence meter ---------------------------------------------------
def test_a_priced_tile_carries_a_meter():
    draws = build_draw_list(_state(_tile(confidence="High", confidence_score=1.0)), CAL)
    meter = draws[0].meter
    assert meter is not None
    # Inset on every side it touches, so it never runs into the exalt ring,
    # which is drawn 2px in at 2px wide.
    assert (meter.left, meter.right, meter.bottom) == (104, 146, 246)
    assert meter.fill_right == meter.right  # a full score fills the track


def test_the_meter_length_is_the_score():
    draws = build_draw_list(_state(_tile(confidence="Low", confidence_score=0.5)), CAL)
    meter = draws[0].meter
    assert meter.fill_right == meter.left + (meter.right - meter.left) // 2


def test_the_meter_colour_comes_from_the_shared_palette():
    """The same hex the browser tile uses -- which is the whole point of it."""
    draws = build_draw_list(_state(_tile(confidence="Medium", confidence_score=0.5)), CAL)
    assert draws[0].meter.fill == CONFIDENCE_COLOURS["Medium"]


def test_an_unsupported_map_gets_no_meter():
    """There is no estimate to be confident about, so an empty bar would lie."""
    unsupported = OverlayTile(x=0, y=0, supported=False, confidence="High", confidence_score=0.9)
    assert build_draw_list(_state(unsupported), CAL)[0].meter is None


def test_a_tile_with_no_confidence_recorded_gets_no_meter():
    assert build_draw_list(_state(_tile()), CAL)[0].meter is None


def test_a_label_with_no_score_still_draws_a_meter():
    """State files written before the score was carried keep working."""
    draws = build_draw_list(_state(_tile(confidence="High")), CAL)
    assert draws[0].meter is not None


def test_the_worst_possible_score_still_shows_a_sliver():
    draws = build_draw_list(_state(_tile(confidence="Low", confidence_score=0.0)), CAL)
    assert draws[0].meter.fill_right > draws[0].meter.left


def test_a_cell_too_small_for_both_keeps_the_price_and_drops_the_meter():
    tiny = Calibration(origin=(0, 0), cell_px=14.0, grid=(12, 12))
    draws = build_draw_list(_state(_tile(confidence="High", confidence_score=0.9)), tiny)
    assert draws[0].text  # the price is still drawn
    assert draws[0].meter is None


def test_the_meter_is_shifted_into_window_coordinates_with_its_tile():
    state = _state(_tile(x=5, y=7, confidence="High", confidence_score=0.5))
    draw = build_draw_list(state, CAL)[0]
    box = draw.relative_to(CAL.grid_rect()[:2])
    assert box.meter.left == draw.meter.left - 100
    assert box.meter.top == draw.meter.top - 200
    assert box.meter.fill_right == draw.meter.fill_right - 100
    # And still inside its own tile after the shift.
    assert box.left < box.meter.left < box.meter.right < box.right
