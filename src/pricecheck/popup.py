"""The popup itself: one tkinter window, reused, that draws a PriceCard.

Everything decided already happened in `src/pricecheck/render.py`. What is left
here is window mechanics, and three of them are worth explaining because they
are the difference between a popup that helps and one that gets in the way.

**It is not click-through.** The overlay's tiles are, because they sit directly
over stash cells the user still has to click. This sits beside the pointer, over
whatever the game happens to be drawing there, and being clickable is how it is
dismissed -- so it takes `WS_EX_LAYERED` for its transparency and never
`WS_EX_TRANSPARENT`.

**It never takes focus.** `-topmost` plus `overrideredirect` plus the
tool-window ex-style means it appears above the game without the game losing
input. A popup that stole focus would, in a fullscreen-windowed client, minimise
or black-frame the game every time the user checked a price, and that is far
worse than no popup.

**It is reused, not recreated.** One `Toplevel` is built at startup and then
withdrawn and redrawn. Creating a window per check costs 50-100 ms of flicker
on Windows, which is most of the time budget for something that is supposed to
feel instant.

The honest limitation is the overlay's, in the same words: nothing drawn by a
layered window appears over a game in **fullscreen-exclusive** mode. The app
checks for it at startup and says so rather than leaving the user with a hotkey
that appears to do nothing.
"""
from __future__ import annotations

from src.overlay.appearance import DEFAULT_APPEARANCE, OverlayAppearance
from src.overlay.window import (
    STRIP_BG,
    STRIP_BUTTON_BG,
    STRIP_FG,
    STRIP_STATUS_FG,
    set_no_activate,
)
from src.pricecheck.render import PriceCard, popup_position, unknown_reward_warning

#: Fixed width, derived height. A price card is mostly a number and a row of
#: rolls, so the width is the one the rolls row needs and the height follows
#: from how much there is to say -- a popup that changed width per item would
#: move the price under the pointer.
POPUP_WIDTH = 304

#: How long a card stays up before hiding itself. A price is a glance, so the
#: default is short; a card reporting a problem needs reading and an action, so
#: it is given noticeably longer.
HIDE_AFTER_MS = 6000
PROBLEM_HIDE_AFTER_MS = 12000

#: The confidence meter, same proportions as the overlay's tile meter.
METER_HEIGHT = 4

#: Padding, in pixels. Tight: this is a panel over a game, and whitespace here
#: is game the user cannot see.
PAD_X = 10
PAD_Y = 8


class PricePopup:
    """A reusable, non-focusing panel showing one PriceCard.

    `tk` is passed in rather than imported, matching `_ControlStrip`: it keeps
    this module importable -- and therefore testable for everything but the
    drawing -- on a machine with no display.
    """

    def __init__(self, tk, root, appearance: OverlayAppearance | None = None):
        self._tk = tk
        self.appearance = appearance or DEFAULT_APPEARANCE
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=STRIP_BG)
        self._hide_timer: str | None = None

        family = self.appearance.font_family
        scale = self.appearance.font_scale

        def size(points: int) -> int:
            return max(7, int(round(points * scale)))

        outer = tk.Frame(self.window, bg=STRIP_BG, padx=PAD_X, pady=PAD_Y)
        outer.pack(fill="both", expand=True)

        # -- the price, and the verdict beside it -------------------------
        head = tk.Frame(outer, bg=STRIP_BG)
        head.pack(fill="x")
        self._headline = tk.Label(
            head, text="", bg=STRIP_BG, fg=STRIP_FG,
            font=(family, size(20), "bold"), anchor="w",
        )
        self._headline.pack(side="left")
        self._verdict = tk.Label(
            head, text="", bg=STRIP_BG, fg=STRIP_FG,
            font=(family, size(9), "bold"), padx=6, pady=1,
        )
        self._verdict.pack(side="right", pady=(4, 0))

        # -- range and the strategy-adjusted figure -----------------------
        self._subline = tk.Label(
            outer, text="", bg=STRIP_BG, fg=STRIP_STATUS_FG,
            font=(family, size(9)), anchor="w", justify="left",
            wraplength=POPUP_WIDTH - 2 * PAD_X,
        )
        self._subline.pack(fill="x")

        # -- confidence meter ---------------------------------------------
        # A Canvas rather than two Frames: the fill is a fraction of the track
        # and a Frame's width would have to be recomputed in pixels on every
        # redraw anyway, which is what a Canvas rectangle already does.
        self._meter = tk.Canvas(
            outer, height=METER_HEIGHT, bg=self.appearance.meter_track,
            highlightthickness=0, bd=0,
        )
        self._meter.pack(fill="x", pady=(PAD_Y, 2))
        self._meter_fill = self._meter.create_rectangle(
            0, 0, 0, METER_HEIGHT, width=0, fill=STRIP_STATUS_FG
        )
        self._confidence = tk.Label(
            outer, text="", bg=STRIP_BG, fg=STRIP_STATUS_FG,
            font=(family, size(8)), anchor="w",
        )
        self._confidence.pack(fill="x")

        # -- the rolls ----------------------------------------------------
        self._rolls = tk.Label(
            outer, text="", bg=STRIP_BUTTON_BG, fg=STRIP_FG,
            font=(family, size(8)), anchor="w", justify="left", padx=6, pady=4,
            wraplength=POPUP_WIDTH - 2 * PAD_X - 12,
        )
        self._rolls.pack(fill="x", pady=(PAD_Y, 0))

        # -- note, reasons, and how it was priced -------------------------
        self._note = tk.Label(
            outer, text="", bg=STRIP_BG, fg=STRIP_STATUS_FG,
            font=(family, size(8)), anchor="w", justify="left",
            wraplength=POPUP_WIDTH - 2 * PAD_X,
        )
        self._note.pack(fill="x", pady=(6, 0))

        # Clicking anywhere dismisses. Bound on every child as well as the
        # window, because a click lands on whichever label is under it and a
        # binding on the Toplevel alone would only catch the padding.
        self._bind_dismiss(self.window)

    # -- showing --------------------------------------------------------
    def show(self, card: PriceCard) -> None:
        """Draw `card` beside the pointer and start its hide timer."""
        self._draw(card)

        # Lay out before measuring: the height depends on how many lines of
        # note and reasons this card has, and `winfo_reqheight` is only
        # meaningful once Tk has processed the geometry it was just given.
        self.window.update_idletasks()
        height = max(self.window.winfo_reqheight(), 1)

        x, y = popup_position(
            cursor=(self.window.winfo_pointerx(), self.window.winfo_pointery()),
            size=(POPUP_WIDTH, height),
            screen=(self.window.winfo_screenwidth(), self.window.winfo_screenheight()),
        )
        self.window.geometry(f"{POPUP_WIDTH}x{height}+{x}+{y}")
        self.window.deiconify()
        self.window.attributes("-topmost", True)
        # Raised without `focus_force`, deliberately -- see the module
        # docstring. `lift` reorders the window; it does not move input.
        self.window.lift()
        # And re-asserted every time, because the ex-style has to be written
        # while the window is mapped: a withdrawn window has no HWND for Tk to
        # have mapped yet, so setting this once in __init__ would be discarded.
        set_no_activate(self.window)
        self._restart_hide_timer(
            PROBLEM_HIDE_AFTER_MS if card.problem else HIDE_AFTER_MS
        )

    def hide(self) -> None:
        self._cancel_hide_timer()
        self.window.withdraw()

    # -- drawing --------------------------------------------------------
    def _draw(self, card: PriceCard) -> None:
        if card.problem:
            self._headline.configure(text="No price", fg=STRIP_STATUS_FG)
            self._verdict.pack_forget()
            self._subline.configure(text=card.problem)
            self._meter.pack_forget()
            self._confidence.pack_forget()
        else:
            self._headline.configure(text=card.headline, fg=STRIP_FG)
            self._verdict.configure(text=f" {card.verdict} ", bg=card.verdict_colour)
            self._verdict.pack(side="right", pady=(4, 0))
            self._subline.configure(text=self._subline_text(card))
            self._meter.pack(fill="x", pady=(PAD_Y, 2))
            self._confidence.pack(fill="x")
            self._draw_meter(card)

        self._rolls.configure(text=self._rolls_text(card))
        self._note.configure(text=self._note_text(card))

    def _draw_meter(self, card: PriceCard) -> None:
        self._meter.configure(bg=self.appearance.meter_track)
        width = max(self.window.winfo_width(), POPUP_WIDTH) - 2 * PAD_X
        fraction = card.confidence_fraction or 0.0
        self._meter.coords(self._meter_fill, 0, 0, int(width * fraction), METER_HEIGHT)
        self._meter.itemconfigure(
            self._meter_fill, fill=card.confidence_colour or STRIP_STATUS_FG
        )
        self._confidence.configure(
            text=f"{card.confidence} confidence" if card.confidence else ""
        )

    def _subline_text(self, card: PriceCard) -> str:
        parts: list[str] = []
        if card.range_text:
            parts.append(card.range_text)
        if card.fair_value_text:
            # Named, because an unlabelled second number next to a price is
            # read as the price being wrong rather than as a different question.
            parts.append(f"fair {card.fair_value_text}")
        if card.strategy and card.strategy != "market":
            parts.append(card.strategy.replace("_", " "))
        return "   ".join(parts)

    def _rolls_text(self, card: PriceCard) -> str:
        if not card.rolls:
            return ""
        text = "   ".join(f"{label} {value}" for label, value in card.rolls)
        return f"{text}\nCorrupted" if card.corrupted else text

    def _note_text(self, card: PriceCard) -> str:
        lines: list[str] = []
        # First, and on every card including a failed one: a reward the parser
        # could not place means the price below it was computed from an
        # incomplete item, and that outranks anything else here.
        warning = unknown_reward_warning(card.unknown_rewards)
        if warning:
            lines.append(warning)
        # The note is the sentence meant for a trade whisper, so it only
        # belongs on a card that actually has a price to quote.
        if card.note and not card.problem:
            lines.append(card.note)
        lines.extend(f"- {reason}" for reason in card.reasons[:3])
        if card.source == "market":
            lines.append("priced from live listings")
        return "\n".join(lines)

    # -- plumbing -------------------------------------------------------
    def _bind_dismiss(self, widget) -> None:
        widget.bind("<Button-1>", lambda _e: self.hide())
        widget.bind("<Escape>", lambda _e: self.hide())
        for child in widget.winfo_children():
            self._bind_dismiss(child)

    def _restart_hide_timer(self, after_ms: int) -> None:
        self._cancel_hide_timer()
        self._hide_timer = self.window.after(after_ms, self.hide)

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer is not None:
            try:
                self.window.after_cancel(self._hide_timer)
            except Exception:  # pragma: no cover - a timer that already fired
                pass
            self._hide_timer = None
