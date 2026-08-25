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
from src.ui.palette import VALUE_COLOURS

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
