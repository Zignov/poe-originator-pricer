"""The two things about the overlay window that fail *silently* when wrong.

Everything else in src/overlay/ is plain data and is tested without a display.
These two are not, and both went wrong in a way no unit test could see, because
neither raises: the overlay opened, reported the right number of maps in its
status line, and drew nothing at all on a screen nobody could assert about.

1. **Click-through is really applied.** `set_click_through` writes a window
   ex-style, and writing it before Tk has mapped the window is discarded --
   along with the layer attributes that make the window paint. The symptom is a
   fully invisible overlay, so the assertion is on the ex-style bit rather than
   on any pixel: if the bit is there, the call happened after the map.

2. **Calibration keys reach the handler.** The overlay window is
   override-redirect and click-through and cannot reliably hold the keyboard
   over a full-screen game, so the keys are bound for the whole application and
   the focus is parked on the control strip. Binding them to the overlay window
   alone answers one keypress and then goes deaf.

Skipped anywhere there is no display, or no Tk that can make a transparent
window -- which is every CI box and every non-Windows machine.
"""
from __future__ import annotations

import pytest

from src.config import Config
from src.overlay.calibration import Calibration
from src.overlay.state import OverlayState, OverlayTile, write_state

pytest.importorskip("tkinter")

WS_EX_TRANSPARENT = 0x00000020


#: The calibration every test starts from, so a test can assert on whole pixels.
BASELINE = Calibration(origin=(0, 0), cell_px=50.0)


@pytest.fixture(scope="module")
def _window(tmp_path_factory):
    """One overlay window for the whole module, and one Tk interpreter with it.

    Module-scoped on purpose. Creating and destroying a `Tk()` per test works
    for two or three and then starts failing to find `tk.tcl` -- a Tcl-level
    problem with repeated interpreter creation, not with anything here, but it
    turns these tests into ones that skip themselves at random.
    """
    from src.overlay.window import OverlayUnavailable, OverlayWindow

    state_path = tmp_path_factory.mktemp("overlay") / "overlay_state.json"
    write_state(
        OverlayState(
            tiles=(
                OverlayTile(x=0, y=0, price_chaos=25.0, label="25c"),
                OverlayTile(x=3, y=2, price_chaos=90.0, label="90c"),
            ),
            tab_names=("Originator Maps",),
        ),
        state_path,
    )
    cfg = Config({"overlay": BASELINE.to_mapping()})
    try:
        window = OverlayWindow(cfg, Calibration.from_config(cfg), state_path)
    except OverlayUnavailable as exc:
        pytest.skip(f"no overlay-capable display here: {exc}")
    window.reload()
    window.root.update()
    yield window
    # quit(), not destroy(): it is quit() that cancels the polling timers, and a
    # timer left pending fires into a dead interpreter and reports it on stderr.
    window.quit()


@pytest.fixture
def overlay(_window):
    """The shared window, wound back to the baseline before and after each test."""
    _reset(_window)
    yield _window
    _reset(_window)


def _reset(window) -> None:
    if window.calibrating:
        window._finish_calibration(save=False)
    window.cfg.set("overlay", BASELINE.to_mapping())
    window.calibration = BASELINE
    window._pending = None
    window.visible = True
    window._place()
    window.redraw()
    window.root.update()


def _ex_style(window) -> int:
    import ctypes

    from src.overlay.window import GWL_EXSTYLE, _hwnd

    user32 = ctypes.windll.user32
    get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    get.restype = ctypes.c_ssize_t
    return int(get(ctypes.c_void_p(_hwnd(window)), GWL_EXSTYLE))


@pytest.mark.skipif("sys.platform != 'win32'")
def test_the_overlay_is_click_through_by_the_time_it_is_on_screen(overlay):
    """The ex-style bit has to survive Tk mapping the window, not just be set."""
    assert _ex_style(overlay.window) & WS_EX_TRANSPARENT


@pytest.mark.skipif("sys.platform != 'win32'")
def test_calibrating_lets_clicks_land_again_and_puts_them_back_afterwards(overlay):
    """You cannot drag a rectangle you cannot click."""
    overlay.start_calibration()
    overlay.root.update()
    assert not _ex_style(overlay.window) & WS_EX_TRANSPARENT

    overlay._finish_calibration(save=False)
    overlay.root.update()
    assert _ex_style(overlay.window) & WS_EX_TRANSPARENT


def _press(overlay, keysym: str, shift: bool = False) -> None:
    """A keypress on whatever currently holds the focus, as the OS would send it."""
    focused = overlay.root.focus_get() or overlay.strip.window
    focused.event_generate(f"<KeyPress-{keysym}>", state=1 if shift else 0)
    overlay.root.update()


def test_every_arrow_press_moves_the_grid_not_just_the_first(overlay):
    """The regression: binding on the overlay window alone answered one key."""
    overlay.start_calibration()
    overlay.root.update()

    for _ in range(4):
        _press(overlay, "Left")
    for _ in range(3):
        _press(overlay, "Down")

    assert overlay.calibration.origin == (-4, 3)


def test_shift_and_an_arrow_resizes_instead_of_moving(overlay):
    overlay.start_calibration()
    overlay.root.update()
    before = overlay.calibration.cell_px

    for _ in range(12):
        _press(overlay, "Right", shift=True)

    assert overlay.calibration.cell_px > before
    assert overlay.calibration.origin == (0, 0)


def test_escape_cancels_back_to_the_saved_calibration(overlay):
    overlay.start_calibration()
    overlay.root.update()
    _press(overlay, "Left")
    _press(overlay, "Left")

    _press(overlay, "Escape")

    assert not overlay.calibrating
    assert overlay.calibration.origin == (0, 0)


def test_enter_saves_and_stamps_the_screen_it_was_confirmed_on(
    overlay, tmp_path, monkeypatch
):
    """The save is redirected: the real config.yaml is not a test fixture."""
    import src.overlay.window as window_module

    written = tmp_path / "config.yaml"
    monkeypatch.setattr(
        window_module,
        "save_to_config",
        lambda cfg, calibration: _save_to(cfg, calibration, written),
    )

    overlay.start_calibration()
    overlay.root.update()
    _press(overlay, "Right")

    _press(overlay, "Return")

    assert not overlay.calibrating
    assert overlay.calibration.origin == (1, 0)
    assert overlay.calibration.screen == overlay.screen
    assert written.exists()


def _save_to(cfg, calibration, path):
    from src.overlay.calibration import save_to_config

    return save_to_config(cfg, calibration, path)


def test_the_strips_quit_shortcut_stands_down_while_calibrating(overlay):
    """"q" over a half-aligned grid must not close the window."""
    overlay.start_calibration()
    overlay.root.update()

    _press(overlay, "q")

    assert overlay.calibrating
    assert overlay.root.winfo_exists()
