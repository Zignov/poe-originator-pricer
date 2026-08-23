"""The one normalized item representation shared by every subsystem.

Stash adapters, market collectors and the model all speak this schema. If GGG
changes an API, only the adapter that produces a NormalizedMap needs replacing.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# The seven pricing features, in a fixed order. This order is written into model
# metadata and checked at load time -- a model whose feature list does not match
# is refused rather than silently mispredicting.
FEATURE_NAMES: tuple[str, ...] = (
    "quantity",
    "rarity",
    "pack_size",
    "more_currency",
    "more_maps",
    "more_scarabs",
    "more_divination_cards",
)

# How each reward value is labelled. Verified against live 3.29 trade API
# responses: these arrive as item *properties*, not as implicit modifiers.
# The numeric ids are the `type` field on the property object.
PROPERTY_TO_FEATURE: dict[str, str] = {
    "Item Quantity": "quantity",
    "Item Rarity": "rarity",
    "Monster Pack Size": "pack_size",
    "More Maps": "more_maps",
    "More Scarabs": "more_scarabs",
    "More Currency": "more_currency",
    "More Divination Cards": "more_divination_cards",
}

PROPERTY_TYPE_ID_TO_FEATURE: dict[int, str] = {
    2: "quantity",
    3: "rarity",
    4: "pack_size",
    88: "more_maps",
    89: "more_scarabs",
    90: "more_currency",
}

# The implicit that marks a map as Originator-influenced.
ORIGINATOR_IMPLICIT_TEXT = "Area is Influenced by the Originator's Memories"
ORIGINATOR_IMPLICIT_STAT_ID = "implicit.stat_2696470877"


@dataclass
class NormalizedMap:
    """A single map, wherever it came from."""

    # -- identity / location ------------------------------------------
    stash_item_id: str | None = None
    stash_tab_id: str | None = None
    stash_tab_name: str | None = None
    x: int | None = None
    y: int | None = None
    width: int = 1
    height: int = 1

    # -- item description ---------------------------------------------
    name: str = ""
    base_type: str = ""
    map_tier: int | None = None
    item_level: int | None = None
    quality: int = 0
    corrupted: bool = False
    item_rarity_frame: str = ""       # Normal / Magic / Rare / Unique
    is_originator: bool = False

    # -- the seven pricing features ------------------------------------
    quantity: int = 0
    rarity: int = 0
    pack_size: int = 0
    more_currency: int = 0
    more_maps: int = 0
    more_scarabs: int = 0
    more_divination_cards: int = 0

    # -- provenance -----------------------------------------------------
    raw_modifiers: list[str] = field(default_factory=list)
    raw_item_data: dict[str, Any] = field(default_factory=dict)

    # -- eligibility ------------------------------------------------------
    supported: bool = False
    unsupported_reason: str = ""

    def features(self) -> dict[str, int]:
        return {n: int(getattr(self, n)) for n in FEATURE_NAMES}

    def feature_vector(self) -> list[int]:
        return [int(getattr(self, n)) for n in FEATURE_NAMES]

    def position_label(self) -> str:
        """Human stash coordinate, e.g. 'A1'. Columns are letters, rows 1-based."""
        if self.x is None or self.y is None:
            return "-"
        col = ""
        n = self.x
        while True:
            col = chr(ord("A") + n % 26) + col
            n = n // 26 - 1
            if n < 0:
                break
        return f"{col}{self.y + 1}"

    def fingerprint(self) -> str:
        """Stable identity for de-duplication when no listing id is available."""
        payload = json.dumps(
            {
                "base": self.base_type,
                "name": self.name,
                "tier": self.map_tier,
                "ilvl": self.item_level,
                "corrupt": self.corrupted,
                "feat": self.feature_vector(),
                "mods": sorted(self.raw_modifiers),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketListing:
    """One observed market listing: a NormalizedMap plus a price."""

    item: NormalizedMap
    listing_id: str | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    indexed_at: datetime | None = None
    league: str = ""
    price_amount: float | None = None
    price_currency: str | None = None
    price_type: str | None = None          # "~b/o", "~price", "~gb/o" ...
    price_chaos: float | None = None
    seller: str | None = None
    source: str = "unknown"
    is_outlier: bool = False
    outlier_reason: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> str:
        if self.listing_id:
            return self.listing_id
        payload = "|".join(
            str(x)
            for x in (
                self.seller,
                self.price_amount,
                self.price_currency,
                self.item.fingerprint(),
                self.indexed_at.isoformat() if self.indexed_at else "",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
