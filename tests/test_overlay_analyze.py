"""Re-reading your tab from the overlay, without alt-tabbing to the browser.

The overlay's *reload* re-reads a file. Analyze re-reads the *tab*. The
difference is the whole point, and it hangs on one thing: an export has to
remember where it came from, or Analyze has nothing to repeat.

So the failures worth guarding are about provenance, and they are all the same
shape -- pressing Analyze and getting numbers for something other than what is
on screen:

- an export from a clipboard paste has no live source at all, and must say so
  rather than fetching "whatever is in the first tab";
- a tab that has been renamed must still be found, by id;
- a tab that is genuinely gone must be reported, not silently swapped for
  another;
- a failed fetch must leave the last good prices on screen, because stale
  prices you can see the age of beat no prices at all when you are mid-map.

And one that is not about provenance: the fetch must never run on the Tk
thread. A window sitting on top of a game is the worst possible place to
freeze for the four seconds a model load takes.
"""
from __future__ import annotations

import time

import pytest

from src.config import Config
from src.overlay.analyze import (
    AnalyzeError,
    AnalyzeResult,
    AnalyzeWorker,
    _tabs_to_fetch,
    analyze,
    source_adapter,
)
from src.overlay.calibration import Calibration
from src.overlay.profiles import INVENTORY_PROFILE, STASH_PROFILE
from src.overlay.state import (
    MODE_MARKET,
    OVERLAY_STATE_VERSION,
    SOURCE_INVENTORY,
    SOURCE_MANUAL,
    SOURCE_STASH,
    OverlaySource,
    OverlayState,
    OverlayStateError,
    OverlayTile,
    read_state,
    state_from_priced,
    write_state,
)
from src.pricing.display import PriceFormatter
from src.pricing.fair_value import Estimate, PricedMap
from src.schema import NormalizedMap
from src.stash.base import StashTab


# ---------------------------------------------------------------------------
# Fakes: a stash adapter and a pricing service, with no network and no model.
# ---------------------------------------------------------------------------
def _item(x: int, y: int, tab_id: str = "tab-1", tab_name: str = "Originator Maps"):
    return NormalizedMap(
        stash_item_id=f"i{x}{y}",
        stash_tab_id=tab_id,
        stash_tab_name=tab_name,
        x=x,
        y=y,
        name="Terror Solace",
        base_type="Map (Tier 16)",
        map_tier=16,
        is_originator=True,
        supported=True,
        quantity=84,
        rarity=134,
        pack_size=32,
        more_maps=35,
    )


class FakeAdapter:
    def __init__(self, tabs, items, available=(True, "")):
        self._tabs = tabs
        self._items = items
        self._available = available
        self.fetched: list[str] = []

    def available(self):
        return self._available

    def list_tabs(self, league):
        return list(self._tabs)

    def fetch_tabs(self, league, tabs):
        self.fetched.extend(t.name for t in tabs)
        return list(self._items)


class FakeService:
    """Prices everything at a fixed number, so a re-price is visible."""

    def __init__(self, price: float):
        self.price = price
        self.formatter = PriceFormatter(unit="chaos", divine_to_chaos=220.0)

    def price_many(self, items, strategy="market"):
        return [
            PricedMap(
                item=item,
                estimate=Estimate(
                    fair_value_chaos=self.price,
                    interval_low_chaos=self.price * 0.8,
                    interval_high_chaos=self.price * 1.2,
                    confidence="High",
                    confidence_score=0.9,
                    model_version="fake",
                ),
                suggested_chaos=self.price,
                strategy=strategy,
            )
            for item in items
        ]


class FakeEngine:
    def __init__(self, price: float = 100.0):
        self._service = FakeService(price)
        self.closed = False

    def service(self):
        return self._service

    def verdicts(self, priced):
        return {}

    def close(self):
        self.closed = True


@pytest.fixture
def cfg():
    return Config({"league": "Allflame", "realm": "pc", "scope": {"map_tier": 16}})


def _state(source: OverlaySource, price: float = 10.0) -> OverlayState:
    return OverlayState(
        tiles=(OverlayTile(x=0, y=0, price_chaos=price, label=f"{price:.0f}c"),),
        strategy="market",
        league="Allflame",
        source=source,
    )


# ---------------------------------------------------------------------------
# Provenance: what an export remembers about where it came from
# ---------------------------------------------------------------------------
def test_the_state_contract_carries_the_source_and_survives_a_round_trip(tmp_path):
    source = OverlaySource(
        kind=SOURCE_INVENTORY,
        league="Allflame",
        names=("Bowzerker",),
        ids=("Bowzerker",),
        mode=MODE_MARKET,
    )
    path = write_state(_state(source), tmp_path / "s.json")
    assert read_state(path).source == source


def test_a_version_one_export_is_refused_rather_than_analysed(tmp_path):
    """It has no source block, so Analyze would have nothing to repeat.

    Refusing is the same behaviour every other schema change gets: a wrong
    number on a tile is worse than no tile.
    """
    import json

    payload = _state(OverlaySource()).to_dict()
    payload["version"] = 1
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OverlayStateError):
        read_state(path)
    assert OVERLAY_STATE_VERSION == 2


def test_a_manual_import_is_not_repeatable_and_says_so(cfg, tmp_path):
    """A clipboard paste describes maps, not a place to go and read them again."""
    state = _state(OverlaySource(kind=SOURCE_MANUAL, names=("pasted",)))
    assert not state.source.repeatable
    with pytest.raises(AnalyzeError) as exc:
        analyze(cfg, state, path=tmp_path / "s.json", engine=FakeEngine())
    assert "manual import" in str(exc.value)


def test_the_manual_adapter_is_refused_before_any_credential_is_touched(cfg):
    with pytest.raises(AnalyzeError):
        source_adapter(cfg, OverlaySource(kind=SOURCE_MANUAL))


def test_the_two_live_kinds_build_the_adapters_they_name(cfg):
    from src.stash.character_api import CharacterInventorySource
    from src.stash.official_api import SessionStashSource

    stash = source_adapter(cfg, OverlaySource(kind=SOURCE_STASH))
    inventory = source_adapter(cfg, OverlaySource(kind=SOURCE_INVENTORY))
    assert isinstance(stash, SessionStashSource)
    assert isinstance(inventory, CharacterInventorySource)


# ---------------------------------------------------------------------------
# Finding the tab again
# ---------------------------------------------------------------------------
def test_a_renamed_tab_is_still_found_by_its_id():
    """Tabs get renamed mid-league; ids do not change. Matching on the name
    alone would report the tab as gone and send you back to the browser."""
    available = [StashTab(id="tab-1", name="Maps (sorted)"), StashTab(id="tab-2", name="Other")]
    source = OverlaySource(kind=SOURCE_STASH, names=("Originator Maps",), ids=("tab-1",))
    assert [t.name for t in _tabs_to_fetch(source, available)] == ["Maps (sorted)"]


def test_a_tab_with_no_id_recorded_falls_back_to_matching_the_name():
    available = [StashTab(id="new-id", name="Originator Maps")]
    source = OverlaySource(kind=SOURCE_STASH, names=("Originator Maps",))
    assert [t.id for t in _tabs_to_fetch(source, available)] == ["new-id"]


def test_a_tab_that_is_genuinely_gone_is_reported_not_swapped():
    """Silently pricing a different tab is the worst thing Analyze could do."""
    available = [StashTab(id="tab-9", name="Currency")]
    source = OverlaySource(
        kind=SOURCE_STASH, league="Allflame", names=("Originator Maps",), ids=("tab-1",)
    )
    with pytest.raises(AnalyzeError) as exc:
        _tabs_to_fetch(source, available)
    assert "Originator Maps" in str(exc.value)


# ---------------------------------------------------------------------------
# The analysis itself
# ---------------------------------------------------------------------------
def test_analyze_refetches_reprices_and_rewrites_the_file(cfg, tmp_path, monkeypatch):
    tabs = [StashTab(id="tab-1", name="Originator Maps")]
    adapter = FakeAdapter(tabs, [_item(2, 3), _item(4, 1)])
    monkeypatch.setattr("src.overlay.analyze.source_adapter", lambda *_a: adapter)

    path = tmp_path / "overlay_state.json"
    before = _state(
        OverlaySource(kind=SOURCE_STASH, league="Allflame", names=("Originator Maps",),
                      ids=("tab-1",)),
        price=10.0,
    )
    write_state(before, path)

    result = analyze(cfg, before, path=path, engine=FakeEngine(price=250.0))

    assert result.ok
    assert "2 maps re-priced" in result.message
    assert adapter.fetched == ["Originator Maps"]

    after = read_state(path)
    assert {(t.x, t.y) for t in after.tiles} == {(2, 3), (4, 1)}
    assert all(t.price_chaos == 250.0 for t in after.tiles)
    # And the new file is still repeatable: analysing twice in a row has to work.
    assert after.source.repeatable
    assert after.source.ids == ("tab-1",)


def test_analyze_keeps_the_mode_and_the_strategy_it_was_given(cfg, tmp_path, monkeypatch):
    """Re-pricing must not quietly swap the model in for the live market."""
    adapter = FakeAdapter([StashTab(id="tab-1", name="Maps")], [_item(0, 0)])
    monkeypatch.setattr("src.overlay.analyze.source_adapter", lambda *_a: adapter)
    seen = {}

    class RecordingEngine(FakeEngine):
        def service(self):
            service = super().service()
            original = service.price_many

            def price_many(items, strategy="market"):
                seen["strategy"] = strategy
                return original(items, strategy=strategy)

            service.price_many = price_many
            return service

    before = OverlayState(
        strategy="patient",
        league="Allflame",
        source=OverlaySource(kind=SOURCE_STASH, league="Allflame", names=("Maps",),
                             ids=("tab-1",)),
    )
    result = analyze(cfg, before, path=tmp_path / "s.json", engine=RecordingEngine())
    assert seen["strategy"] == "patient"
    assert result.state.strategy == "patient"


def test_the_inventory_profile_writes_a_five_row_grid(cfg, tmp_path, monkeypatch):
    adapter = FakeAdapter([StashTab(id="Bowzerker", name="Bowzerker")], [_item(0, 0)])
    monkeypatch.setattr("src.overlay.analyze.source_adapter", lambda *_a: adapter)
    before = _state(
        OverlaySource(kind=SOURCE_INVENTORY, league="Allflame", names=("Bowzerker",),
                      ids=("Bowzerker",))
    )
    result = analyze(
        cfg, before, profile=INVENTORY_PROFILE, path=tmp_path / "s.json", engine=FakeEngine()
    )
    assert result.state.grid == (12, 5)


def test_an_unavailable_credential_is_reported_in_words_a_user_can_act_on(
    cfg, tmp_path, monkeypatch
):
    adapter = FakeAdapter([], [], available=(False, "POESESSID is not set"))
    monkeypatch.setattr("src.overlay.analyze.source_adapter", lambda *_a: adapter)
    before = _state(OverlaySource(kind=SOURCE_STASH, names=("Maps",), ids=("tab-1",)))
    with pytest.raises(AnalyzeError) as exc:
        analyze(cfg, before, path=tmp_path / "s.json", engine=FakeEngine())
    assert "POESESSID" in str(exc.value)
    assert "Settings" in str(exc.value)


# ---------------------------------------------------------------------------
# Off the Tk thread
# ---------------------------------------------------------------------------
def _wait_for(worker: AnalyzeWorker, timeout: float = 10.0) -> AnalyzeResult:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = worker.poll()
        if found is not None:
            return found
        time.sleep(0.01)
    raise AssertionError("the analysis never came back")


def test_the_worker_returns_its_result_through_a_queue(cfg, tmp_path, monkeypatch):
    adapter = FakeAdapter([StashTab(id="tab-1", name="Maps")], [_item(0, 0)])
    monkeypatch.setattr("src.overlay.analyze.source_adapter", lambda *_a: adapter)

    worker = AnalyzeWorker(cfg)
    worker._engine = FakeEngine(price=99.0)
    path = tmp_path / "s.json"
    state = _state(OverlaySource(kind=SOURCE_STASH, league="Allflame", names=("Maps",),
                                 ids=("tab-1",)))

    assert "analysing" in worker.start(state, STASH_PROFILE, path)
    result = _wait_for(worker)
    assert result.ok
    assert read_state(path).tiles[0].price_chaos == 99.0


def test_a_failing_fetch_comes_back_as_a_message_not_an_exception(cfg, tmp_path, monkeypatch):
    """The window must survive a dead connection with its last prices intact."""

    def explode(*_a):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("src.overlay.analyze.source_adapter", explode)
    worker = AnalyzeWorker(cfg)
    worker._engine = FakeEngine()
    state = _state(OverlaySource(kind=SOURCE_STASH, names=("Maps",), ids=("tab-1",)))

    worker.start(state, STASH_PROFILE, tmp_path / "s.json")
    result = _wait_for(worker)
    assert not result.ok
    assert "connection reset" in result.message


def test_nothing_to_analyse_yet_is_a_message_not_a_crash(cfg, tmp_path):
    worker = AnalyzeWorker(cfg)
    assert "nothing to analyse" in worker.start(None, STASH_PROFILE, tmp_path / "s.json")
    assert not worker.busy


def test_a_manual_export_never_starts_a_thread(cfg, tmp_path):
    worker = AnalyzeWorker(cfg)
    said = worker.start(_state(OverlaySource(kind=SOURCE_MANUAL)), STASH_PROFILE, tmp_path / "s")
    assert "manual import" in said
    assert not worker.busy


# ---------------------------------------------------------------------------
# The chord
# ---------------------------------------------------------------------------
def test_each_profile_has_its_own_analyse_chord_distinct_from_its_toggle():
    """Showing a window costs nothing; re-reading a tab spends an API call.
    One press must never be mistakable for the other, on either surface."""
    stash = Calibration.from_config(Config({}), STASH_PROFILE)
    inventory = Calibration.from_config(Config({}), INVENTORY_PROFILE)
    chords = {
        stash.hotkey_toggle,
        stash.hotkey_analyze,
        inventory.hotkey_toggle,
        inventory.hotkey_analyze,
    }
    assert len(chords) == 4, chords


def test_both_analyse_chords_are_ones_windows_can_actually_register():
    from src.overlay.hotkeys import parse_hotkey

    for profile in (STASH_PROFILE, INVENTORY_PROFILE):
        modifiers, key = parse_hotkey(profile.analyze_hotkey)
        assert modifiers and key


def test_the_analyse_chord_is_persisted_with_the_rest_of_the_calibration(tmp_path):
    from src.config import load_config
    from src.overlay.calibration import save_to_config

    cfg = Config({})
    cal = Calibration.from_config(cfg, INVENTORY_PROFILE)
    cal = type(cal)(**{**cal.__dict__, "hotkey_analyze": "ctrl+f9", "screen": (2560, 1080)})
    path = save_to_config(cfg, cal, tmp_path / "config.yaml")
    assert Calibration.from_config(load_config(path), INVENTORY_PROFILE).hotkey_analyze == "ctrl+f9"


# ---------------------------------------------------------------------------
# What the page stamps onto an export
# ---------------------------------------------------------------------------
def test_state_from_priced_defaults_to_a_manual_source():
    """An export nobody stamped must not claim to be repeatable."""
    state = state_from_priced([], PriceFormatter())
    assert state.source.kind == SOURCE_MANUAL
    assert not state.source.repeatable
