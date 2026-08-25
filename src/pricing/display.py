"""How prices are shown to the user.

Everything inside the application stores and computes in **chaos**. This module
is the only place that decides what unit the user actually reads, so changing the
display currency never touches the model, the database, or the pricing logic.

Chaos is the default: uncorrupted T16 Originator Maps mostly trade between about
50 and 200 chaos, and "0.28d" is a worse thing to read than "64c".
"""
from __future__ import annotations

from dataclasses import dataclass

#: Above this many chaos, "auto" switches to divine. Roughly the point where the
#: chaos figure stops being a number people quote.
AUTO_DIVINE_THRESHOLD_CHAOS = 2000.0

#: Above this many chaos the market stops being a continuum. Measured on 4,102
#: live listings: above 200c, 71% are priced in divine and *every one of those*
#: is a whole number of divine -- 1d, 2d, 3d or 4d. There is nothing in between,
#: so a suggested "just under 340c" is not a more precise 2 divine, it is a
#: price nobody types. Only the *listing* amount snaps to this grid; the
#: displayed table value does not, because a column where every expensive map
#: reads the same handful of numbers cannot be sorted or compared.
DIVINE_QUANTISED_ABOVE_CHAOS = 200.0

#: ...and stops where the evidence does. Every whole-divine ask observed was 1,
#: 2, 3 or 4 divine; above that the sample is empty (that is corrupted-map
#: territory, outside this model's scope), so snapping a 9.8d figure to 10d
#: would be extrapolating a pattern nobody measured. Beyond this, ordinary
#: rounding applies.
DIVINE_QUANTISED_MAX_DIVINE = 5.0

UNITS = ("chaos", "divine", "auto")

UNIT_LABELS = {
    "chaos": "chaos",
    "divine": "divine",
    "auto": "chaos below ~2000c, divine above",
}


@dataclass
class PriceFormatter:
    """Converts chaos into the unit the user reads, and back."""

    unit: str = "chaos"
    divine_to_chaos: float = 230.0

    def __post_init__(self) -> None:
        if self.unit not in UNITS:
            raise ValueError(f"unknown display unit {self.unit!r}; expected one of {UNITS}")
        if self.divine_to_chaos <= 0:
            raise ValueError("divine_to_chaos must be positive")

    # -- unit choice ----------------------------------------------------
    def unit_for(self, chaos: float | None) -> str:
        """Which unit a given amount is rendered in."""
        if self.unit != "auto":
            return self.unit
        if chaos is None:
            return "chaos"
        return "divine" if abs(chaos) >= AUTO_DIVINE_THRESHOLD_CHAOS else "chaos"

    def suffix(self, chaos: float | None = None) -> str:
        return "c" if self.unit_for(chaos) == "chaos" else "d"

    #: Column header when a whole column shares one unit.
    def column_suffix(self) -> str:
        return "c" if self.unit == "chaos" else "d"

    # -- conversion -----------------------------------------------------
    def value(self, chaos: float | None) -> float | None:
        """Numeric amount in the display unit, for tables and charts."""
        if chaos is None:
            return None
        if self.unit_for(chaos) == "chaos":
            return chaos
        return chaos / self.divine_to_chaos

    def to_chaos(self, amount: float | None, unit: str | None = None) -> float | None:
        if amount is None:
            return None
        unit = unit or (self.unit if self.unit != "auto" else "chaos")
        return amount if unit == "chaos" else amount * self.divine_to_chaos

    # -- rendering ------------------------------------------------------
    def format(self, chaos: float | None, with_suffix: bool = True) -> str:
        if chaos is None:
            return "-"
        unit = self.unit_for(chaos)
        amount = chaos if unit == "chaos" else chaos / self.divine_to_chaos
        text = _round_text(amount, unit)
        return f"{text}{'c' if unit == 'chaos' else 'd'}" if with_suffix else text

    def range(self, low: float | None, high: float | None) -> str:
        if low is None or high is None:
            return "-"
        unit = self.unit_for(high)
        low_text = _round_text(low if unit == "chaos" else low / self.divine_to_chaos, unit)
        return f"{low_text}-{self.format(high)}"

    # -- the note the user pastes into the game -------------------------
    def note(self, chaos: float | None) -> str:
        """Price note in the game's format, e.g. '~price 85 chaos'."""
        if chaos is None:
            return ""
        amount, unit = self.listing_amount(chaos)
        return f"~price {amount:g} {unit}"

    def listing_amount(self, chaos: float | None) -> tuple[float, str] | None:
        """(amount, currency) a seller would actually type.

        Above DIVINE_QUANTISED_ABOVE_CHAOS this ignores the display unit and
        answers in whole divine, because that is the only thing the top of this
        market trades in -- see the constant for the counts.
        """
        if chaos is None:
            return None
        in_divine = chaos / self.divine_to_chaos
        if chaos >= DIVINE_QUANTISED_ABOVE_CHAOS and in_divine <= DIVINE_QUANTISED_MAX_DIVINE:
            # max(1) because the band starts above one divine at any plausible
            # rate; rounding a 1.05d ask down to "0 divine" would be nonsense.
            return float(max(1, round(in_divine))), "divine"
        unit = self.unit_for(chaos)
        amount = chaos if unit == "chaos" else chaos / self.divine_to_chaos
        return round_for_listing(amount, unit), unit


def _round_text(amount: float, unit: str) -> str:
    return f"{round_for_listing(amount, unit):g}"


def round_for_listing(amount: float, unit: str = "chaos") -> float:
    """Round to an amount a player would actually type into a note.

    Nobody lists a map at 63.41 chaos. Chaos rounds to whole numbers (and to the
    nearest 5 once it is large); divine keeps decimals because the numbers are
    small.
    """
    if unit == "chaos":
        if amount >= 500:
            return float(round(amount / 25) * 25)
        if amount >= 100:
            return float(round(amount / 5) * 5)
        if amount >= 10:
            return float(round(amount))
        return float(round(amount, 1))
    if amount >= 100:
        return float(round(amount))
    if amount >= 20:
        return float(round(amount * 2) / 2)
    if amount >= 5:
        return float(round(amount * 10) / 10)
    return float(round(amount * 100) / 100)


def formatter_from(cfg, divine_to_chaos: float) -> PriceFormatter:
    return PriceFormatter(
        unit=cfg.get("display.currency", "chaos"), divine_to_chaos=divine_to_chaos
    )
