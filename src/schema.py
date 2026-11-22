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

# How each reward value is labelled. Verified against live trade API responses:
# these arrive as item *properties*, not as implicit modifiers. The numeric ids
# are the `type` field on the property object.
#
# GGG relabels these between leagues, and the Covens change is the case worth
# keeping in mind when reading this file. Two of the seven moved:
#
#   Allflame                  Covens                        type id
#   Item Quantity             Atlas Drops                    2 -> 112
#   More Scarabs              More Threads                   89 (unchanged)
#
# Both names and ids are therefore accumulating aliases rather than being
# replaced. A map from an older league, an older export or a community CSV
# still has to parse, so nothing here is ever removed -- only added to.
PROPERTY_TO_FEATURE: dict[str, str] = {
    "Item Quantity": "quantity",
    "Atlas Drops": "quantity",
    "Item Rarity": "rarity",
    "Monster Pack Size": "pack_size",
    "More Maps": "more_maps",
    "More Scarabs": "more_scarabs",
    "More Threads": "more_scarabs",
    "More Currency": "more_currency",
    "More Divination Cards": "more_divination_cards",
}

PROPERTY_TYPE_ID_TO_FEATURE: dict[int, str] = {
    2: "quantity",
    112: "quantity",  # Covens' "Atlas Drops": a new id, not a renamed one
    3: "rarity",
    4: "pack_size",
    88: "more_maps",
    89: "more_scarabs",  # also Covens' "More Threads" -- GGG kept the id
    90: "more_currency",
    91: "more_divination_cards",
}

# The display name for each feature, which is the *only* thing that should
# change when GGG relabels a reward.
#
# The feature keys themselves never change, and `more_scarabs` is the one that
# makes people want to break that rule -- in Covens it holds thread counts, not
# scarab counts. It stays because the key is a **slot identifier**, not a claim
# about what the slot currently contains, and GGG said as much by keeping type
# id 89 through the rename. Renaming the key would mean a database migration
# and a `feature_list` that no existing model matches, for no gain the label
# below does not already give.
FEATURE_LABELS: dict[str, str] = {
    "quantity": "Atlas Drops",
    "rarity": "Item Rarity",
    "pack_size": "Monster Pack Size",
    "more_currency": "More Currency",
    "more_maps": "More Maps",
    "more_scarabs": "More Threads",
    "more_divination_cards": "More Divination Cards",
}

# The short forms, for a table column or a popup over the game.
FEATURE_SHORT_LABELS: dict[str, str] = {
    "quantity": "Atlas",
    "rarity": "Rar",
    "pack_size": "Pack",
    "more_currency": "Cur",
    "more_maps": "Maps",
    "more_scarabs": "Thr",
    "more_divination_cards": "Div",
}

# The implicit that marks a map as Originator-influenced.
ORIGINATOR_IMPLICIT_TEXT = "Area is Influenced by the Originator's Memories"
ORIGINATOR_IMPLICIT_STAT_ID = "implicit.stat_2696470877"

# The other influences a map can carry, which this project excludes.
#
# A map can be Originator *and* Shaper, Elder or one of the four Conquerors at
# the same time. Those are real Originator maps with readable rolls, so nothing
# is wrong with them -- but the model has seven features and not one of them is
# "carries Elder influence". An extra influence changes what a map sells for
# without changing any number the model can see, so in training it is noise
# with no signal attached, and when buying it is a map priced for a reason the
# app cannot explain.
#
# They are about 2.7% of the market (4,072 listings against 3,961 with these
# excluded, measured live on 2026-11-22), so dropping them costs very little
# and removes an unexplainable term from every price.
#
# Ids verified against /api/trade/data/stats on 2026-11-22. The `|n` suffix
# selects which influence within a shared stat, so each has to be named.
OTHER_INFLUENCE_STAT_IDS: tuple[str, ...] = (
    "implicit.stat_1792283443|1",   # Area is influenced by The Shaper
    "implicit.stat_1792283443|2",   # Area is influenced by The Elder
    "implicit.stat_3624393862|1",   # Map is occupied by The Enslaver
    "implicit.stat_3624393862|2",   # Map is occupied by The Eradicator
    "implicit.stat_3624393862|3",   # Map is occupied by The Constrictor
    "implicit.stat_3624393862|4",   # Map is occupied by The Purifier
)

# The same influences as item text, for filtering things that never went
# through the trade API -- a stash read, a clipboard paste, a CSV import.
OTHER_INFLUENCE_TEXTS: tuple[str, ...] = (
    "influenced by The Shaper",
    "influenced by The Elder",
    "occupied by The Enslaver",
    "occupied by The Eradicator",
    "occupied by The Constrictor",
    "occupied by The Purifier",
)


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
    #: Reward-shaped properties this build did not recognise. Not persisted and
    #: never part of the fingerprint: it is a statement about the parser, not
    #: about the item, and the same map read by a newer build should hash the
    #: same. It exists so a league rename surfaces as a visible warning instead
    #: of a feature silently reading zero -- see src/parsing/implicit_parser.py.
    unknown_rewards: list[str] = field(default_factory=list)

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
