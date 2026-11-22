"""The price checker as a running program: hotkey, clipboard, popup, strip.

Three things arrive from outside and have to be combined without blocking each
other:

- a **system-wide hotkey**, which can only be received on its own thread
  (`src/overlay/hotkeys.py` explains why Tk swallows it otherwise),
- the **clipboard**, which changes whenever the user presses Ctrl+C in the game,
- the **pricing service**, whose first answer takes seconds because it loads a
  model, and whose later answers take milliseconds.

The arrangement is the one `src/overlay/analyze.py` already established and
which this reuses rather than reinvents: the HTTP request runs on a worker
thread and returns through a queue, and a single Tk `after` loop drains the
queue, polls the hotkey listener and polls the clipboard. Nothing but the main
thread ever touches a widget. Tk is not thread-safe and a popup drawn from a
worker thread is a crash that happens on someone else's machine a week later.

The strip is the same small always-on-top window the overlay uses, for the same
reason: the popup comes and goes, so a process with no console and no tray
needs one place that says what league and model it is attached to and offers a
way to stop it.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.config import Config, load_config
from src.overlay.appearance import OverlayAppearance
from src.overlay.hotkeys import HotkeyListener, HotkeyError, parse_hotkey
from src.overlay.window import (
    STRIP_BG,
    STRIP_BUTTON_BG,
    STRIP_FG,
    STRIP_HEIGHT,
    STRIP_STATUS_FG,
    FULLSCREEN_WARNING,
    fullscreen_exclusive_active,
    set_no_activate,
)
from src.pricecheck import clipboard
from src.pricecheck.client import PriceCheckClient, ServiceError, client_from_config
from src.pricecheck.render import (
    PriceCard,
    card_from_response,
    error_card,
    status_caption,
)
from src.pricing.sell_strategy import STRATEGIES

CONFIG_KEY = "pricecheck"

#: How often the clipboard counter and the hotkey queue are checked. 120 ms is
#: a tenth of the time it takes to notice a window appear, and the clipboard
#: check at that rate is one syscall that takes no lock -- see
#: src/pricecheck/clipboard.py.
POLL_MS = 120

#: The name the hotkey listener reports. One chord, so the mapping is trivial,
#: but it goes through the same named-bindings interface the overlay uses so
#: that adding a second chord later is a dictionary entry.
CHECK = "check"

#: How long to wait for a service we just started to answer /health. A cold
#: start imports pandas and the model backend before uvicorn binds, which on a
#: spinning disk is genuinely several seconds.
SPAWN_TIMEOUT_SECONDS = 25.0
SPAWN_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class PriceCheckSettings:
    """The `pricecheck:` config block, validated.

    Clamped and defaulted on the way in for the same reason
    `OverlayAppearance` is: this runs in a window over a game with no console
    attached, so a typo in config.yaml has to degrade to something usable
    rather than raise where nobody can read it.
    """

    hotkey: str = "ctrl+alt+d"
    watch_clipboard: bool = True
    strategy: str = "market"
    port: int = 8777

    @classmethod
    def from_config(cls, cfg: Config) -> "PriceCheckSettings":
        block = cfg.get(CONFIG_KEY) or {}
        default = cls()
        strategy = str(block.get("strategy") or default.strategy)
        port = block.get("port", default.port)
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = default.port
        return cls(
            hotkey=str(block.get("hotkey") or default.hotkey),
            watch_clipboard=bool(block.get("watch_clipboard", default.watch_clipboard)),
            strategy=strategy if strategy in STRATEGIES else default.strategy,
            # 1-1023 are privileged and 0 is "any port", neither of which a
            # client could then find again.
            port=port if 1024 <= port <= 65535 else default.port,
        )


# ---------------------------------------------------------------------------
# Pricing off the Tk thread
# ---------------------------------------------------------------------------
@dataclass
class PriceResult:
    """One finished check. `card` is always drawable -- see render.error_card."""

    card: PriceCard
    #: Monotonic counter, so a reply that arrives after a newer check was
    #: started can be discarded instead of drawn over the newer price.
    serial: int = 0


class PriceWorker:
    """Prices clipboard text over HTTP, one request at a time, off-thread.

    Single-slot rather than a pool. Two checks in flight would mean two popups
    racing to be the one on screen, and the user only ever wants the most
    recent -- so a check that arrives while one is running replaces the pending
    text instead of queueing behind it.
    """

    def __init__(self, client: PriceCheckClient, strategy: str = "market"):
        self.client = client
        self.strategy = strategy
        self._results: queue.Queue[PriceResult] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._serial = 0
        self._pending: tuple[int, str] | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, text: str) -> int:
        """Queue `text` to be priced. Returns the serial of this check."""
        with self._lock:
            self._serial += 1
            serial = self._serial
            self._pending = (serial, text)
        if not self.busy:
            self._thread = threading.Thread(
                target=self._run, name="pricecheck-worker", daemon=True
            )
            self._thread.start()
        return serial

    @property
    def serial(self) -> int:
        with self._lock:
            return self._serial

    def poll(self) -> PriceResult | None:
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        while True:
            with self._lock:
                job = self._pending
                self._pending = None
            if job is None:
                return
            serial, text = job
            self._results.put(PriceResult(self._price(text), serial))

    def _price(self, text: str) -> PriceCard:
        try:
            return card_from_response(self.client.price_text(text, self.strategy))
        except ServiceError as exc:
            return error_card(str(exc))
        except Exception as exc:  # pragma: no cover - defence around a popup
            # A check that fails must still produce a popup. The alternative is
            # a hotkey that silently does nothing, in a process with no console
            # to print the traceback to.
            return error_card(f"price check failed: {exc}")


# ---------------------------------------------------------------------------
# Starting a service if there is not one
# ---------------------------------------------------------------------------
def spawn_service(port: int, cfg: Config | None = None) -> subprocess.Popen | None:
    """Start `python -m src.service` and wait until it answers.

    Started as a child process rather than on a thread in here, which is the
    whole point of the boundary: the model and the database live in one process
    and the window lives in another, so a Tk callback cannot stall on a model
    load and the service outlives nothing it should not.

    The token is read *after* `/health` answers, never before. The service
    writes the token file and then binds the port, so health answering is the
    signal that the file on disk is this service's and not a previous run's --
    getting that order wrong would authenticate against a stale token and
    present the user with a 401 they cannot explain.
    """
    creation_flags = 0
    if sys.platform == "win32":
        # No console window for the child. Without this a double-clicked
        # launcher pops a second black window that the user cannot close
        # without killing the service.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        [sys.executable, "-m", "src.service", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )

    probe = PriceCheckClient(token="", port=port)
    deadline = time.monotonic() + SPAWN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None  # it exited; the caller reports that it could not start
        if probe.health():
            return process
        time.sleep(SPAWN_POLL_SECONDS)
    return None


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
class PriceCheckApp:
    """Hotkey, clipboard watcher, popup and strip, on one Tk event loop."""

    def __init__(
        self,
        cfg: Config,
        settings: PriceCheckSettings,
        client: PriceCheckClient,
        tk,
        appearance: OverlayAppearance | None = None,
    ):
        from src.pricecheck.popup import PricePopup

        self.cfg = cfg
        self.settings = settings
        self.appearance = appearance or OverlayAppearance.from_config(cfg)
        self.worker = PriceWorker(client, settings.strategy)
        self._tk = tk

        self.root = tk.Tk()
        # Withdrawn, not merely hidden: the root exists only to own the other
        # two windows and run the event loop, and an empty 200x200 frame in the
        # taskbar is a window the user would reasonably try to close.
        self.root.withdraw()

        self.popup = PricePopup(tk, self.root, self.appearance)
        self.strip = _CheckerStrip(self, tk)

        self.watcher = (
            clipboard.ClipboardWatcher() if settings.watch_clipboard else None
        )
        self.hotkeys = HotkeyListener({CHECK: settings.hotkey})
        self._last_serial_drawn = 0
        self._report_startup()
        self.hotkeys.start()
        self.root.after(POLL_MS, self._poll)

    # -- the one timer --------------------------------------------------
    def _poll(self) -> None:
        for name in self.hotkeys.poll():
            if name == CHECK:
                self.check_now()

        if self.watcher is not None:
            text = self.watcher.poll()
            if text:
                self._submit(text)

        result = self.worker.poll()
        if result is not None:
            # Only the newest check may draw. An older reply arriving late --
            # the cold-start request, typically, which can take seconds --
            # would otherwise paint a stale price over a fresh one.
            if result.serial >= self._last_serial_drawn:
                self._last_serial_drawn = result.serial
                self.popup.show(result.card)
                self.strip.report(
                    result.card.problem or self.strip.caption, transient=True
                )

        self.root.after(POLL_MS, self._poll)

    # -- actions --------------------------------------------------------
    def check_now(self) -> None:
        """Price whatever is on the clipboard right now.

        The hotkey path. It reads the clipboard directly instead of waiting for
        the watcher to notice a change, because the common use is to re-check
        an item already copied -- and for the hotkey to be the thing that works
        when watching is switched off.
        """
        text = clipboard.read_item_text()
        if not text:
            self.popup.show(
                error_card(
                    "Nothing copied from the game. Hover an item in Path of Exile "
                    "and press Ctrl+C, then press this hotkey again.",
                    actionable=False,
                )
            )
            return
        if self.watcher is not None:
            # So the watcher does not immediately show the same item again.
            self.watcher.forget()
        self._submit(text)

    def _submit(self, text: str) -> None:
        self.worker.submit(text)
        self.strip.report("checking...", transient=True)

    def quit(self) -> None:
        self.hotkeys.stop()
        try:
            self.root.destroy()
        except Exception:  # pragma: no cover - a root already torn down
            pass

    # -- startup reporting ----------------------------------------------
    def _report_startup(self) -> None:
        """Say, once, everything that would otherwise fail silently later.

        Four separate things can be wrong before a single key is pressed, and
        every one of them presents identically to the user -- a hotkey that
        appears to do nothing. So each gets its own sentence on the strip, in
        the order of how badly it breaks the feature.
        """
        problems: list[str] = []

        if fullscreen_exclusive_active():
            # First, because it makes everything else invisible: the popup
            # would be drawn correctly and never appear.
            problems.append(FULLSCREEN_WARNING)

        ok, why = clipboard.available()
        if not ok:
            problems.append(why)

        try:
            parse_hotkey(self.settings.hotkey)
        except HotkeyError as exc:
            problems.append(f"hotkey: {exc}")

        caption = status_caption(self._status(), "")
        self.strip.caption = caption
        self.strip.report(problems[0] if problems else caption)

    def _status(self) -> dict[str, Any] | None:
        try:
            return self.worker.client.status()
        except ServiceError:
            return None

    def run(self) -> int:
        self.root.mainloop()
        self.worker.client.warm = False
        return 0


class _CheckerStrip:
    """The checker's own control strip: status, a manual check, and quit.

    Modelled on the overlay's `_ControlStrip` and deliberately not shared with
    it. The two look the same and do different things -- that one drives a grid
    over the stash, this one reports a connection and fires a check -- and
    folding them into one widget with a mode flag would couple two windows that
    are only coincidentally the same size.
    """

    def __init__(self, app: "PriceCheckApp", tk):
        self.app = app
        self.caption = ""
        appearance = app.appearance
        self.window = tk.Toplevel(app.root)
        self.window.title("Originator price check")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=STRIP_BG, padx=6, pady=4)

        row = tk.Frame(self.window, bg=STRIP_BG)
        row.pack(fill="x")
        tk.Label(
            row, text=f"price check  {app.settings.hotkey}", bg=STRIP_BG,
            fg=appearance.ring_worth, font=(appearance.font_family, 8, "bold"),
        ).pack(side="left", padx=(2, 8))
        for label, command in (("check", app.check_now), ("quit", app.quit)):
            tk.Button(
                row, text=label, command=command, relief="flat", bd=0,
                bg=STRIP_BUTTON_BG, fg=STRIP_FG,
                activebackground=appearance.ring_worth,
                font=(appearance.font_family, 8), padx=6, pady=1,
            ).pack(side="left", padx=2)

        self.status = tk.Label(
            self.window, text="", bg=STRIP_BG, fg=STRIP_STATUS_FG,
            font=(appearance.font_family, 8), anchor="w", justify="left",
            wraplength=max(200, appearance.strip_width - 120),
        )
        self.status.pack(fill="x", pady=(3, 0))

        # Bottom-right by default, out of the way of both the stash panel and
        # the game's own HUD, and draggable because "out of the way" depends on
        # a screen this code cannot see.
        width = appearance.strip_width
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        self.window.geometry(
            f"{width}x{STRIP_HEIGHT}+{screen_w - width - 24}+{screen_h - STRIP_HEIGHT - 72}"
        )
        # The strip appears while the game is already running, so it must not
        # take focus either. Its buttons still work -- WS_EX_NOACTIVATE
        # suppresses activation, not input.
        set_no_activate(self.window)

        self._drag = (0, 0)
        for widget in (self.window, self.status):
            widget.bind("<Button-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag_to)

    def report(self, message: str, transient: bool = False) -> None:
        if not message:
            return
        self.status.configure(text=message)
        if not transient:
            self.caption = message

    def _grab(self, event) -> None:
        self._drag = (event.x_root, event.y_root)

    def _drag_to(self, event) -> None:
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        self._drag = (event.x_root, event.y_root)
        self.window.geometry(
            f"+{self.window.winfo_x() + dx}+{self.window.winfo_y() + dy}"
        )


class PriceCheckUnavailable(RuntimeError):
    """The checker cannot run here, and the message says why."""


def run_price_check(
    cfg: Config | None = None,
    *,
    port: int | None = None,
    strategy: str | None = None,
    watch: bool | None = None,
    hotkey: str | None = None,
    spawn: bool = True,
    token: str | None = None,
) -> int:
    """Open the price checker and block until it is closed. The CLI's entry."""
    try:
        import tkinter
    except ImportError as exc:  # pragma: no cover - tkinter ships with CPython
        raise PriceCheckUnavailable(
            "tkinter is not available in this Python build, so the price checker "
            "cannot draw its popup"
        ) from exc

    cfg = cfg or load_config()
    settings = PriceCheckSettings.from_config(cfg)
    settings = PriceCheckSettings(
        hotkey=hotkey or settings.hotkey,
        watch_clipboard=settings.watch_clipboard if watch is None else watch,
        strategy=strategy or settings.strategy,
        port=port or settings.port,
    )

    child: subprocess.Popen | None = None
    client = client_from_config(cfg, port=settings.port, token=token)
    if not client.health():
        if not spawn:
            raise PriceCheckUnavailable(
                f"nothing is listening on 127.0.0.1:{settings.port}. Start it with "
                "`python -m src.service`, or drop --no-spawn to have one started."
            )
        child = spawn_service(settings.port, cfg)
        if child is None:
            raise PriceCheckUnavailable(
                "could not start the pricing service. Run `python -m src.service` "
                "in a terminal to see why it failed."
            )
        # Only now, and for the reason given in spawn_service.
        client = client_from_config(cfg, port=settings.port, token=token)

    app = PriceCheckApp(cfg, settings, client, tkinter)
    try:
        return app.run()
    finally:
        if child is not None:
            # A service this process started is this process's to stop. One it
            # merely found is left running -- it may be the app's.
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                child.kill()
