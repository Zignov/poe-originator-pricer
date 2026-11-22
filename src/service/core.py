"""The pricing core, assembled once and callable without any UI.

This is the whole point of the service layer: everything below `src/ui` and
`src/overlay` already has no presentation in it, but the only things that knew
how to *assemble* it were a Streamlit page and a CLI command. A native overlay,
a Tauri shell or a test harness had no way in that did not drag Streamlit's
caching and session state along with it.

So the assembly moves here. `PricingCore` holds the config, the repository, the
predictor and the pricing service, and answers the two questions a client
actually has: price these feature values, and price this copied item text.

Nothing in this module imports Streamlit, and that is the contract. Path of
Building has run the same arrangement for years -- its entire calculation
engine is tested through `HeadlessWrapper.lua`, outside its GUI host -- and it
is what makes the UI technology a reversible decision rather than a one-way
door.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Sequence

from src.config import Config, load_config
from src.database.repository import Repository
from src.market.ingest import converter_for
from src.ml.predict import ModelNotTrained, ModelSchemaMismatch, Predictor
from src.parsing.item_parser import classify, parse_item_text
from src.pricing.display import PriceFormatter
from src.pricing.fair_value import PricedMap, PricingService
from src.pricing.sell_strategy import STRATEGIES
from src.schema import FEATURE_NAMES, NormalizedMap


class CoreUnavailable(RuntimeError):
    """The core cannot price anything, and the message says why."""


@dataclass
class PricingCore:
    """Everything needed to price a map, with no UI attached."""

    cfg: Config
    repo: Repository
    predictor: Predictor | None
    service: PricingService
    formatter: PriceFormatter
    model_error: str = ""

    # -- construction ---------------------------------------------------
    @classmethod
    def build(cls, cfg: Config | None = None) -> "PricingCore":
        """Assemble from config. A missing model is reported, not raised.

        The app is useful without a trained model -- the market-comparison
        path and the importers still work -- so failing to load one leaves
        `predictor` as None and records why, exactly as the Streamlit layer
        already does.
        """
        cfg = cfg or load_config()
        repo = Repository(cfg.db_path)
        predictor: Predictor | None = None
        why = ""
        try:
            predictor = Predictor.load(cfg)
        except (ModelNotTrained, ModelSchemaMismatch) as exc:
            why = str(exc)

        converter = converter_for(cfg, repo)
        service = PricingService.build(cfg, repo, predictor, converter)
        return cls(
            cfg=cfg,
            repo=repo,
            predictor=predictor,
            service=service,
            formatter=service.formatter,
            model_error=why,
        )

    # -- what a client asks for -----------------------------------------
    def price_values(
        self,
        rows: Sequence[dict[str, Any]],
        strategy: str = "market",
    ) -> list[PricedMap]:
        """Price maps given as plain feature dictionaries."""
        return self.price_items([map_from_values(row) for row in rows], strategy)

    def price_text(self, text: str, strategy: str = "market") -> PricedMap:
        """Price one map from the text the game puts on the clipboard.

        `classify` is the load-bearing call here, and leaving it out is the bug
        the clipboard price checker found on its first real run: parsing fills
        in the rolls but `NormalizedMap.supported` defaults to False, so an
        item that was only parsed comes back "not one the pricer covers" --
        every time, for every map. `map_from_values` sets `supported` itself,
        which is why the `/price` route worked while this one never had.
        """
        if not (text or "").strip():
            raise CoreUnavailable("no item text was supplied")
        item = classify(
            parse_item_text(text),
            map_tier=self.cfg.get("scope.map_tier"),
            require_uncorrupted=self.cfg.get("scope.require_uncorrupted"),
        )
        return self.price_items([item], strategy)[0]

    def price_items(
        self, items: Sequence[NormalizedMap], strategy: str = "market"
    ) -> list[PricedMap]:
        if strategy not in STRATEGIES:
            raise CoreUnavailable(
                f"unknown strategy {strategy!r}; expected one of {', '.join(STRATEGIES)}"
            )
        if not items:
            return []
        return self.service.price_many(list(items), strategy=strategy)

    # -- what a client needs to know ------------------------------------
    def status(self) -> dict[str, Any]:
        meta = self.predictor.metadata if self.predictor else {}
        return {
            "league": self.cfg.get("league"),
            "model_version": self.predictor.version if self.predictor else None,
            "model_backend": meta.get("backend"),
            "model_trained_at": meta.get("trained_at"),
            "model_league": meta.get("league"),
            "model_error": self.model_error or None,
            "comparables": int(
                self.predictor.training_features.shape[0] if self.predictor else 0
            ),
            "divine_to_chaos": float(self.formatter.divine_to_chaos),
            "display_unit": self.formatter.unit,
            "strategies": list(STRATEGIES),
        }


def map_from_values(row: dict[str, Any]) -> NormalizedMap:
    """A NormalizedMap from a feature dictionary, with the rest defaulted.

    Unknown keys are ignored rather than rejected: a client sending a whole
    stash row should not have to strip it down to the seven features first,
    and a typo in an optional field is not worth refusing a price over.
    """
    values: dict[str, Any] = {}
    for name in FEATURE_NAMES:
        try:
            values[name] = int(row.get(name, 0) or 0)
        except (TypeError, ValueError):
            raise CoreUnavailable(f"{name!r} must be a number, got {row.get(name)!r}")
    for name in ("item_level", "quality", "map_tier"):
        if row.get(name) is not None:
            try:
                values[name] = int(row[name])
            except (TypeError, ValueError):
                pass
    return NormalizedMap(
        is_originator=True,
        supported=True,
        corrupted=bool(row.get("corrupted", False)),
        stash_item_id=row.get("id"),
        x=row.get("x"),
        y=row.get("y"),
        **values,
    )


def priced_to_dict(priced: PricedMap, formatter: PriceFormatter) -> dict[str, Any]:
    """One priced map as JSON, in chaos and in the display unit.

    Both, deliberately. Chaos is what everything is stored and modelled in, so
    a client doing arithmetic should use it; the display figures exist so a
    client does not have to reimplement the unit rules to show a number.
    """
    est = priced.estimate
    return {
        "supported": priced.supported,
        "note": priced.note or None,
        "strategy": priced.strategy,
        "source": priced.source,
        "chaos": {
            "fair_value": est.fair_value_chaos if est else None,
            "suggested": priced.suggested_chaos,
            "low": est.interval_low_chaos if est else None,
            "high": est.interval_high_chaos if est else None,
            "market_reference": priced.market_reference_chaos,
        },
        "display": {
            "unit": formatter.suffix(est.fair_value_chaos if est else None),
            "fair_value": formatter.value(est.fair_value_chaos) if est else None,
            "suggested": formatter.value(priced.suggested_chaos),
            "formatted": formatter.format(priced.suggested_chaos),
            # The interval, formatted here rather than left to the client.
            # Added when the clipboard price checker became the first consumer
            # and needed to *show* a range: it had the two chaos figures and
            # the unit, and still could not render "310-480c" without
            # reimplementing when a price switches to divine and how it rounds.
            # That is precisely the duplication this block exists to prevent,
            # so the rule it follows is that anything the user reads as a
            # number is formatted on this side of the boundary.
            "low": formatter.value(est.interval_low_chaos) if est else None,
            "high": formatter.value(est.interval_high_chaos) if est else None,
            "range": (
                formatter.range(est.interval_low_chaos, est.interval_high_chaos)
                if est
                else ""
            ),
        },
        "confidence": {
            "level": est.confidence if est else None,
            "score": est.confidence_score if est else None,
            "reasons": list(est.confidence_reasons) if est else [],
        },
        "item": {
            name: getattr(priced.item, name) for name in FEATURE_NAMES
        }
        | {
            "position": priced.item.position_label(),
            "tab": priced.item.stash_tab_name or None,
            "corrupted": priced.item.corrupted,
            # Reward names this build could not place. Carried across the
            # boundary because the client is what the user is looking at, and
            # a reward read as 0 is otherwise indistinguishable from a reward
            # the map genuinely does not have.
            "unknown_rewards": list(priced.item.unknown_rewards),
        },
    }


# ---------------------------------------------------------------------------
# One core per process.
#
# Loading the model and opening the database is not free, and the service is a
# long-lived local process answering many requests. The lock matters because a
# server handles requests on a thread pool: without it, two simultaneous first
# requests would each build a core, and the loser's Repository would be left
# open with nothing holding it.
# ---------------------------------------------------------------------------
_CORE: PricingCore | None = None
_LOCK = threading.Lock()


def get_core(cfg: Config | None = None) -> PricingCore:
    global _CORE
    if _CORE is None:
        with _LOCK:
            if _CORE is None:
                _CORE = PricingCore.build(cfg)
    return _CORE


def reset_core() -> None:
    """Drop the cached core. For tests, and after retraining."""
    global _CORE
    with _LOCK:
        _CORE = None
