"""Currency normalization to chaos.

Only the divine rate is user-configurable and time-stamped, because it is the
only one that moves enough during a league to matter for map pricing. Everything
else uses a static, clearly-labelled approximation and is treated as low
confidence; listings in those currencies are rare for T16 Originator Maps.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Rough static ratios, only used for currencies that almost never appear on map
# listings. `divine` is deliberately absent -- it always comes from the database
# or config so it can never silently go stale.
STATIC_CHAOS_RATES: dict[str, float] = {
    "chaos": 1.0,
    "alch": 0.2,
    "alt": 0.05,
    "fusing": 0.3,
    "regal": 0.3,
    "vaal": 1.0,
    "annul": 4.0,
    "chrome": 0.05,
    "jewellers": 0.05,
    "exalted": 10.0,
}

# Mirror is included so absurd price-fixing listings convert to something the
# outlier detector can reject numerically rather than dropping them silently.
MIRROR_IN_DIVINE = 300.0


class CurrencyConverter:
    def __init__(self, divine_to_chaos: float, as_of: datetime | None = None):
        if divine_to_chaos <= 0:
            raise ValueError("divine_to_chaos must be positive")
        self.divine_to_chaos = float(divine_to_chaos)
        self.as_of = as_of or datetime.now(timezone.utc)

    def to_chaos(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None or currency is None:
            return None
        cur = currency.strip().lower()
        if cur == "divine":
            return amount * self.divine_to_chaos
        if cur == "mirror":
            return amount * MIRROR_IN_DIVINE * self.divine_to_chaos
        rate = STATIC_CHAOS_RATES.get(cur)
        if rate is None:
            return None
        return amount * rate

    def chaos_to_divine(self, chaos: float | None) -> float | None:
        if chaos is None:
            return None
        return chaos / self.divine_to_chaos

    def supported(self, currency: str | None) -> bool:
        if not currency:
            return False
        cur = currency.strip().lower()
        return cur in ("divine", "mirror") or cur in STATIC_CHAOS_RATES
