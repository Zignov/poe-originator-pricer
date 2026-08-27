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
from src.overlay.calibration import (
    Calibration,
    resolution_warning,
    save_to_config,
)
from src.overlay.hotkeys import HotkeyListener
from src.overlay.layout import (
    TEXT_COLOUR,
    TILE_OUTLINE,
    build_draw_list,
    font_size_for,
    geometry_string,
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
#: clipped "quit" is a button people cannot find.
STRIP_WIDTH = 500


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


# ---------------------------------------------------------------------------
class OverlayWindow:
    """The overlay proper, plus the little control strip that drives it."""

    def __init__(
        self,
        cfg: Config,
        calibration: Calibration,
        state_path: Path | str | None = None,
    ):
        import tkinter as tk

        self._tk = tk
        self.cfg = cfg
        self.calibration = calibration
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
        self.strip.report(resolution_warning(calibration, self.screen) or "")

        # Held so quitting can cancel them. A pending `after` fires into an
        # interpreter that no longer exists and Tcl reports it on stderr as an
        # "invalid command name", which looks like a crash on the way out.
        self._timers: dict[str, str] = {}
        self._timers["state"] = self.root.after(0, self._poll_state)
        self._timers["hotkeys"] = self.root.after(HOTKEY_POLL_MS, self._poll_hotkeys)

    # -- placement and drawing -------------------------------------------
    def _place(self) -> None:
        width, height, x, y = window_geometry(self.calibration)
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
        font = ("Segoe UI", font_size_for(self.calibration.cell_px), "bold")
        for draw in build_draw_list(self.state, self.calibration):
            box = draw.relative_to(origin)
            self.canvas.create_rectangle(
                box.left, box.top, box.right, box.bottom,
                fill=box.fill, outline=TILE_OUTLINE, width=1,
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
            self.canvas.create_text(
                (box.left + box.right) / 2,
                (box.top + box.bottom) / 2,
                text=box.text,
                fill=TEXT_COLOUR,
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
            self.state = state
            self.strip.report(self._analysis_note or state.summary())
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
        self.canvas.create_rectangle(0, 0, width - 1, height - 1, fill="#101317", outline="")
        for column in range(cols + 1):
            x = round(column * self.calibration.cell_px)
            self.canvas.create_line(x, 0, x, height, fill="#39d0d8")
        for row in range(rows + 1):
            y = round(row * self.calibration.cell_px)
            self.canvas.create_line(0, y, width, y, fill="#39d0d8")

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
        self.window = tk.Toplevel(overlay.root)
        self.window.title(f"Originator overlay — {overlay.profile.label}")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#15181d", padx=6, pady=4)

        row = tk.Frame(self.window, bg="#15181d")
        row.pack(fill="x")
        # With both profiles running there are two identical strips on screen,
        # and the only way to know which "calibrate" you are about to press is
        # for each to say what it is drawing over.
        tk.Label(
            row, text=overlay.profile.label, bg="#15181d", fg="#39d0d8",
            font=("Segoe UI", 8, "bold"),
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
                bg="#242a33", fg="#f4f4f5", activebackground="#39d0d8",
                font=("Segoe UI", 8), padx=6, pady=1,
            ).pack(side="left", padx=2)

        self.status = tk.Label(
            self.window, text="", bg="#15181d", fg="#a9b0ba",
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=380,
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
        left, top, _right, bottom = self.overlay.calibration.grid_rect()
        # Above the grid if there is room, below it otherwise, so the strip
        # never covers the first row of maps.
        y = top - 58 if top - 58 >= 0 else bottom + 6
        self.window.geometry(geometry_string(STRIP_WIDTH, 52, left, y))

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
) -> int:
    """Open one overlay and block until it is closed. The CLI's entry point.

    `profile` selects which surface this process draws over -- the stash tab or
    the inventory -- which in turn selects the config block it calibrates into
    and the state file it watches. Run it twice, once per profile, to have both.
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
    overlay = OverlayWindow(cfg, calibration, state_path)
    if calibrate or not calibration.calibrated:
        overlay.start_calibration()
    return overlay.run()
