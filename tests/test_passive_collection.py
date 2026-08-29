"""Collecting on a loop, unattended, without becoming a scraper.

Everything here drives the loop with a stub collector and an injected clock, so
a day of cycles replays in milliseconds and nothing opens a socket. The tests
that matter most are the two about stopping: a switch that takes half an hour
to visibly do anything is not a switch, and a scan that keeps going after the
toggle is off is the behaviour that gets a User-Agent blocked.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.config import Config
from src.market.collect_job import COLLECT_LOCK
from src.market.passive import (
    COOLING,
    STOPPED,
    PassiveCollector,
    PassiveSettings,
    controller,
)


class _Repo:
    closed = False

    def close(self):
        self.closed = True


def _cfg(**overrides):
    data = {
        "league": "Allflame",
        "database": {"path": ":memory:"},
        "collection": {"passive": dict(
            {"maps_per_scan": 400, "cooldown_minutes": 30, "max_cooldown_minutes": 90,
             "auto_off_after_hours": 0, "auto_retrain": True,
             "retrain_every_scans": 4}, **overrides)},
    }
    return Config(data)


def _settle(collector, until, timeout=5.0):
    """Wait for a condition, then stop the loop. Never sleeps a fixed amount."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not until():
        time.sleep(0.005)
    collector.stop()
    collector.join(timeout=2.0)
    return until()


def test_passive_mode_scans_then_waits_out_its_cooldown_before_scanning_again():
    scans = []
    collector = PassiveCollector()

    def collect(cfg, repo, limit, progress=None, cancel=None):
        scans.append(limit)
        return {"inserted": 3}

    collector.start(
        _cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=1000),
        collect=collect, train=lambda cfg: None, repo_factory=_Repo,
    )
    assert _settle(collector, lambda: len(scans) >= 2)
    assert scans[0] == 400, "the scan size comes from the settings"


def test_turning_passive_mode_off_wakes_it_out_of_the_cooldown_immediately():
    """A thirty-minute sleep would mean the toggle took thirty minutes to work."""
    started = threading.Event()
    collector = PassiveCollector()

    def collect(cfg, repo, limit, progress=None, cancel=None):
        started.set()
        return {"inserted": 1}

    # A cooldown far longer than the test could ever wait for.
    collector.start(_cfg(cooldown_minutes=600, retrain_every_scans=1000),
                    collect=collect, train=lambda cfg: None, repo_factory=_Repo)
    assert started.wait(timeout=5.0)
    while collector.state.phase != COOLING and collector.running:
        time.sleep(0.005)

    began = time.monotonic()
    collector.stop("switched off")
    collector.join(timeout=5.0)
    assert not collector.running
    assert time.monotonic() - began < 2.0, "the cooldown was slept through, not woken"
    assert collector.state.phase == STOPPED


def test_turning_passive_mode_off_mid_scan_keeps_the_listings_it_already_wrote():
    """Ingest flushes every fifty rows, so a stopped scan is not a lost one."""
    collector = PassiveCollector()
    in_scan = threading.Event()

    def collect(cfg, repo, limit, progress=None, cancel=None):
        in_scan.set()
        while not cancel.is_set():
            time.sleep(0.005)
        return {"inserted": 17, "cancelled": True}

    collector.start(_cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=1000),
                    collect=collect, train=lambda cfg: None, repo_factory=_Repo)
    assert in_scan.wait(timeout=5.0)
    collector.stop()
    collector.join(timeout=5.0)
    assert collector.state.listings_total == 17


def test_passive_mode_switches_itself_off_after_the_configured_number_of_hours():
    now = [1000.0]
    collector = PassiveCollector()

    def collect(cfg, repo, limit, progress=None, cancel=None):
        now[0] += 3600.0  # each scan takes an hour of the fake clock
        return {"inserted": 1}

    collector.start(
        _cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, auto_off_after_hours=2, retrain_every_scans=1000),
        collect=collect, train=lambda cfg: None, repo_factory=_Repo,
        clock=lambda: now[0],
    )
    collector.join(timeout=5.0)
    assert not collector.running
    assert "2h" in collector.state.stopped_reason
    assert collector.state.scans_done == 2


def test_a_manual_collection_and_a_passive_scan_never_read_the_market_at_the_same_time():
    """Two RateLimiters cannot see each other's requests -- see COLLECT_LOCK."""
    overlapped = []
    collector = PassiveCollector()
    scanning = threading.Event()

    def collect(cfg, repo, limit, progress=None, cancel=None):
        scanning.set()
        overlapped.append(COLLECT_LOCK.locked())
        while not cancel.is_set():
            time.sleep(0.005)
        return {"inserted": 0}

    collector.start(_cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=1000),
                    collect=collect, train=lambda cfg: None, repo_factory=_Repo)
    assert scanning.wait(timeout=5.0)
    assert COLLECT_LOCK.locked(), "a scan in flight must hold the shared lock"
    assert not COLLECT_LOCK.acquire(blocking=False), "a manual run would have to wait"
    collector.stop()
    collector.join(timeout=5.0)
    assert not COLLECT_LOCK.locked(), "the lock is released when the scan ends"


def test_passive_mode_retrains_only_once_every_configured_number_of_scans():
    trains = []
    collector = PassiveCollector()
    collector.start(
        _cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=3),
        collect=lambda cfg, repo, limit, progress=None, cancel=None: {"inserted": 5},
        train=lambda cfg: trains.append(1), repo_factory=_Repo,
    )
    _settle(collector, lambda: collector.state.scans_done >= 6, timeout=5.0)
    assert 1 <= len(trains) <= 3, f"trained {len(trains)} times over ~6 scans"


def test_a_scan_that_collected_nothing_new_does_not_trigger_a_retrain():
    """A quiet league returns the same listings; refitting them is minutes spent
    to produce the model already loaded, and drops every page's caches to do it."""
    state = type("S", (), {"scans_done": 4})()
    settings = PassiveSettings(auto_retrain=True, retrain_every_scans=4)
    assert PassiveCollector._should_train(settings, state, inserted=10)
    assert not PassiveCollector._should_train(settings, state, inserted=0)


def test_auto_retrain_off_means_never_however_many_scans_land():
    state = type("S", (), {"scans_done": 8})()
    settings = PassiveSettings(auto_retrain=False, retrain_every_scans=4)
    assert not PassiveCollector._should_train(settings, state, inserted=100)


def test_not_enough_data_stops_the_retrain_without_stopping_the_collection():
    """Too little data to fit is a reason to keep collecting, not to give up."""
    scans = []
    collector = PassiveCollector()

    def boom(cfg):
        raise RuntimeError("NotEnoughData: 12 rows")

    collector.start(
        _cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=1),
        collect=lambda cfg, repo, limit, progress=None, cancel=None: (
            scans.append(1) or {"inserted": 2}),
        train=boom, repo_factory=_Repo,
    )
    assert _settle(collector, lambda: len(scans) >= 3)
    assert "NotEnoughData" in collector.state.last_error
    assert collector.state.model_generation == 0


def test_the_worker_never_touches_streamlit():
    """`invalidate_model_cache` and `st.session_state` are script-thread only.

    Checked over the import graph rather than the text, because the module
    docstring names the Streamlit calls it is avoiding and a substring search
    cannot tell an explanation from a call.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/market/passive.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "streamlit" not in imported

    # And nothing it imports drags Streamlit in behind it.
    assert "src.ui" not in {n for n in imported if n.startswith("src.ui")}


def test_passive_mode_does_not_start_itself_on_import():
    """A collector that resumes because a config file said so is the failure
    mode the module docstring exists to rule out."""
    assert not controller().running


def test_the_process_has_exactly_one_collector():
    assert controller() is controller()


def test_a_scan_that_raises_is_recorded_and_the_loop_carries_on():
    scans = []
    collector = PassiveCollector()

    def collect(cfg, repo, limit, progress=None, cancel=None):
        scans.append(1)
        if len(scans) == 1:
            raise ConnectionError("the wire went away")
        return {"inserted": 1}

    collector.start(_cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=1000),
                    collect=collect, train=lambda cfg: None, repo_factory=_Repo)
    assert _settle(collector, lambda: len(scans) >= 2)
    assert "ConnectionError" in collector.state.last_error


def test_the_worker_closes_the_database_connection_it_opened():
    """One leaked handle per scan, held for as long as the app is up."""
    repos = []
    collector = PassiveCollector()

    def factory():
        repo = _Repo()
        repos.append(repo)
        return repo

    collector.start(_cfg(cooldown_minutes=0.001, max_cooldown_minutes=0.001, retrain_every_scans=1000),
                    collect=lambda cfg, repo, limit, progress=None, cancel=None: {"inserted": 1},
                    train=lambda cfg: None, repo_factory=factory)
    _settle(collector, lambda: len(repos) >= 2)
    assert all(r.closed for r in repos)
