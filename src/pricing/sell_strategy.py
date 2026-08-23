"""Turning a fair-value estimate into a listing price.

Fair market value and suggested listing price are deliberately separate numbers.
The multipliers start from config, but once enough market data exists they are
re-estimated from the observed spread of comparable listings rather than staying
fixed percentages forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

STRATEGIES = ("quick_sell", "market", "patient")
STRATEGY_LABELS = {
    "quick_sell": "Quick sell",
    "market": "Market",
    "patient": "Patient",
}

#: Below this many comparables the observed spread is too noisy to trust.
MIN_COMPARABLES_FOR_SPREAD = 25

#: Range the median ask/estimate ratio must fall in for the observed spread to be
#: believable as seller behaviour rather than model miscalibration.
SANE_MARKET_RATIO = (0.6, 1.7)


@dataclass
class StrategyMultipliers:
    quick_sell: float
    market: float
    patient: float
    source: str  # "config" or "observed-spread"

    def get(self, strategy: str) -> float:
        return getattr(self, strategy)

    def as_dict(self) -> dict[str, Any]:
        return {
            "quick_sell": self.quick_sell,
            "market": self.market,
            "patient": self.patient,
            "source": self.source,
        }


def multipliers_from_config(cfg_strategies: dict[str, float]) -> StrategyMultipliers:
    return StrategyMultipliers(
        quick_sell=float(cfg_strategies.get("quick_sell", 0.94)),
        market=float(cfg_strategies.get("market", 0.99)),
        patient=float(cfg_strategies.get("patient", 1.06)),
        source="config",
    )


def estimate_multipliers(
    listings: pd.DataFrame,
    fallback: StrategyMultipliers,
    predicted_column: str = "predicted_chaos",
    actual_column: str = "price_chaos",
) -> StrategyMultipliers:
    """Derive the multipliers from how real asks are spread around fair value.

    `listings` must carry both the model's estimate and the actual asking price
    for the same items. The ratio distribution of ask/estimate is what a seller
    is choosing between, so its 25th / 50th / 75th percentiles become quick /
    market / patient.
    """
    if listings is None or listings.empty:
        return fallback
    if predicted_column not in listings or actual_column not in listings:
        return fallback

    frame = listings[[predicted_column, actual_column]].dropna()
    frame = frame[(frame[predicted_column] > 0) & (frame[actual_column] > 0)]
    if len(frame) < MIN_COMPARABLES_FOR_SPREAD:
        return fallback

    ratio = (frame[actual_column] / frame[predicted_column]).to_numpy()
    # Trim the tails so a single troll listing cannot move the strategy.
    lo, hi = np.quantile(ratio, [0.05, 0.95])
    ratio = ratio[(ratio >= lo) & (ratio <= hi)]
    if ratio.size < MIN_COMPARABLES_FOR_SPREAD:
        return fallback

    quick, market, patient = (float(v) for v in np.quantile(ratio, [0.25, 0.50, 0.75]))

    # A median ask far from the estimate is not seller behaviour, it is a
    # miscalibrated model -- and silently multiplying the prediction by 0.2 would
    # hide that behind a plausible-looking number. Refuse the correction and say
    # why, so the miscalibration surfaces instead of being papered over.
    if not SANE_MARKET_RATIO[0] <= market <= SANE_MARKET_RATIO[1]:
        return StrategyMultipliers(
            quick_sell=fallback.quick_sell,
            market=fallback.market,
            patient=fallback.patient,
            source=(
                f"config (observed spread rejected: median ask is {market:.2f}x the "
                f"estimate, so the model looks miscalibrated -- retrain)"
            ),
        )

    return StrategyMultipliers(
        quick_sell=round(quick, 4),
        market=round(market, 4),
        patient=round(patient, 4),
        source="observed-spread",
    )


def suggested_price(fair_value: float, strategy: str, multipliers: StrategyMultipliers) -> float:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    return fair_value * multipliers.get(strategy)


def round_for_listing(divine: float) -> float:
    """Round to an amount a player would actually type into a note."""
    if divine >= 100:
        return float(round(divine))
    if divine >= 20:
        return float(round(divine * 2) / 2)
    if divine >= 5:
        return float(round(divine * 10) / 10)
    return float(round(divine * 100) / 100)
