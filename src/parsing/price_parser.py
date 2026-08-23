"""Parsing of prices, from both API price objects and stash-tab note text."""
from __future__ import annotations

import re
from typing import Any

# "~price 9.8 divine", "~b/o 250 chaos", "~gb/o 3 exalted", "price 1 mirror"
_NOTE = re.compile(
    r"~?\s*(?P<type>b/o|price|gb/o)\s+(?P<amount>\d+(?:[.,]\d+)?)\s*(?:/\s*\d+)?\s*(?P<currency>[a-zA-Z\-']+)",
    re.I,
)

# Canonical currency ids as used by the trade API.
CURRENCY_ALIASES: dict[str, str] = {
    "c": "chaos",
    "chaos": "chaos",
    "chaosorb": "chaos",
    "d": "divine",
    "div": "divine",
    "divine": "divine",
    "ex": "exalted",
    "exa": "exalted",
    "exalted": "exalted",
    "mir": "mirror",
    "mirror": "mirror",
    "alch": "alch",
    "alt": "alt",
    "vaal": "vaal",
    "annul": "annul",
    "regal": "regal",
    "fusing": "fusing",
    "chrom": "chrome",
    "chrome": "chrome",
    "jew": "jewellers",
    "jewellers": "jewellers",
}


class PriceParseError(ValueError):
    pass


def normalize_currency(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().lower().replace(" ", "").replace("-", "")
    key = key.removesuffix("orb").removesuffix("orbs") or key
    return CURRENCY_ALIASES.get(key, name.strip().lower())


def parse_price_note(note: str | None) -> dict[str, Any] | None:
    """Parse a stash-tab note such as '~price 9.8 divine'."""
    if not note:
        return None
    m = _NOTE.search(note)
    if not m:
        return None
    amount = float(m.group("amount").replace(",", "."))
    return {
        "price_type": "~" + m.group("type").lower(),
        "price_amount": amount,
        "price_currency": normalize_currency(m.group("currency")),
    }


def parse_api_price(listing: dict[str, Any] | None) -> dict[str, Any]:
    """Pull the price out of a trade-API `listing` object."""
    price = (listing or {}).get("price") or {}
    amount = price.get("amount")
    return {
        "price_type": price.get("type"),
        "price_amount": float(amount) if amount is not None else None,
        "price_currency": normalize_currency(price.get("currency")),
    }


def format_price_note(amount: float, currency: str = "divine") -> str:
    """Produce the note text the user pastes into the game, e.g. '~price 9.8 divine'."""
    if amount >= 100:
        text = f"{amount:.0f}"
    elif amount >= 10:
        text = f"{amount:.1f}"
    else:
        text = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"~price {text} {currency}"
