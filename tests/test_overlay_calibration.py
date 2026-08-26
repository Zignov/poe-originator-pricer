"""Where the overlay thinks the stash tab is.

The failure this file exists to prevent is the quiet one: a calibration that is
wrong still draws a tidy grid of plausible prices, just over the wrong maps. So
the arithmetic is tested to the pixel, and so is the refusal to trust a
calibration saved at a different resolution.
"""
from __future__ import annotations

from src.config import ROOT, Config, load_config
from src.overlay.calibration import (
    MIN_CELL_PX,
    Calibration,
    resolution_warning,
    save_to_config,
)


# -- coordinate maths -------------------------------------------------------
def test_the_first_cell_starts_at_the_origin():
    cal = Calibration(origin=(100, 200), cell_px=50.0)
    assert cal.cell_rect(0, 0) == (100, 200, 150, 250)


def test_neighbouring_cells_share_an_edge_exactly_on_a_fractional_cell_size():
    """53.4px cells: each cell's right edge must be the next one's left edge.

    Rounding a cell's *size* instead of its edges is what accumulates drift, and
    five pixels of drift across twelve columns is a price on the wrong map.
    """
    cal = Calibration(origin=(0, 0), cell_px=53.4)
    for x in range(11):
        assert cal.cell_rect(x, 0)[2] == cal.cell_rect(x + 1, 0)[0]


def test_the_last_column_lands_where_the_grid_outline_ends():
    cal = Calibration(origin=(17, 9), cell_px=53.4, grid=(12, 12))
    assert cal.cell_rect(11, 11)[2:] == cal.grid_rect()[2:]


def test_a_multi_cell_item_spans_its_own_width_and_height():
    cal = Calibration(origin=(0, 0), cell_px=50.0)
    assert cal.cell_rect(1, 1, width=2, height=3) == (50, 50, 150, 200)


def test_a_negative_origin_is_honoured_for_a_monitor_left_of_the_primary_one():
    cal = Calibration(origin=(-1920, -120), cell_px=50.0)
    assert cal.cell_rect(0, 0) == (-1920, -120, -1870, -70)


def test_the_grid_outline_is_the_whole_grid():
    cal = Calibration(origin=(10, 10), cell_px=50.0, grid=(12, 24))
    assert cal.grid_rect() == (10, 10, 610, 1210)
    assert cal.size() == (600, 1200)


# -- the calibration keys ---------------------------------------------------
def test_an_arrow_press_moves_the_origin_by_one_pixel():
    assert Calibration(origin=(10, 10)).moved(-1, 0).origin == (9, 10)


def test_moving_does_not_change_the_cell_size():
    cal = Calibration(cell_px=53.4)
    assert cal.moved(5, 5).cell_px == 53.4


def test_resizing_grows_the_outline_by_the_pixels_asked_for():
    cal = Calibration(origin=(0, 0), cell_px=50.0, grid=(10, 10))
    assert cal.resized(d_width=10).size()[0] == 510


def test_resizing_on_both_axes_at_once_averages_rather_than_letting_one_win():
    cal = Calibration(cell_px=50.0, grid=(10, 10))
    # +10px wide implies 51.0, +20px tall implies 52.0.
    assert cal.resized(d_width=10, d_height=20).cell_px == 51.5


def test_resizing_by_nothing_leaves_the_calibration_untouched():
    cal = Calibration(cell_px=50.0)
    assert cal.resized() is cal


def test_a_cell_cannot_be_shrunk_away_to_nothing():
    """Otherwise a held-down key leaves a zero-area window and no way back."""
    cal = Calibration(cell_px=10.0, grid=(10, 10))
    assert cal.resized(d_width=-10_000).cell_px == MIN_CELL_PX


def test_opacity_is_clamped_to_something_you_can_still_see_through():
    assert Calibration().with_opacity(5.0).opacity == 1.0
    assert Calibration().with_opacity(-1.0).opacity == 0.1


# -- persistence ------------------------------------------------------------
def test_an_empty_config_yields_an_uncalibrated_default():
    cal = Calibration.from_mapping({})
    assert cal.grid == (12, 12)
    assert not cal.calibrated


def test_a_calibration_round_trips_through_the_config_mapping():
    cal = Calibration(
        origin=(-100, 40), cell_px=53.4, grid=(24, 24), opacity=0.4,
        monitor=1, hotkey_toggle="ctrl+shift+p", screen=(2560, 1440),
    )
    assert Calibration.from_mapping(cal.to_mapping()) == cal


def test_the_saved_block_has_the_shape_the_example_config_documents():
    keys = set(Calibration(screen=(1920, 1080)).to_mapping())
    assert keys == {"origin", "cell_px", "grid", "opacity", "monitor", "hotkey_toggle", "screen"}


def test_an_uncalibrated_calibration_writes_no_screen_key():
    """Its absence is the flag, so writing a placeholder would fake calibration."""
    assert "screen" not in Calibration().to_mapping()


def test_a_hand_mangled_config_falls_back_to_defaults_rather_than_raising():
    """The overlay has no console to print a traceback into."""
    cal = Calibration.from_mapping(
        {"origin": "somewhere", "cell_px": "big", "grid": None, "opacity": "half"}
    )
    assert cal == Calibration()


def test_saving_writes_the_overlay_block_to_the_config_file(tmp_path):
    cfg = Config({"league": "Allflame"})
    path = save_to_config(cfg, Calibration(origin=(3, 4), screen=(1920, 1080)), tmp_path / "c.yaml")
    reloaded = load_config(path)
    assert Calibration.from_config(reloaded).origin == (3, 4)
    assert Calibration.from_config(reloaded).calibrated


def test_the_shipped_defaults_parse_and_are_marked_uncalibrated():
    """config.example.yaml by name, not whatever config.yaml happens to exist.

    A bare `load_config()` reads the developer's own config.yaml when there is
    one, so this passed on a fresh clone and failed the moment anybody actually
    calibrated -- asserting about the machine it ran on rather than about what
    ships.
    """
    cal = Calibration.from_config(load_config(ROOT / "config.example.yaml"))
    assert cal.grid == (12, 12)
    assert not cal.calibrated


# -- resolution mismatch ----------------------------------------------------
def test_a_calibration_that_matches_the_screen_produces_no_warning():
    cal = Calibration(screen=(2560, 1440))
    assert resolution_warning(cal, (2560, 1440)) is None


def test_a_different_resolution_is_reported_with_both_numbers():
    cal = Calibration(screen=(2560, 1440))
    message = resolution_warning(cal, (1920, 1080))
    assert "2560x1440" in message and "1920x1080" in message


def test_never_having_been_calibrated_is_reported_before_anything_is_drawn():
    assert "never been calibrated" in resolution_warning(Calibration(), (1920, 1080))


def test_an_unknown_screen_size_does_not_block_a_saved_calibration():
    """The CLI checks before Tk exists, and cannot know the screen size yet."""
    assert resolution_warning(Calibration(screen=(1920, 1080)), None) is None
