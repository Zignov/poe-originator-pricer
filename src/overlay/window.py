"""The only module that imports tkinter: a thin renderer over src/overlay/layout.py.

Everything with a decision in it -- which colour, which rectangle, which
warning -- has already been made by the time anything here runs. This module
owns three things instead: the Windows window flags that make a window
transparent, always on top and click-through; the timers that watch the state
file and drain the hotkey queue; and the calibration screen.

How the transparency is done, and why in that order:

- `overrideredirect(True)` removes the frame. A title bar over the stash would
  cover the top row of maps, and there is nothing to drag anyway -- the position
  comes from the calibration.
- `-transparentcolor` makes one sentinel colour vanish *completely*, taking the
  mouse with it. That is what leaves empty stash cells fully see-through.
- `-alpha` then fades everything that is left, so the tiles sit over the maps
  instead of hiding them.
- `WS_EX_TRANSPARENT` on top of `WS_EX_LAYERED` sends every click through to the
  window underneath. This is off during calibration -- you cannot drag a
  rectangle you cannot click -- and on at every other moment.

Honest limitation, stated here as well as in the README: none of this can draw
over a game running in **fullscreen exclusive** mode, because that mode hands
the display to the game and takes the desktop compositor out of the path. Use
windowed fullscreen. Nothing in this file interacts with the game client in any
way -- it does not read it, hook it, or send it anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.config import Config, load_config
from src.overlay.appearance import DEFAULT_APPEARANCE, OverlayAppearance
from src.overlay.calibration import (
    Calibration,
    resolution_warning,
    save_to_config,
)
from src.overlay.hotkeys import HotkeyListener
from src.overlay.layout import (
    build_draw_list,
    font_size_for,
    geometry_string,
    grid_mismatch,
    window_geometry,
)
from src.overlay.analyze import AnalyzeWorker
from src.overlay.profiles import DEFAULT_PROFILE, OverlayProfile, profile_for
from src.overlay.state import (
    EXALT_SKIP,
    OverlayState,
    StateWatcher,
    state_path_for,
)

#: The colour that is made fully transparent. Any colour would do as long as
#: nothing else on the canvas uses it; this near-black is picked because it is
#: not in the palette and not a plausible ring colour, so no tile can ever
#: accidentally punch a hole in itself.
SENTINEL = "#010203"

#: How often the state file is checked, and how often the hotkey queue is
#: drained. The file poll is slow on purpose -- the user presses a button in a
#: browser and then looks at the game, so half a second is imperceptible, and
#: it keeps the overlay off the CPU while the game wants it.
STATE_POLL_MS = 500
HOTKEY_POLL_MS = 80

#: The control strip's width. Sized to its buttons: five of them plus the
#: profile label do not fit in the four-button strip this started as, and a
#: clipped "quit" is a button people cannot find. The default only -- the
#: Settings page can widen it, within the bounds in src/overlay/appearance.py.
STRIP_WIDTH = DEFAULT_APPEARANCE.strip_width

#: The strip's own height, and the gap it keeps from the grid. Not settings:
#: they are what two rows of 8pt text plus padding measures, and a strip that
#: does not fit its own contents is not a preference anybody has.
STRIP_HEIGHT = 52
STRIP_GAP = 6

#: The chrome of the strip itself. Slate, matching the web app's panel colours,
#: because the strip is structure -- it carries no price and no verdict, so
#: nothing on it may compete with the tiles for the eye.
STRIP_BG = "#15181d"
STRIP_BUTTON_BG = "#242a33"
STRIP_FG = "#f4f4f5"
STRIP_STATUS_FG = "#a9b0ba"


class OverlayUnavailable(RuntimeError):
    """No display, no tkinter, or no way to make a transparent window here."""


# ---------------------------------------------------------------------------
# Windows window flags
GWL_EXSTYLE = -20
#: Set by Tk when `-transparentcolor` / `-alpha` are applied, and owned by Tk
#: thereafter -- see the note in `set_click_through`. Named here so the ex-style
#: values printed while debugging are readable.
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
#: Keeps the overlay out of the taskbar and out of Alt-Tab. Alt-Tabbing onto a
#: click-through window would put the game behind a window that cannot be
#: clicked back out of. Tk already sets this for an override-redirect toplevel,
#: so nothing here has to ask for it.
WS_EX_TOOLWINDOW = 0x00000080
#: Stops a window becoming the foreground window when it is shown or clicked.
#: Not the same thing as `-topmost`, which only controls z-order: a topmost
#: window can still take focus, and a window that takes focus from a
#: fullscreen-windowed game minimises it. See `set_no_activate`.
WS_EX_NOACTIVATE = 0x08000000

#: `SHQueryUserNotificationState` says a Direct3D application is running in
#: fullscreen-exclusive mode. That mode hands the display to the game and no
#: layered window can draw over it, so the overlay is running correctly and is
#: simply invisible -- the single worst failure this thing has, because there
#: is nothing on screen to explain it. Documented at
#: learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryusernotificationstate
QUNS_RUNNING_D3D_FULL_SCREEN = 3


def fullscreen_exclusive_active() -> bool | None:
    """Is a game currently holding the display in fullscreen-exclusive mode?

    Returns None when the question cannot be answered -- not Windows, or the
    call failed -- which is deliberately distinct from False. A caller should
    warn on True and stay quiet on None rather than claiming the display is
    fine when it does not know.

    This asks Windows about *any* fullscreen-exclusive D3D application, not
    specifically Path of Exile. That is the right question anyway: whatever is
    holding the display is what the overlay cannot draw over.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        state = ctypes.c_int()
        # S_OK is 0; anything else means the state was not filled in.
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return None
        return state.value == QUNS_RUNNING_D3D_FULL_SCREEN
    except (OSError, AttributeError, ValueError):
        return None


#: What to tell the user when the check comes back True. Kept here so the
#: overlay's own status strip and the web page's launch button say the same
#: thing rather than drifting into two half-explanations.
FULLSCREEN_WARNING = (
    "Path of Exile looks like it is in fullscreen-exclusive mode. No overlay "
    "can draw over that -- switch the game to windowed fullscreen and the "
    "tiles will appear."
)


def _hwnd(window) -> int:
    """The real top-level window handle behind a Tk window."""
    import ctypes

    handle = window.winfo_id()
    parent = ctypes.windll.user32.GetParent(handle)
    return int(parent or handle)


def set_click_through(window, enabled: bool) -> None:
    """Add or remove WS_EX_TRANSPARENT, so clicks pass to the game or do not.

    Two things here are load-bearing, and getting either wrong makes the whole
    overlay invisible rather than merely un-clickable -- a layered window whose
    layer attributes are not set draws *nothing at all*, so the failure looks
    like "the overlay never opened" instead of "a window flag is wrong".

    - **Only the WS_EX_TRANSPARENT bit is touched.** The rest of the ex-style,
      WS_EX_LAYERED included, belongs to Tk: it is Tk that sets the layered bit
      when `-transparentcolor` and `-alpha` are applied, and that pairs it with
      the `SetLayeredWindowAttributes` call which gives the layer its colour key
      and opacity. Re-asserting WS_EX_LAYERED from out here is the documented
      way to lose those attributes.
    - **The window must already exist on screen.** `winfo_id()` forces the
      handle into being, so calling this on an unmapped window creates the HWND
      ahead of Tk's own mapping and the ex-style written here is discarded when
      Tk maps it -- taking the layer attributes with it. `update_idletasks()`
      lets Tk finish mapping first, which costs nothing once it already has.
    """
    if sys.platform != "win32":  # pragma: no cover - Windows-only feature
        return
    import ctypes

    window.update_idletasks()

    user32 = ctypes.windll.user32
    # SetWindowLongPtrW only exists on 64-bit; the styles are the same either way.
    get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get.restype = ctypes.c_ssize_t
    put.restype = ctypes.c_ssize_t
    put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

    handle = _hwnd(window)
    style = int(get(ctypes.c_void_p(handle), GWL_EXSTYLE))
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    put(ctypes.c_void_p(handle), GWL_EXSTYLE, style)


def set_no_activate(window, enabled: bool = True) -> None:
    """Add WS_EX_NOACTIVATE, so showing this window cannot steal focus.

    `-topmost` and `overrideredirect` put a window above the game. Neither
    stops it *becoming the foreground window* when it is shown or clicked, and
    for a client in fullscreen-windowed mode that is the difference between a
    price appearing and the game minimising. `lift()` alone is not enough
    either: it reorders windows, and on Windows showing a previously withdrawn
    toplevel can still activate it.

    Mouse clicks still arrive -- WS_EX_NOACTIVATE suppresses activation, not
    input -- which is what lets the popup stay dismissable by clicking it.

    Same two constraints as `set_click_through`, for the same reasons: only this
    one bit is touched, and the window has to be mapped already or Tk discards
    the ex-style written here when it maps it.
    """
    if sys.platform != "win32":  # pragma: no cover - Windows-only feature
        return
    import ctypes

    window.update_idletasks()

    user32 = ctypes.windll.user32
    get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get.restype = ctypes.c_ssize_t
    put.restype = ctypes.c_ssize_t
    put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

    handle = _hwnd(window)
    style = int(get(ctypes.c_void_p(handle), GWL_EXSTYLE))
    if enabled:
        style |= WS_EX_NOACTIVATE
    else:
        style &= ~WS_EX_NOACTIVATE
    put(ctypes.c_void_p(handle), GWL_EXSTYLE, style)


# ---------------------------------------------------------------------------
class OverlayWindow:
    """The overlay proper, plus the little control strip that drives it."""

    def __init__(
        self,
        cfg: Config,
        calibration: Calibration,
        state_path: Path | str | None = None,
        appearance: OverlayAppearance | None = None,
    ):
        import tkinter as tk

        self._tk = tk
        self.cfg = cfg
        self.calibration = calibration
        # Read from the config once, here, rather than looked up per redraw:
        # the redraw runs on a timer over a game, and it is not the place to be
        # re-parsing a settings block. Changing an appearance therefore takes a
        # restart of the overlay, which is what the Settings page says.
        self.appearance = appearance or OverlayAppearance.from_config(cfg)
        # Which surface this window is for comes from the calibration it was
        # handed, not from a second argument that could disagree with it.
        self.profile: OverlayProfile = calibration.profile_spec
        self.state_path = Path(state_path or state_path_for(self.profile))
        self.state: OverlayState | None = None
        self.watcher = StateWatcher(self.state_path)
        self.visible = True
        self.calibrating = False
        self._pending: Calibration | None = None
        # Held between the worker finishing and the watcher noticing the file it
        # wrote, so the strip ends up saying "42 maps re-priced" rather than the
        # generic summary line the redraw would otherwise overwrite it with.
        self._analysis_note = ""

        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - needs a headless box
            raise OverlayUnavailable(f"no display available for the overlay: {exc}") from exc
        self.root.withdraw()

        self.screen = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())

        self.window = tk.Toplevel(self.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-transparentcolor", SENTINEL)
        except tk.TclError as exc:  # pragma: no cover - non-Windows Tk
            raise OverlayUnavailable(
                "this Tk build has no -transparentcolor, so a see-through overlay "
                f"cannot be made here: {exc}"
            ) from exc
        self.window.attributes("-alpha", calibration.opacity)
        self.window.configure(bg=SENTINEL)

        self.canvas = tk.Canvas(
            self.window, bg=SENTINEL, highlightthickness=0, borderwidth=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.strip = _ControlStrip(self, tk)

        # One analysis at a time, on a thread, because a fetch plus a model
        # pass is seconds and this window sits on top of a game.
        self.analyzer = AnalyzeWorker(cfg)

        self.hotkeys = HotkeyListener(
            {
                "toggle": calibration.hotkey_toggle,
                "analyze": calibration.hotkey_analyze,
            }
        )
        self.hotkeys.start()

        self._place()
        set_click_through(self.window, True)
        # Fullscreen-exclusive first: when it is on, nothing else the strip
        # could say matters, because the tiles are not on screen to be judged.
        # The strip is an ordinary window, so it stays visible even then --
        # which makes it the only place that can explain the empty screen.
        self.strip.report(
            FULLSCREEN_WARNING
            if fullscreen_exclusive_active()
            else (resolution_warning(calibration, self.screen) or "")
        )

        # Held so quitting can cancel them. A pending `after` fires into an
        # interpreter that no longer exists and Tcl reports it on stderr as an
        # "invalid command name", which looks like a crash on the way out.
        self._timers: dict[str, str] = {}
        #: Reported once. A chord that could not be registered will not start
        #: working, so repeating the warning every poll would bury the state
        #: summary the strip exists to show.
        self._hotkey_errors_shown = False
        self._timers["state"] = self.root.after(0, self._poll_state)
        self._timers["hotkeys"] = self.root.after(HOTKEY_POLL_MS, self._poll_hotkeys)

    # -- placement and drawing -------------------------------------------
    def _place(self) -> None:
        width, height, x, y = window_geometry(self.calibration, self.state)
        self.window.geometry(geometry_string(width, height, x, y))
        self.window.attributes("-alpha", self.calibration.opacity)

    def redraw(self) -> None:
        self.canvas.delete("all")
        if self.calibrating:
            self._draw_calibration()
            return
        if not self.visible or self.state is None:
            return

        origin = self.calibration.grid_rect()[:2]
        appearance = self.appearance
        font = appearance.label_font(
            font_size_for(self.calibration.cell_px, appearance.font_scale)
        )
        # An optional flat wash across the whole grid, under everything else.
        # Off by default, and the default is the one that matters: an overlay
        # whose empty cells are opaque is a screenshot of a stash tab sitting on
        # top of the stash tab. Some people want the dimmed panel anyway.
        if appearance.tint:
            width, height, _x, _y = window_geometry(self.calibration, self.state)
            self.canvas.create_rectangle(
                0, 0, width, height, fill=appearance.tint, outline=""
            )
        # Tk reads width=0 as "hairline", not "none", so an outline the user has
        # turned off has to go out as no colour rather than as no width.
        outline = appearance.outline if appearance.outline_width else ""
        for draw in build_draw_list(self.state, self.calibration, appearance):
            box = draw.relative_to(origin)
            self.canvas.create_rectangle(
                box.left, box.top, box.right, box.bottom,
                fill=box.fill, outline=outline,
                width=max(1, appearance.outline_width),
            )
            if box.ring:
                dash = (3, 2) if (box.tile and box.tile.exalt == EXALT_SKIP) else ()
                self.canvas.create_rectangle(
                    box.left + 2, box.top + 2, box.right - 2, box.bottom - 2,
                    outline=box.ring, width=2, dash=dash,
                )
            if box.meter:
                # Track first, then the fill on top of it: two rectangles rather
                # than one, so an almost-empty meter still reads as a meter.
                self.canvas.create_rectangle(
                    box.meter.left, box.meter.top, box.meter.right, box.meter.bottom,
                    fill=box.meter.track, outline="",
                )
                self.canvas.create_rectangle(
                    box.meter.left, box.meter.top, box.meter.fill_right, box.meter.bottom,
                    fill=box.meter.fill, outline="",
                )
            if box.dots:
                # No outline: a 6px circle with a 1px border is a ring, and a
                # ring in the corner of a tile already means something else here.
                for dot in box.dots.dots:
                    self.canvas.create_oval(*dot, fill=box.dots.fill, outline="")
            self.canvas.create_text(
                (box.left + box.right) / 2,
                (box.top + box.bottom) / 2,
                text=box.text,
                fill=box.text_fill,
                font=font,
            )

    # -- the state file ---------------------------------------------------
    def _poll_state(self) -> None:
        # The analysis result is collected here, on the Tk thread, rather than
        # by a callback from the worker: every widget this touches belongs to
        # this thread and Tk is not thread-safe. A failed analysis reports and
        # is otherwise ignored -- the tiles already on screen are the last good
        # prices, and blanking them because a fetch timed out would lose real
        # information to report an error that the strip is already showing.
        finished = self.analyzer.poll()
        if finished is not None and not finished.ok:
            self.strip.report(finished.message)
        elif finished is not None:
            self._analysis_note = finished.message

        state = self.watcher.poll()
        if state is not None:
            grew = self.state is None or tuple(state.grid) != tuple(self.state.grid)
            self.state = state
            if grew:
                # Before the redraw, not after: a tab wider than the
                # calibration is drawn into whatever the window currently is,
                # and the tiles past its edge are simply not on screen.
                self._place()
            mismatch = grid_mismatch(self.calibration, state)
            self.strip.report(mismatch or self._analysis_note or state.summary())
            self._analysis_note = ""
            self.redraw()
        elif self.watcher.error:
            self.strip.report(self.watcher.error)
        self._timers["state"] = self.root.after(STATE_POLL_MS, self._poll_state)

    def reload(self) -> None:
        """Force a re-read even if the stamp has not moved."""
        self.watcher = StateWatcher(self.state_path)
        state = self.watcher.poll()
        if state is not None:
            self.state = state
            self.strip.report(state.summary())
        else:
            self.strip.report(self.watcher.error or f"nothing to read at {self.state_path}")
        self.redraw()

    # -- controls ----------------------------------------------------------
    def _poll_hotkeys(self) -> None:
        # Registration happens after start() returns, so the list fills in
        # asynchronously and has to be polled rather than read once. Until this
        # ran, a chord another application already owned -- ctrl+alt+i is
        # commonly taken -- failed silently: the key did nothing and nothing
        # anywhere said why, which is indistinguishable from a broken overlay.
        if self.hotkeys.errors and not self._hotkey_errors_shown:
            self._hotkey_errors_shown = True
            self.strip.report("hotkey unavailable -- " + "; ".join(self.hotkeys.errors))
        for name in self.hotkeys.poll():
            if name == "toggle":
                self.toggle()
            elif name == "analyze":
                self.analyze()
        self._timers["hotkeys"] = self.root.after(HOTKEY_POLL_MS, self._poll_hotkeys)

    def analyze(self) -> None:
        """Read this surface again and re-price it, without leaving the game.

        Everything after "write the file" is the ordinary redraw path: the
        worker writes the state file and the watcher three lines above notices
        it, exactly as it notices an export from the browser. Analysing is a way
        of writing that file, not a second way of drawing.
        """
        if self.calibrating:
            # The grid on screen is an alignment tool right now, not prices.
            # Repainting it mid-drag would lose the drag.
            self.strip.report("finish calibrating first (Enter saves, Esc cancels)")
            return
        self.strip.report(self.analyzer.start(self.state, self.profile, self.state_path))

    def toggle(self) -> None:
        self.visible = not self.visible
        self.strip.report("overlay hidden" if not self.visible else "overlay shown")
        self.redraw()

    def quit(self) -> None:
        self.hotkeys.stop()
        self.analyzer.close()
        for job in self._timers.values():
            try:
                self.root.after_cancel(job)
            except self._tk.TclError:  # pragma: no cover - already fired
                pass
        self._timers.clear()
        try:
            self.root.destroy()
        except self._tk.TclError:  # pragma: no cover - already torn down
            # Quitting twice -- the button and then the window manager, say --
            # is a normal way to close a window, not an error worth showing.
            pass

    # -- calibration -------------------------------------------------------
    def start_calibration(self) -> None:
        """Turn the overlay into a draggable outline until Enter or Esc.

        Click-through comes off first. It has to: a calibration screen you
        cannot focus is one you cannot type into, and the arrow keys are the
        whole interface.

        The keys are bound with `bind_all` rather than on the overlay window,
        and the focus is handed to the *control strip*, for the same reason:
        over a game that keeps taking the foreground back, the only window in
        this app the user can reliably give focus to is the one they can see and
        click. Binding on the overlay alone means calibration answers exactly
        one keypress and then goes deaf, because the focus has since moved to
        the strip or been taken by the game and never comes back on its own.
        """
        self.calibrating = True
        self._pending = self.calibration
        self.visible = True
        set_click_through(self.window, False)
        self.window.attributes("-alpha", max(0.75, self.calibration.opacity))
        self.root.bind_all("<Key>", self._calibration_key)
        self.window.bind("<Button-1>", lambda _e: self._claim_keyboard())
        self._claim_keyboard()
        self.strip.report(
            "calibrating: arrows move, shift+arrows resize, Enter saves. "
            "If the keys do nothing, click this strip first."
        )
        self.redraw()

    def _claim_keyboard(self) -> None:
        """Put the keyboard back on the strip, which is the window with a body.

        Called after every calibration keypress as well as at the start. A
        single `focus_force` at the start is not enough: raising and moving a
        topmost override-redirect window, and a full-screen game underneath that
        wants the foreground, both end with the focus somewhere else.
        """
        try:
            self.strip.window.focus_force()
        except self._tk.TclError:  # pragma: no cover - torn down mid-calibration
            pass

    def _calibration_key(self, event) -> str | None:
        """Handle one calibration keypress; "break" for the ones we consumed."""
        if not self.calibrating:
            return None
        pending = self._pending or self.calibration
        shifted = bool(event.state & 0x0001)
        key = event.keysym

        moves = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}
        if key in moves:
            dx, dy = moves[key]
            # Shift resizes the outline instead of moving it; the sign is the
            # same, so the key that grows the box is the one pointing outward.
            pending = pending.resized(dx, dy) if shifted else pending.moved(dx, dy)
        elif key in ("plus", "equal", "KP_Add"):
            pending = pending.with_opacity(pending.opacity + 0.05)
        elif key in ("minus", "KP_Subtract"):
            pending = pending.with_opacity(pending.opacity - 0.05)
        elif key in ("Return", "KP_Enter"):
            self._finish_calibration(save=True)
            return "break"
        elif key == "Escape":
            self._finish_calibration(save=False)
            return "break"
        else:
            return None

        self._pending = pending
        self.calibration = pending
        self._place()
        self.redraw()
        self._claim_keyboard()
        return "break"

    def _finish_calibration(self, save: bool) -> None:
        self.root.unbind_all("<Key>")
        self.window.unbind("<Button-1>")
        self.calibrating = False
        if save and self._pending is not None:
            # The resolution is stamped in at save time rather than at edit
            # time, so a calibration only ever claims the screen it was
            # actually confirmed on.
            self.calibration = self._pending.with_screen(self.screen)
            path = save_to_config(self.cfg, self.calibration)
            self.strip.report(f"calibration saved to {path.name}")
        else:
            self.calibration = Calibration.from_config(self.cfg, self.profile)
            self.strip.report("calibration cancelled")
        self._pending = None
        self._place()
        set_click_through(self.window, True)
        self.redraw()

    def _draw_calibration(self) -> None:
        """The outline the user is dragging: every cell, no prices.

        Drawing every cell rather than only the border is the point -- lining up
        the outer edge is easy and still leaves the interior half a pixel out
        per column on a fractional cell size. Aligning the *inner* lines with
        the stash's own is what makes the calibration exact.
        """
        cols, rows = self.calibration.grid
        left, top, right, bottom = self.calibration.grid_rect()
        width, height = right - left, bottom - top
        # The alignment lines take the exalt-ring colour, which is the one hue
        # in the overlay that is deliberately not in any value band -- so they
        # stay findable over whatever the game is drawing underneath, and they
        # follow the accent when the user moves it.
        grid_line = self.appearance.ring_worth
        self.canvas.create_rectangle(0, 0, width - 1, height - 1, fill="#101317", outline="")
        for column in range(cols + 1):
            x = round(column * self.calibration.cell_px)
            self.canvas.create_line(x, 0, x, height, fill=grid_line)
        for row in range(rows + 1):
            y = round(row * self.calibration.cell_px)
            self.canvas.create_line(0, y, width, y, fill=grid_line)

        cell = f"{self.calibration.cell_px:.2f}"
        lines = [
            (
                f"origin {self.calibration.origin}   cell {cell}px   "
                f"grid {cols}x{rows}   alpha {self.calibration.opacity:.2f}"
            ),
            (
                "arrows move  -  shift+arrows resize  -  +/- opacity  -  "
                "Enter saves  -  Esc cancels"
            ),
            self.profile.calibration_hint,
        ]
        for index, text in enumerate(lines):
            self.canvas.create_text(
                6, 8 + index * 16, text=text, anchor="w", fill="#f4f4f5",
                font=("Segoe UI", 9, "bold" if index == 0 else "normal"),
            )

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> int:
        self.reload()
        self.root.mainloop()
        return 0


class _ControlStrip:
    """A small, deliberately *not* click-through window: the way in.

    The overlay itself cannot be clicked or focused by design, so every control
    lives here. It is the honest solution to a real constraint: a system-wide
    hotkey covers show/hide while the game has focus, and everything rarer --
    reload, recalibrate, quit -- is a button on a strip you can always find,
    rather than a second and third chord stolen from the OS.
    """

    def __init__(self, overlay: OverlayWindow, tk):
        self.overlay = overlay
        appearance = overlay.appearance
        self.window = tk.Toplevel(overlay.root)
        self.window.title(f"Originator overlay — {overlay.profile.label}")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=STRIP_BG, padx=6, pady=4)

        row = tk.Frame(self.window, bg=STRIP_BG)
        row.pack(fill="x")
        # With both profiles running there are two identical strips on screen,
        # and the only way to know which "calibrate" you are about to press is
        # for each to say what it is drawing over. In the accent colour, so the
        # strip and the rings it belongs to are visibly the same overlay.
        tk.Label(
            row, text=overlay.profile.label, bg=STRIP_BG, fg=appearance.ring_worth,
            font=(appearance.font_family, 8, "bold"),
        ).pack(side="left", padx=(2, 8))
        for label, command in (
            ("show/hide", overlay.toggle),
            # Between reload (re-read the file) and calibrate (move the grid),
            # because that is the order of how much it does.
            ("analyze", overlay.analyze),
            ("reload", overlay.reload),
            ("calibrate", overlay.start_calibration),
            ("quit", overlay.quit),
        ):
            tk.Button(
                row, text=label, command=command, relief="flat", bd=0,
                bg=STRIP_BUTTON_BG, fg=STRIP_FG,
                activebackground=appearance.ring_worth,
                font=(appearance.font_family, 8), padx=6, pady=1,
            ).pack(side="left", padx=2)

        self.status = tk.Label(
            self.window, text="", bg=STRIP_BG, fg=STRIP_STATUS_FG,
            font=(appearance.font_family, 8), anchor="w", justify="left",
            # Wrapped just inside the strip, whatever width it has been given:
            # a fixed 380 in a 700px strip wastes half of it, and in a 340px
            # one the text runs past the edge it was meant to stay inside.
            wraplength=max(200, appearance.strip_width - 120),
        )
        self.status.pack(fill="x", pady=(3, 0))

        # Keys work whenever the strip has focus, which is what makes the
        # overlay usable at all when the global hotkey is taken by something
        # else. They stand down during calibration: the strip is deliberately
        # holding the keyboard then, and a stray "q" over a half-aligned grid
        # would close the window rather than type into it.
        def _unless_calibrating(action):
            def handler(_event):
                if overlay.calibrating:
                    return None
                action()
                return "break"

            return handler

        for sequence, action in (
            ("<o>", overlay.toggle),
            ("<a>", overlay.analyze),
            ("<r>", overlay.reload),
            ("<c>", overlay.start_calibration),
            ("<q>", overlay.quit),
        ):
            self.window.bind(sequence, _unless_calibrating(action))

        # Draggable, because it is placed above the grid by default and that is
        # occasionally exactly where the game puts something you need to see.
        self._drag = (0, 0)
        for widget in (self.window, self.status):
            widget.bind("<Button-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag_to)

        self._place()

    def _place(self) -> None:
        appearance = self.overlay.appearance
        left, top, _right, bottom = self.overlay.calibration.grid_rect()
        above = top - (STRIP_HEIGHT + STRIP_GAP)
        below = bottom + STRIP_GAP
        # The user's side if it fits, the other one if it does not. "Above" is
        # the default and still falls back, because a grid calibrated against
        # the top of the screen has no room over it -- and a strip placed at a
        # negative y is a strip nobody can click, which is the one control
        # surface the overlay has.
        if appearance.strip_anchor == "below":
            y = below
        else:
            y = above if above >= 0 else below
        self.window.geometry(
            geometry_string(appearance.strip_width, STRIP_HEIGHT, left, y)
        )

    def report(self, message: str) -> None:
        if message:
            self.status.configure(text=message)

    def _grab(self, event) -> None:
        self._drag = (event.x_root, event.y_root)

    def _drag_to(self, event) -> None:
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        self._drag = (event.x_root, event.y_root)
        x = self.window.winfo_x() + dx
        y = self.window.winfo_y() + dy
        self.window.geometry(geometry_string(
            self.window.winfo_width(), self.window.winfo_height(), x, y
        ))


def run_overlay(
    cfg: Config | None = None,
    *,
    calibrate: bool = False,
    state_path: Path | str | None = None,
    profile: str | OverlayProfile = DEFAULT_PROFILE,
    appearance: OverlayAppearance | None = None,
) -> int:
    """Open one overlay and block until it is closed. The CLI's entry point.

    `profile` selects which surface this process draws over -- the stash tab or
    the inventory -- which in turn selects the config block it calibrates into
    and the state file it watches. Run it twice, once per profile, to have both.

    `appearance` defaults to whatever `appearance.overlay:` holds, which is one
    block shared by both profiles: the two surfaces sit in different places and
    are different shapes, but there is no reason for the inventory's prices to
    be a different colour from the stash's.
    """
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:  # pragma: no cover - tkinter ships with CPython
        raise OverlayUnavailable(
            "tkinter is not available in this Python build, so the overlay cannot run"
        ) from exc

    cfg = cfg or load_config()
    spec = profile_for(profile)
    calibration = Calibration.from_config(cfg, spec)
    overlay = OverlayWindow(cfg, calibration, state_path, appearance)
    if calibrate or not calibration.calibrated:
        overlay.start_calibration()
    return overlay.run()
