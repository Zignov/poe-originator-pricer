"""Repeating the fetch that produced an overlay export, from the overlay itself.

The overlay's *reload* button re-reads the file the app last wrote. That is not
the same thing as new prices, and the difference is the entire reason this
module exists: without it you still alt-tab to a browser and press a button,
which is the step the overlay was built to remove.

Analyze reads the `source` block of the state file -- the provenance the export
carries, see src/overlay/state.py -- reconstructs the adapter from config,
fetches the same tab or the same character again, re-prices it in the same mode
and at the same selling strategy, and writes the state file. The overlay's
existing `StateWatcher` then picks the new file up and redraws through the
normal path. There is deliberately no second drawing path: analysing is a *way
of writing the file*, and everything after that is unchanged.

Three things are worth saying about the shape of this:

- **Nothing credential-shaped is in the state file.** The source block names a
  league, a tab and a mode. The POESESSID comes from the same place it always
  comes from -- `POESESSID` in the environment or a .env -- and is read in this
  process, not carried in a JSON file on disk next to prices.
- **The model and the repository are loaded once and kept.** The first analyze
  pays for loading them; the tenth does not. That matters because the thing
  this is for is running a map, checking the drop, running another one -- a
  loop where a four-second stall every time would send you back to the browser.
- **Market mode is repeated, not silently downgraded to the model.** It is the
  slow, API-spending mode, so the result says what it spent, and the run is
  bounded by the same configured request budget the page uses. Quietly
  substituting model prices for market prices would be worse than either: the
  numbers would keep moving and nothing on screen would say why.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import Config
from src.database.repository import Repository
from src.overlay.profiles import DEFAULT_PROFILE, OverlayProfile, profile_for
from src.overlay.state import (
    MODE_MARKET,
    SOURCE_INVENTORY,
    SOURCE_STASH,
    OverlaySource,
    OverlayState,
    free_affix_slots,
    state_from_priced,
    write_state,
)
from src.parsing.affixes import extract_affixes
from src.pricing.exalt import evaluate_exalt
from src.schema import NormalizedMap
from src.stash.base import StashTab


class AnalyzeError(RuntimeError):
    """The analysis could not be done, with a reason fit to show on the strip."""


@dataclass(frozen=True)
class AnalyzeResult:
    """What one press of Analyze produced."""

    ok: bool
    message: str
    state: OverlayState | None = None
    path: Path | None = None


# ---------------------------------------------------------------------------
# Building the fetch again
# ---------------------------------------------------------------------------
def source_adapter(cfg: Config, source: OverlaySource):
    """The stash adapter that can repeat this export's fetch.

    Only the two live sources are reconstructible. A clipboard paste or a CSV
    describes maps, not a place to go and read them again, so those raise here
    rather than being approximated by "whatever is in the first tab" -- an
    Analyze that silently prices a *different* tab than the one on screen is the
    worst thing this feature could do.
    """
    from src.stash.character_api import CharacterInventorySource
    from src.stash.official_api import SessionStashSource

    common: dict[str, Any] = {
        "realm": cfg.get("realm", "pc"),
        "map_tier": cfg.get("scope.map_tier"),
        "require_uncorrupted": cfg.get("scope.require_uncorrupted"),
        # Left as None on purpose: the adapters fall back to POESESSID and
        # POE_ACCOUNT_NAME in the environment, which is where a .env puts them.
        # There is no credential in the state file and none is passed through
        # one -- the overlay reads the same environment the app does.
        "poesessid": None,
        "account_name": None,
    }
    if source.kind == SOURCE_INVENTORY:
        return CharacterInventorySource(**common)
    if source.kind == SOURCE_STASH:
        return SessionStashSource(**common)
    raise AnalyzeError(
        "This export came from a manual import, so there is nothing live to "
        "read again. Re-price it in the app."
    )


def _tabs_to_fetch(source: OverlaySource, available: list[StashTab]) -> list[StashTab]:
    """Match the export's tabs against what the account has right now.

    By id first and name second. A tab can be renamed between two analyses and
    its id cannot, but the ids of a re-opened snapshot may predate a league
    reset -- so both are tried before giving up, and giving up is explicit.
    """
    wanted_ids = {i for i in source.ids if i}
    wanted_names = {n for n in source.names if n}
    matched = [t for t in available if t.id in wanted_ids]
    if not matched:
        matched = [t for t in available if t.name in wanted_names]
    if not matched:
        had = ", ".join(sorted(wanted_names)) or "the exported tab"
        raise AnalyzeError(f"{had} is not in {source.league} any more. Re-price it in the app.")
    return matched


# ---------------------------------------------------------------------------
# Pricing it again
# ---------------------------------------------------------------------------
class _Engine:
    """The model, catalogue and repository, loaded once and kept.

    Held on the worker rather than rebuilt per analysis: loading a CatBoost
    model is seconds, and the loop this feature serves is "run a map, look,
    run another", where a stall that long is what sends you back to the
    browser.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._repo: Repository | None = None
        self._predictor = None
        self._catalogue = None
        self._loaded = False

    @property
    def repo(self) -> Repository:
        if self._repo is None:
            self._repo = Repository(self.cfg.db_path)
        return self._repo

    def _load(self) -> None:
        if self._loaded:
            return
        from src.ml.affix_catalogue import CATALOGUE_NAME, AffixCatalogue
        from src.ml.predict import ModelNotTrained, ModelSchemaMismatch, Predictor

        try:
            self._predictor = Predictor.load(self.cfg)
        except (ModelNotTrained, ModelSchemaMismatch):
            self._predictor = None
        try:
            self._catalogue = AffixCatalogue.load(self.cfg.model_dir / CATALOGUE_NAME)
        except Exception:  # a corrupt sidecar must not break an analysis
            self._catalogue = None
        self._loaded = True

    def service(self):
        from src.market.ingest import converter_for
        from src.pricing.fair_value import PricingService

        self._load()
        return PricingService.build(
            self.cfg, self.repo, self._predictor, converter_for(self.cfg, self.repo)
        )

    def exalt_cost(self) -> float:
        stored = self.repo.get_setting("exalted_to_chaos")
        if stored:
            return float(stored)
        return float(self.cfg.get("currency.exalted_to_chaos", 1.25) or 1.25)

    def verdicts(self, priced) -> dict[int, Any]:
        """The exalt rings, recomputed so they move with the new prices.

        Skipped entirely when there is no model: `evaluate_exalt` needs one to
        say what an extra affix would be worth, and a tab of ringless tiles is
        the correct drawing of "we cannot tell", which is what the web grid also
        shows.
        """
        self._load()
        if self._predictor is None:
            return {}
        rate = None
        out: dict[int, Any] = {}
        cost = self.exalt_cost()
        for entry in priced:
            try:
                out[id(entry)] = evaluate_exalt(
                    entry.item,
                    self._affixes(entry.item),
                    self._catalogue,
                    self._predictor,
                    cost_chaos=cost,
                    divine_to_chaos=rate,
                )
            except Exception:
                continue
        return out

    def open_affixes(self, priced) -> dict[int, int | None]:
        """The free-slot dots, which must not depend on there being a model.

        `verdicts` above returns nothing at all without a predictor, because an
        exalt judgement needs one to price the outcome. Counting open slots does
        not: it needs the affix block off the payload and the game's own
        prefix/suffix limits, both of which `_affixes` already has. Deriving the
        dots from the verdicts would therefore have made them disappear on a
        machine with no trained model -- silently, and looking exactly like a
        tab of maps that happen to be full.

        Keyed by `id(entry)` like the verdicts, because that is what
        `state_from_priced` reads them back by.
        """
        self._load()
        out: dict[int, int | None] = {}
        for entry in priced:
            try:
                out[id(entry)] = free_affix_slots(self._affixes(entry.item))
            except Exception:
                # A payload shaped in a way the catalogue chokes on is an
                # unknown count, not a failed analysis.
                out[id(entry)] = None
        return out

    def _affixes(self, item: NormalizedMap):
        payload = item.raw_item_data or {}
        if self._catalogue is not None:
            return self._catalogue.read_affixes(payload)
        return extract_affixes(payload)

    def close(self) -> None:
        if self._repo is not None:
            try:
                self._repo.close()
            except Exception:
                pass
            self._repo = None


def analyze(
    cfg: Config,
    state: OverlayState,
    *,
    profile: str | OverlayProfile = DEFAULT_PROFILE,
    path: Path | str | None = None,
    engine: _Engine | None = None,
) -> AnalyzeResult:
    """Fetch this export's source again, re-price it, and write the state file.

    Synchronous and free of Tk, so the whole behaviour is testable without a
    display. `AnalyzeWorker` is the only thing that knows about threads.
    """
    from src.overlay.state import state_path_for

    spec = profile_for(profile)
    source = state.source
    if not source.repeatable:
        raise AnalyzeError(
            "This export came from a manual import, so there is nothing live to "
            "read again. Re-price it in the app."
        )

    owns_engine = engine is None
    engine = engine or _Engine(cfg)
    try:
        adapter = source_adapter(cfg, source)
        ok, why = adapter.available()
        if not ok:
            raise AnalyzeError(f"{why}. Set it in the app's Settings page.")

        league = source.league or cfg.get("league")
        tabs = _tabs_to_fetch(source, adapter.list_tabs(league))
        items = adapter.fetch_tabs(league, tabs)

        service = engine.service()
        if source.mode == MODE_MARKET:
            priced, note = _price_from_market(cfg, engine, service, items, state.strategy)
        else:
            priced = service.price_many(items, strategy=state.strategy)
            note = ""

        fresh = state_from_priced(
            priced,
            service.formatter,
            grid=spec.grid,
            strategy=state.strategy,
            league=league,
            tab_names=[t.name for t in tabs],
            verdicts=engine.verdicts(priced),
            open_affixes=engine.open_affixes(priced),
            source=OverlaySource(
                kind=source.kind,
                league=league,
                names=tuple(t.name for t in tabs),
                ids=tuple(t.id for t in tabs),
                mode=source.mode,
            ),
        )
        written = write_state(fresh, Path(path or state_path_for(spec)))
        drawn = len(fresh.tiles)
        message = f"{drawn} maps re-priced from {source.describe()}"
        return AnalyzeResult(True, (message + note).strip(), fresh, written)
    finally:
        if owns_engine:
            engine.close()


def _price_from_market(cfg, engine, service, items, strategy):
    """The live-listings mode, bounded by the configured request budget.

    The same pricer the page runs, at the budget config already carries, so a
    hotkey press cannot spend more of a rate-limited connection than pressing
    the button in the browser would. What it spent goes back to the strip: an
    API-spending action that reports nothing is one you stop trusting.
    """
    from src.pricing.market_comparison import MarketComparisonPricer

    pricer = MarketComparisonPricer.build(cfg, engine.repo, service)
    run = pricer.run(items)
    priced = pricer.priced(items, run, strategy=strategy)
    note = f"  |  {run.requests_used} requests"
    if run.stopped_reason:
        note += f"  |  {run.stopped_reason}"
    return priced, note


# ---------------------------------------------------------------------------
# Running it without freezing the window
# ---------------------------------------------------------------------------
class AnalyzeWorker:
    """Runs one analysis at a time on a background thread.

    Tk is single-threaded and a fetch plus a model pass is seconds, so doing
    this on the main thread would freeze a window that is sitting on top of the
    game -- the worst possible place to freeze.

    Results come back through a queue and are collected by the same `after`
    loop that already polls the state file, so nothing here ever touches a Tk
    widget from the wrong thread. That rule is the reason this class returns
    results instead of taking a callback.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._engine = _Engine(cfg)
        self._results: queue.Queue[AnalyzeResult] = queue.Queue()
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        state: OverlayState | None,
        profile: str | OverlayProfile = DEFAULT_PROFILE,
        path: Path | str | None = None,
    ) -> str:
        """Kick off an analysis; the returned string is what to put on the strip.

        A second press while one is running is ignored rather than queued. Two
        overlapping fetches of the same tab would spend twice the rate limit to
        produce the same answer, and the second would land after the first and
        redraw the same tiles again.
        """
        if self.busy:
            return "already analysing..."
        if state is None:
            return "nothing to analyse yet -- send a tab from the app first"
        if not state.source.repeatable:
            return "this export came from a manual import; re-price it in the app"

        def run() -> None:
            try:
                self._results.put(
                    analyze(self.cfg, state, profile=profile, path=path, engine=self._engine)
                )
            except AnalyzeError as exc:
                self._results.put(AnalyzeResult(False, str(exc)))
            except Exception as exc:  # a fetch can fail in a hundred ways
                self._results.put(AnalyzeResult(False, f"analysis failed: {exc}"))

        self._thread = threading.Thread(target=run, name="overlay-analyze", daemon=True)
        self._thread.start()
        return f"analysing {state.source.describe()}..."

    def poll(self) -> AnalyzeResult | None:
        """The finished result, once, or None. Called from the Tk timer."""
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._engine.close()
