"""The wiring: settings, and the worker that keeps HTTP off the Tk thread.

No Tk here. `PriceCheckApp` and the strip need a display, but the two things
that are actually hard -- a bad config value and a reply that arrives after a
newer one -- do not, and those are what this pins.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.pricecheck.app import CONFIG_KEY, PriceCheckSettings, PriceWorker
from src.pricecheck.client import ServiceError


class _Cfg:
    """The smallest thing with a `get` that reads one dotted path."""

    def __init__(self, block=None):
        self.block = block

    def get(self, path, default=None):
        return self.block if path == CONFIG_KEY else default


# -- settings ---------------------------------------------------------------
def test_an_absent_block_gives_the_shipped_defaults():
    settings = PriceCheckSettings.from_config(_Cfg(None))
    assert settings.hotkey == "ctrl+alt+d"
    assert settings.watch_clipboard is True
    assert settings.strategy == "market"
    assert settings.port == 8777


def test_the_default_chord_stays_clear_of_the_games_own_copy_key():
    """Ctrl+C is the one binding this feature must not touch, and a chord
    sitting next to it would be mispressed constantly."""
    assert "c" not in PriceCheckSettings().hotkey.rsplit("+", 1)[-1]


def test_a_configured_block_is_read():
    settings = PriceCheckSettings.from_config(
        _Cfg({
            "hotkey": "ctrl+alt+p",
            "watch_clipboard": False,
            "strategy": "patient",
            "port": 9001,
        })
    )
    assert settings == PriceCheckSettings("ctrl+alt+p", False, "patient", 9001)


@pytest.mark.parametrize("bad", ["nonsense", "", None, 7])
def test_an_unknown_strategy_falls_back_rather_than_raising(bad):
    """This runs in a window over a game with no console, so a typo in
    config.yaml has to degrade to something usable."""
    assert PriceCheckSettings.from_config(_Cfg({"strategy": bad})).strategy == "market"


@pytest.mark.parametrize("bad", [0, 80, 443, 1023, 65536, 99999, -1, "abc", None, 1.5e9])
def test_an_unusable_port_falls_back(bad):
    """0 means "any port", which a client could never find again, and
    1-1023 are privileged."""
    assert PriceCheckSettings.from_config(_Cfg({"port": bad})).port == 8777


@pytest.mark.parametrize("port", [1024, 8777, 9000, 65535])
def test_a_usable_port_is_kept(port):
    assert PriceCheckSettings.from_config(_Cfg({"port": port})).port == port


def test_a_string_port_that_is_a_number_is_accepted():
    """YAML quoting is a hand-edit away, and refusing "9000" would be pedantry."""
    assert PriceCheckSettings.from_config(_Cfg({"port": "9000"})).port == 9000


def test_the_shipped_config_defaults_match_the_dataclass():
    """Two places declare these, so they have to be checked against each other.

    `src/config.py` holds them for the file on disk and `PriceCheckSettings`
    holds them for a missing block. A disagreement would mean the app behaved
    differently depending on whether config.yaml happened to mention the
    feature.
    """
    from src.config import _DEFAULTS

    shipped = _DEFAULTS[CONFIG_KEY]
    default = PriceCheckSettings()
    assert shipped["hotkey"] == default.hotkey
    assert shipped["watch_clipboard"] == default.watch_clipboard
    assert shipped["strategy"] == default.strategy
    assert shipped["port"] == default.port


# -- the worker -------------------------------------------------------------
class _FakeClient:
    """A client that answers on command, so the ordering can be driven."""

    def __init__(self, delay: float = 0.0, fail: Exception | None = None):
        self.delay = delay
        self.fail = fail
        self.calls: list[tuple[str, str]] = []
        self.warm = True

    def price_text(self, text, strategy="market"):
        self.calls.append((text, strategy))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return {
            "supported": True,
            "chaos": {"suggested": 412.0},
            "display": {"formatted": f"{len(text)}c", "range": ""},
            "confidence": {"level": "High", "score": 0.9, "reasons": []},
            "item": {"quantity": 1},
        }


def _drain(worker: PriceWorker, expected: int, timeout: float = 5.0):
    """Collect `expected` results, or fail rather than hang the suite."""
    out = []
    deadline = time.monotonic() + timeout
    while len(out) < expected and time.monotonic() < deadline:
        result = worker.poll()
        if result is None:
            time.sleep(0.01)
            continue
        out.append(result)
    assert len(out) == expected, f"got {len(out)} of {expected} results"
    return out


def test_a_check_is_priced_and_comes_back_through_the_queue():
    worker = PriceWorker(_FakeClient(), "patient")
    worker.submit("Rarity: Rare")
    (result,) = _drain(worker, 1)
    assert result.card.priced
    assert worker.client.calls == [("Rarity: Rare", "patient")]


def test_the_strategy_from_settings_is_the_one_used():
    worker = PriceWorker(_FakeClient(), "quick_sell")
    worker.submit("x")
    _drain(worker, 1)
    assert worker.client.calls[0][1] == "quick_sell"


def test_serials_increase_so_a_late_reply_can_be_recognised():
    worker = PriceWorker(_FakeClient())
    first = worker.submit("a")
    second = worker.submit("b")
    assert second > first


def test_a_check_during_a_slow_one_replaces_it_rather_than_queueing():
    """Three fast presses while a cold request is in flight must not produce
    three popups. The user only ever wants the most recent price.
    """
    client = _FakeClient(delay=0.25)
    worker = PriceWorker(client)
    worker.submit("first")
    time.sleep(0.05)  # let the worker pick "first" up
    worker.submit("second")
    worker.submit("third")
    worker.submit("fourth")

    results = _drain(worker, 2)
    # "first" was already in flight and finishes; the rest collapse to the
    # last one submitted, so two replies rather than four.
    assert [text for text, _ in client.calls] == ["first", "fourth"]
    assert results[-1].serial == worker.serial


def test_the_newest_serial_is_the_one_a_caller_should_draw():
    worker = PriceWorker(_FakeClient())
    worker.submit("a")
    (result,) = _drain(worker, 1)
    assert result.serial == worker.serial


def test_a_service_error_becomes_a_card_rather_than_killing_the_thread():
    """A hotkey that silently does nothing is the failure that gets a tool
    uninstalled instead of fixed."""
    worker = PriceWorker(_FakeClient(fail=ServiceError("nothing is listening")))
    worker.submit("x")
    (result,) = _drain(worker, 1)
    assert not result.card.priced
    assert "nothing is listening" in result.card.problem


def test_an_unexpected_exception_also_becomes_a_card():
    worker = PriceWorker(_FakeClient(fail=RuntimeError("boom")))
    worker.submit("x")
    (result,) = _drain(worker, 1)
    assert not result.card.priced
    assert "boom" in result.card.problem


def test_the_worker_survives_the_error_and_prices_the_next_check():
    class _Flaky(_FakeClient):
        def price_text(self, text, strategy="market"):
            if text == "bad":
                raise ServiceError("transient")
            return super().price_text(text, strategy)

    worker = PriceWorker(_Flaky())
    worker.submit("bad")
    _drain(worker, 1)
    worker.submit("Rarity: Rare")
    (result,) = _drain(worker, 1)
    assert result.card.priced, "the worker did not recover from the first failure"


def test_polling_an_idle_worker_returns_none():
    """The Tk timer calls this several times a second forever."""
    worker = PriceWorker(_FakeClient())
    for _ in range(20):
        assert worker.poll() is None


def test_only_one_thread_runs_at_a_time():
    client = _FakeClient(delay=0.1)
    worker = PriceWorker(client)
    before = threading.active_count()
    for index in range(8):
        worker.submit(f"item-{index}")
    assert threading.active_count() <= before + 1
    _drain(worker, 2)


def test_the_worker_thread_is_a_daemon():
    """Otherwise closing the strip leaves the process alive on a pending
    request, which on Windows means an invisible process holding the port."""
    worker = PriceWorker(_FakeClient(delay=0.2))
    worker.submit("x")
    assert worker._thread is not None
    assert worker._thread.daemon
    _drain(worker, 1)
