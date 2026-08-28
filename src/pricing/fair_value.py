"""The pricing service: item in, priced row out.

This is the seam the UI and the CLI both sit on. It combines the model estimate,
the comparable listings and the selling strategy into one result object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from src.config import Config
from src.database.repository import PRICING_COLUMNS, Repository
from src.market.comparables import ComparablePool, market_reference_price
from src.ml.decision_bands import verdict
from src.ml.predict import Estimate, Predictor
from src.pricing.currency import CurrencyConverter
from src.pricing.display import PriceFormatter, formatter_from
from src.pricing.sell_strategy import (
    StrategyMultipliers,
    multipliers_from_config,
    suggested_price,
)
from src.schema import NormalizedMap


@dataclass
class PricedMap:
    item: NormalizedMap
    estimate: Estimate | None
    comparables: pd.DataFrame = field(default_factory=pd.DataFrame)
    suggested_chaos: float | None = None
    market_reference_chaos: float | None = None
    strategy: str = "market"
    note: str = ""
    #: Which pricing mode produced this row -- "model" or "market". The two modes
    #: sit beside each other rather than one replacing the other, so a caller
    #: holding a list of these has to be able to tell which it is looking at.
    source: str = "model"
    #: The live derivation behind a market-comparison row: the comparables, the
    #: tolerance the search had to reach, and what was discarded. `None` on a
    #: model row. Typed loosely to keep the import one-directional.
    derivation: Any | None = None

    @property
    def supported(self) -> bool:
        return self.item.supported and self.estimate is not None

    #: Price columns, so the UI can format them all the same way without
    #: hardcoding the list in every page.
    PRICE_COLUMNS = ("Fair Value", "Suggested", "Low", "High", "Market Median")

    def row(self, formatter: PriceFormatter) -> dict[str, Any]:
        est = self.estimate
        amount = formatter.value
        return {
            "Pos": self.item.position_label(),
            "Tab": self.item.stash_tab_name or "",
            "Currency": self.item.more_currency,
            "Maps": self.item.more_maps,
            "Scarabs": self.item.more_scarabs,
            "Div Cards": self.item.more_divination_cards,
            "Pack": self.item.pack_size,
            "Qty": self.item.quantity,
            "Rarity": self.item.rarity,
            "Fair Value": amount(est.fair_value_chaos) if est else None,
            "Suggested": amount(self.suggested_chaos),
            "Low": amount(est.interval_low_chaos) if est else None,
            "High": amount(est.interval_high_chaos) if est else None,
            "Market Median": amount(self.market_reference_chaos),
            # What to do with it. The number alone stopped being enough once the
            # model gained a training floor: below it every map comes back at
            # roughly the same figure, which reads as a cheap price rather than
            # as "no price". See src/ml/decision_bands.py.
            "Action": verdict(est.fair_value_chaos) if est else "",
            "Confidence": est.confidence if est else "-",
            "Status": "" if self.supported else (self.item.unsupported_reason or "Unsupported"),
            "_fingerprint": self.item.fingerprint(),
        }


class PricingService:
    def __init__(
        self,
        cfg: Config,
        repo: Repository,
        predictor: Predictor | None,
        converter: CurrencyConverter,
        multipliers: StrategyMultipliers | None = None,
        formatter: PriceFormatter | None = None,
    ):
        self.cfg = cfg
        self.repo = repo
        self.predictor = predictor
        self.converter = converter
        self.formatter = formatter or formatter_from(cfg, converter.divine_to_chaos)
        self.multipliers = multipliers or multipliers_from_config(
            cfg.get("pricing.strategies", {})
        )
        #: Built on first use and shared by every map priced through this
        #: service. See `comparable_pool`.
        self._pool: ComparablePool | None = None

    def comparable_pool(self) -> ComparablePool:
        """The listing snapshot every comparables search on this service shares.

        Memoised on the instance rather than globally, because a service is
        built per request (per Streamlit rerun, per CLI invocation) and that is
        exactly the lifetime over which the listings cannot change underneath
        it. A pool that outlived the request would need invalidating; this one
        does not.
        """
        if self._pool is None:
            self._pool = ComparablePool.build(
                self.repo,
                league=self.cfg.get("league"),
                max_age_days=self.cfg.get("market.max_age_days"),
                map_tier=self.cfg.get("scope.map_tier"),
                require_uncorrupted=self.cfg.get("scope.require_uncorrupted"),
            )
        return self._pool

    def price(
        self,
        item: NormalizedMap,
        strategy: str = "market",
        comparables: int = 8,
        estimate: Estimate | None = None,
    ) -> PricedMap:
        """One priced map. `estimate` skips the model call when a batch made it."""
        if not item.supported or self.predictor is None:
            return PricedMap(item=item, estimate=None, strategy=strategy)

        if estimate is None:
            estimate = self.predictor.estimate(item, self.converter.divine_to_chaos)
        comps = self.comparable_pool().nearest(item, limit=comparables)
        suggested = suggested_price(estimate.fair_value_chaos, strategy, self.multipliers)
        # Round to a number a player would actually type, then convert back so
        # the stored figure and the displayed note never disagree.
        amount, unit = self.formatter.listing_amount(suggested)
        rounded_chaos = self.formatter.to_chaos(amount, unit)
        return PricedMap(
            item=item,
            estimate=estimate,
            comparables=comps,
            suggested_chaos=rounded_chaos,
            market_reference_chaos=market_reference_price(comps),
            strategy=strategy,
            note=f"~price {amount:g} {unit}",
        )

    def price_from_market(
        self,
        item: NormalizedMap,
        derivation,
        comparables: pd.DataFrame | None = None,
        strategy: str = "market",
    ) -> PricedMap:
        """A `PricedMap` from live comparables instead of the model.

        Everything downstream of the number is shared with the model path on
        purpose -- the same selling-strategy multipliers, the same rounding to a
        price a player would actually type, the same `Estimate` shape. The two
        modes differ in where the fair value comes from and nowhere else, which
        is what makes the side-by-side comparison meaningful.
        """
        estimate = Estimate(
            fair_value_chaos=derivation.fair_value_chaos,
            interval_low_chaos=derivation.interval_low_chaos,
            interval_high_chaos=derivation.interval_high_chaos,
            confidence=derivation.confidence,
            confidence_score=derivation.confidence_score,
            confidence_reasons=list(derivation.confidence_reasons),
            model_version=f"live market ({derivation.tolerance:g}x tolerance)",
        )
        suggested = suggested_price(estimate.fair_value_chaos, strategy, self.multipliers)
        amount, unit = self.formatter.listing_amount(suggested)
        frame = comparables if comparables is not None else pd.DataFrame()
        reference = None
        if not frame.empty and "price_chaos" in frame:
            reference = float(frame["price_chaos"].median())
        return PricedMap(
            item=item,
            estimate=estimate,
            comparables=frame,
            suggested_chaos=self.formatter.to_chaos(amount, unit),
            market_reference_chaos=reference,
            strategy=strategy,
            note=f"~price {amount:g} {unit}",
            source="market",
            derivation=derivation,
        )

    @classmethod
    def build(
        cls,
        cfg: Config,
        repo: Repository,
        predictor: Predictor | None,
        converter: CurrencyConverter,
    ) -> "PricingService":
        """Construct with strategy multipliers derived from the market when possible.

        Falls back to the configured values when there is not enough data for the
        observed spread to be meaningful.
        """
        multipliers = multipliers_from_config(cfg.get("pricing.strategies", {}))
        if predictor is not None:
            multipliers = observed_multipliers(cfg, repo, predictor, multipliers)
        return cls(
            cfg,
            repo,
            predictor,
            converter,
            multipliers,
            formatter_from(cfg, converter.divine_to_chaos),
        )

    def price_many(
        self, items: Sequence[NormalizedMap], strategy: str = "market", comparables: int = 8
    ) -> list[PricedMap]:
        """Price a whole tab.

        The two costs that do not have to be paid per map are lifted out here:
        the model runs once over every priceable map rather than once each, and
        the comparables all search one shared `ComparablePool`. What is left in
        the loop is the part that genuinely differs per map.
        """
        if self.predictor is None:
            return [PricedMap(item=item, estimate=None, strategy=strategy) for item in items]

        priceable = [item for item in items if item.supported]
        estimates = dict(
            zip(
                (id(item) for item in priceable),
                self.predictor.estimate_many(priceable, self.converter.divine_to_chaos),
            )
        )
        return [
            self.price(item, strategy, comparables, estimate=estimates.get(id(item)))
            for item in items
        ]

    def to_frame(self, priced: Sequence[PricedMap]) -> pd.DataFrame:
        return pd.DataFrame([p.row(self.formatter) for p in priced])


def observed_multipliers(
    cfg: Config,
    repo: Repository,
    predictor: Predictor,
    fallback: StrategyMultipliers,
) -> StrategyMultipliers:
    """Re-derive quick / market / patient from how real asks sit around fair value.

    Every recent in-scope listing is scored by the model, and the distribution of
    ask / estimate is what a seller is actually choosing between. Falls back to
    the configured multipliers when the sample is too small to be meaningful.
    """
    import numpy as np

    from src.ml.features import rows_to_frame, target_inverse
    from src.pricing.sell_strategy import estimate_multipliers

    rows = repo.listing_rows(
        league=cfg.get("league"),
        include_outliers=False,
        max_age_days=cfg.get("market.max_age_days"),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        columns=PRICING_COLUMNS,
    )
    frame = rows_to_frame(rows)
    if frame.empty:
        return fallback
    missing = [c for c in predictor.columns if c not in frame.columns]
    if missing:
        return fallback

    predicted = target_inverse(
        np.asarray(predictor.model.predict(frame[predictor.columns]), dtype=float),
        predictor.target_mode,
    )
    frame = frame.assign(predicted_chaos=np.clip(predicted, 0.0, None))
    return estimate_multipliers(frame, fallback)
