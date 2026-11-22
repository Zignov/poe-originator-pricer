"""Appearance settings, and the claim that every one of them reaches a renderer.

A settings pane is the easiest place in an application to ship a control that
does nothing: the widget draws, the value is stored, and no test ever asks
whether anything read it. So these tests are almost all of the same shape --
set a preference, then assert on the thing that is actually drawn with it.

The web half is tested through the generated stylesheet and the generated
`.streamlit/config.toml` rather than through a browser, because those two
strings are the entire mechanism: one re-points the design tokens the page's
CSS is written against, the other is what Streamlit reads at startup.
"""
from __future__ import annotations

import pytest

from src.config import Config, defaults_for
from src.overlay.appearance import (
    MAX_FONT_SCALE,
    MAX_OUTLINE_WIDTH,
    MAX_STRIP_WIDTH,
    MIN_FONT_SCALE,
    MIN_STRIP_WIDTH,
    OverlayAppearance,
)
from src.overlay.calibration import MAX_OPACITY, MIN_CELL_PX, MIN_OPACITY, Calibration
from src.overlay.layout import (
    build_draw_list,
    dots_draw,
    font_size_for,
    meter_draw,
    ring_colour,
)
from src.overlay.state import EXALT_SKIP, EXALT_WORTH, OverlayState, OverlayTile
from src.ui import palette

CAL = Calibration(origin=(0, 0), cell_px=50.0)


def a_tile(**fields) -> OverlayTile:
    base = dict(
        x=0, y=0, price_chaos=120.0, label="120c",
        confidence="Medium", confidence_score=0.5, open_affixes=2,
    )
    return OverlayTile(**{**base, **fields})


def a_state(*tiles: OverlayTile) -> OverlayState:
    return OverlayState(tiles=tiles or (a_tile(),))


# ---------------------------------------------------------------------------
# The defaults are the constants they replaced
# ---------------------------------------------------------------------------
def test_the_shipped_appearance_draws_what_the_constants_always_drew():
    """The whole risk of making colours configurable, in one assertion.

    Every field here used to be a module constant. If a default has drifted,
    an install that has never opened the Settings page starts looking different
    for no reason the user did anything about.
    """
    from src.overlay import layout

    default = OverlayAppearance()
    assert default.ring_worth == layout.RING_WORTH
    assert default.ring_skip == layout.RING_SKIP
    assert default.outline == layout.TILE_OUTLINE
    assert default.muted_text == layout.MUTED_TEXT
    assert default.dots_fill == layout.DOTS_FILL
    assert default.meter_track == palette.CONFIDENCE_TRACK
    assert default.muted_tile_fill == layout.MUTED_FILL


def test_config_defaults_and_the_dataclass_defaults_agree():
    """Two places say what the shipped overlay looks like; they must not drift."""
    assert OverlayAppearance().to_mapping() == defaults_for("appearance.overlay")


def test_the_web_defaults_generate_no_stylesheet_at_all():
    """A default install must put exactly the markup on the page it always did."""
    web = defaults_for("appearance.web")
    assert palette.appearance_css(**web) == ""


# ---------------------------------------------------------------------------
# Every overlay control reaches the draw list
# ---------------------------------------------------------------------------
def test_the_ring_colours_follow_the_setting():
    chosen = OverlayAppearance().with_values(ring_worth="#ff00aa", ring_skip="#334455")
    assert ring_colour(EXALT_WORTH, chosen) == "#ff00aa"
    assert ring_colour(EXALT_SKIP, chosen) == "#334455"
    # A map that cannot take an orb still gets no ring, whatever the colours are.
    assert ring_colour("", chosen) == ""

    draws = build_draw_list(a_state(a_tile(exalt=EXALT_WORTH)), CAL, chosen)
    assert draws[0].ring == "#ff00aa"


def test_turning_the_confidence_meter_off_removes_it_from_the_draw_list():
    """Not merely "the meter is not drawn": there must be no rectangle for it.

    A meter suppressed in the renderer instead of here would still be reserving
    the bottom of the tile, and the free-affix badge stops short of whatever the
    meter took.
    """
    tile = a_tile()
    assert meter_draw(tile, 0, 0, 50, 50) is not None
    off = OverlayAppearance().with_values(show_meter=False)
    assert meter_draw(tile, 0, 0, 50, 50, off) is None
    assert build_draw_list(a_state(tile), CAL, off)[0].meter is None


def test_turning_the_dots_off_removes_the_badge():
    tile = a_tile()
    assert dots_draw(tile, 0, 0, 50, 50) is not None
    off = OverlayAppearance().with_values(show_dots=False)
    assert dots_draw(tile, 0, 0, 50, 50, None, off) is None
    assert build_draw_list(a_state(tile), CAL, off)[0].dots is None


def test_the_badge_reclaims_the_meters_room_when_the_meter_is_off():
    """The two readings are laid out against each other, so one going has to help.

    This is the assertion that the switches are wired to the *layout* rather
    than bolted on at the renderer: with the meter gone the badge's floor is the
    tile's own inner edge, so a cell too small for both can still carry the dots.
    """
    # Three dots on a 24px cell: two rows, which clear the tile's inner edge and
    # do not clear the meter. The one size where the switch is visible at all.
    small = a_tile(open_affixes=3)
    with_meter = dots_draw(small, 0, 0, 24, 24, meter_draw(small, 0, 0, 24, 24))
    no_meter = OverlayAppearance().with_values(show_meter=False)
    without = dots_draw(small, 0, 0, 24, 24, None, no_meter)
    assert with_meter is None and without is not None


def test_the_meter_track_and_the_dot_colour_follow_the_setting():
    chosen = OverlayAppearance().with_values(meter_track="#010101", dots_fill="#00ff00")
    draws = build_draw_list(a_state(), CAL, chosen)
    assert draws[0].meter.track == "#010101"
    assert draws[0].dots.fill == "#00ff00"


def test_the_unsupported_tile_follows_the_setting():
    chosen = OverlayAppearance().with_values(muted_text="#abcdef")
    unsupported = a_tile(price_chaos=None, supported=False, label="")
    draw = build_draw_list(a_state(unsupported), CAL, chosen)[0]
    assert draw.text_fill == "#abcdef"
    assert draw.text == "-", "an unpriceable map still gets a tile and a dash"


def test_an_empty_muted_fill_follows_the_shared_palette():
    """The one field that defers rather than storing a colour.

    A user who re-colours the "unknown" band in the web app has re-coloured the
    overlay's unsupported tile too, because there is one definition of it.
    """
    try:
        palette.apply_palette_overrides({"value_colours": {"unknown": "#654321"}})
        assert OverlayAppearance().muted_tile_fill == "#654321"
        assert build_draw_list(
            a_state(a_tile(price_chaos=None, supported=False)), CAL
        )[0].fill == "#654321"
    finally:
        palette.reset_palette()


def test_a_re_coloured_band_reaches_the_overlays_tiles():
    """The shared-palette promise: amber in the browser is amber over the game."""
    try:
        palette.apply_palette_overrides({"value_colours": {"exceptional": "#123456"}})
        draws = build_draw_list(a_state(a_tile(price_chaos=900.0)), CAL)
        assert draws[0].fill == "#123456"
    finally:
        palette.reset_palette()


# ---------------------------------------------------------------------------
# Font scale
# ---------------------------------------------------------------------------
def test_the_label_scale_is_not_swallowed_by_the_fit_clamp():
    """The bug this ordering exists to avoid: a slider that moves nothing.

    The size fitted to the cell is already near the 16pt ceiling at any ordinary
    cell pitch, so clamping after the multiplier would leave the control inert
    over most of its range.
    """
    fitted = font_size_for(53.4)
    assert font_size_for(53.4, 2.0) > fitted
    assert font_size_for(53.4, 0.5) < fitted
    assert font_size_for(53.4, 1.0) == fitted


def test_the_label_font_is_the_face_and_weight_that_were_chosen():
    plain = OverlayAppearance().with_values(font_family="Consolas", bold_labels=False)
    assert plain.label_font(11) == ("Consolas", 11, "normal")
    assert OverlayAppearance().label_font(11)[2] == "bold"


# ---------------------------------------------------------------------------
# Clamping: a bad value must not be able to break a window with no console
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("font_scale", 99, MAX_FONT_SCALE),
        ("font_scale", 0, MIN_FONT_SCALE),
        ("font_scale", "big", 1.0),
        ("outline_width", 99, MAX_OUTLINE_WIDTH),
        ("outline_width", -3, 0),
        ("strip_width", 10, MIN_STRIP_WIDTH),
        ("strip_width", 99999, MAX_STRIP_WIDTH),
        ("strip_anchor", "sideways", "above"),
        ("ring_worth", "not a colour", "#39d0d8"),
        ("ring_worth", "#GGG", "#39d0d8"),
        ("font_family", "", "Segoe UI"),
        ("bold_labels", "yes please", True),
    ],
)
def test_a_nonsense_value_falls_back_rather_than_reaching_tk(field, value, expected):
    assert getattr(OverlayAppearance.from_mapping({field: value}), field) == expected


def test_the_empty_string_is_a_real_answer_for_the_tint():
    """"" means "draw nothing", and must not be rejected as a bad colour.

    It is also the default, and the important one: an overlay whose empty cells
    are opaque is a screenshot of a stash tab on top of the stash tab.
    """
    assert OverlayAppearance.from_mapping({"tint": ""}).tint == ""
    assert OverlayAppearance().tint == ""
    assert OverlayAppearance.from_mapping({"tint": "#101317"}).tint == "#101317"
    assert OverlayAppearance.from_mapping({"tint": "nope"}).tint == ""


def test_a_setting_survives_the_round_trip_through_config():
    cfg = Config({})
    chosen = OverlayAppearance().with_values(
        font_family="Consolas", font_scale=1.4, show_dots=False, strip_width=700
    )
    from src.overlay.appearance import save_to_config

    save_to_config(cfg, chosen)
    assert OverlayAppearance.from_config(cfg) == chosen


def test_with_values_re_clamps_rather_than_trusting_the_caller():
    """One place decides what a legal value is, and it is not the widget."""
    assert OverlayAppearance().with_values(font_scale=99).font_scale == MAX_FONT_SCALE


# ---------------------------------------------------------------------------
# The calibration knobs the page edits
# ---------------------------------------------------------------------------
def test_opacity_cell_and_origin_are_clamped_the_way_the_overlay_needs():
    assert Calibration().with_opacity(5.0).opacity == MAX_OPACITY
    assert Calibration().with_opacity(-1).opacity == MIN_OPACITY
    # A cell of zero is a window with no area and no way back to one.
    assert Calibration().with_cell_px(0).cell_px == MIN_CELL_PX
    # Negative origins are correct, not merely tolerated: a monitor left of the
    # primary one genuinely starts below zero on the virtual desktop.
    assert Calibration().with_origin(-1920, -40).origin == (-1920, -40)


def test_the_cell_size_really_moves_the_tiles_and_the_label():
    small = Calibration(origin=(0, 0), cell_px=20.0)
    large = Calibration(origin=(0, 0), cell_px=80.0)
    assert build_draw_list(a_state(), small)[0].width == 20
    assert build_draw_list(a_state(), large)[0].width == 80
    assert font_size_for(80.0) > font_size_for(20.0)


def test_the_origin_really_moves_the_window():
    from src.overlay.layout import window_geometry

    _w, _h, x, y = window_geometry(Calibration(origin=(-1920, 40), cell_px=50.0))
    assert (x, y) == (-1920, 40)


# ---------------------------------------------------------------------------
# The web half
# ---------------------------------------------------------------------------
def test_the_stylesheet_re_points_the_tokens_the_pages_css_is_written_against():
    """It overrides `:root`; it does not restate the design system.

    Every rule in src/ui/common.py is written against these token names, so a
    token that stopped matching would leave a theme that changes nothing.
    """
    from pathlib import Path

    css = palette.appearance_css(theme="light", accent="#aa3311")
    sheet = (
        Path(__file__).resolve().parent.parent / "src" / "ui" / "common.py"
    ).read_text(encoding="utf-8")
    for token in ("ground", "panel", "panel-lift", "rule", "ink", "ink-dim", "brass"):
        assert f"--{token}:" in css
        assert f"--{token}:" in sheet, f"common.py no longer defines --{token}"
    assert "--ground: #f4f2ed" in css, "the light tokens did not reach the sheet"
    assert "--brass: #aa3311" in css
    # The translucent variant is derived rather than asked for, so a custom
    # accent looks deliberate everywhere it is used at partial strength.
    assert "rgba(170,51,17,0.32)" in css


def test_the_density_and_the_text_size_are_really_in_the_stylesheet():
    compact = palette.appearance_css(density="compact")
    assert palette.DENSITIES["compact"]["top"] in compact
    assert palette.DENSITIES["comfortable"]["top"] not in compact
    assert "font-size: 115.0%" in palette.appearance_css(font_scale=1.15)


def test_the_text_size_is_clamped_to_where_the_stash_grid_still_lays_out():
    assert palette.clamp_font_scale(9) == palette.MAX_FONT_SCALE
    assert palette.clamp_font_scale(0.1) == palette.MIN_FONT_SCALE
    assert palette.clamp_font_scale("large") == 1.0


def test_follow_system_writes_no_base_so_streamlit_can_follow_the_browser():
    """The mechanism, not an omission: naming a base is what would stop it."""
    toml = palette.streamlit_theme_toml("system", "#aa3311", "Georgia, serif")
    assert "base =" not in toml
    assert "backgroundColor" not in toml
    # The two that are not a light-or-dark decision are still written.
    assert 'primaryColor = "#aa3311"' in toml
    assert 'font = "Georgia, serif"' in toml


def test_the_generated_theme_matches_the_one_shipped_in_the_repo():
    """The file in the repo must be what this page would write for the defaults.

    Otherwise the first use of the Appearance tab silently restyles an app
    nobody asked to restyle.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    shipped = (root / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    web = defaults_for("appearance.web")
    generated = palette.streamlit_theme_toml(web["theme"], web["accent"], web["font"])

    def keys(text: str) -> dict[str, str]:
        return dict(re.findall(r'^(\w+) = "(.*)"', text, re.M))

    assert keys(shipped) == keys(generated)


def test_the_streamlit_theme_takes_its_colours_from_the_same_tokens():
    """One definition of "dark", not two that have to be kept in step by hand."""
    toml = palette.streamlit_theme_toml("dark")
    dark = palette.WEB_THEMES["dark"]
    assert f'backgroundColor = "{dark["ground"]}"' in toml
    assert f'secondaryBackgroundColor = "{dark["panel"]}"' in toml
    assert f'textColor = "{dark["ink"]}"' in toml


# ---------------------------------------------------------------------------
# The shared palette
# ---------------------------------------------------------------------------
def test_overrides_are_applied_in_place_so_every_importer_sees_them():
    """The mechanism the whole shared palette rests on.

    `src/ui/stash_grid.py` and `src/overlay/layout.py` both bound the dictionary
    object at import. Rebinding the name here would leave both of them holding
    the old one, and the setting would appear to do nothing on either page.
    """
    original = palette.VALUE_COLOURS
    try:
        palette.apply_palette_overrides({"value_colours": {"high": "#123456"}})
        assert palette.VALUE_COLOURS is original
        assert original["high"] == "#123456"
    finally:
        palette.reset_palette()


def test_an_unparseable_colour_is_dropped_rather_than_stored():
    try:
        palette.apply_palette_overrides(
            {"value_colours": {"high": "puce", "low": "#123456", "not a band": "#fff"}}
        )
        assert palette.VALUE_COLOURS["high"] == palette.DEFAULT_VALUE_COLOURS["high"]
        assert palette.VALUE_COLOURS["low"] == "#123456"
        assert "not a band" not in palette.VALUE_COLOURS
    finally:
        palette.reset_palette()


def test_only_what_changed_is_written_back():
    """A release's new band colour has to reach somebody who once set a different one."""
    try:
        assert palette.palette_overrides_only() == {}
        palette.apply_palette_overrides({"confidence_colours": {"Low": "#123456"}})
        assert palette.palette_overrides_only() == {
            "confidence_colours": {"Low": "#123456"}
        }
    finally:
        palette.reset_palette()


def test_reset_restores_every_dictionary():
    palette.apply_palette_overrides(
        {
            "value_colours": {"high": "#111111"},
            "confidence_colours": {"Low": "#222222"},
            "text_on_value": {"negligible": "#333333"},
        }
    )
    palette.reset_palette()
    assert palette.VALUE_COLOURS == palette.DEFAULT_VALUE_COLOURS
    assert palette.CONFIDENCE_COLOURS == palette.DEFAULT_CONFIDENCE_COLOURS
    assert palette.TEXT_ON_VALUE == palette.DEFAULT_TEXT_ON_VALUE


def test_a_cold_process_picks_up_a_saved_palette(tmp_path, monkeypatch):
    """The overlay is a separate process with no Streamlit in it.

    An override applied only by the Settings page would reach the web grid and
    never the thing drawn over the game, which is the one place the two agreeing
    actually matters.
    """
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = tmp_path / "config.yaml"
    config.write_text(
        "appearance:\n  palette:\n    value_colours:\n      high: '#123456'\n",
        encoding="utf-8",
    )
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(root)!r})
        import src.config as config
        config.read_raw_config = lambda *a, **k: __import__("yaml").safe_load(
            open({str(config)!r}, encoding="utf-8")
        )
        from src.ui.palette import VALUE_COLOURS, apply_palette_overrides
        apply_palette_overrides(config.read_raw_config()["appearance"]["palette"])
        print(VALUE_COLOURS["high"])
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(root)
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "#123456"


def test_a_broken_config_file_cannot_stop_the_palette_importing():
    """It is read at import, in a process with no console to print the error in."""
    from src.config import read_raw_config

    assert read_raw_config(__file__) == {} or isinstance(read_raw_config(__file__), dict)
