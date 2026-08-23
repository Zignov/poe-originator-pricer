from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Item text fixtures. These are real 3.29 items: the trade API returns the exact
# clipboard text in item.extended.text (base64), and these were captured from it.
# ---------------------------------------------------------------------------
T16_UNCORRUPTED_RARE = """Item Class: Maps
Rarity: Rare
Terror Solace
Map (Tier 16)
--------
Item Quantity: +84% (augmented)
Item Rarity: +134% (augmented)
Monster Pack Size: +32% (augmented)
More Maps: +35% (augmented)
More Currency: +47% (augmented)
--------
Item Level: 84
--------
Monster Level: 83
--------
Area is Influenced by the Originator's Memories
--------
23% increased Monster Damage
Monsters have 30% increased Area of Effect
Players have 25% less Accuracy Rating
--------
Travel to a Map of this tier or lower by using this in a personal Map Device. Maps can only be used once.
"""

T16_CORRUPTED = """Item Class: Maps
Rarity: Rare
Hollow Crosscut
Map (Tier 16)
--------
Item Quantity: +110% (augmented)
Item Rarity: +65% (augmented)
Monster Pack Size: +41% (augmented)
More Scarabs: +36% (augmented)
More Currency: +47% (augmented)
--------
Item Level: 85
--------
Monster Level: 83
--------
Area is Influenced by the Originator's Memories
--------
Players have -10% to all maximum Resistances
Monsters have 100% increased Area of Effect
--------
Travel to a Map of this tier or lower by using this in a personal Map Device. Maps can only be used once.
--------
Corrupted
"""

T16_NO_REWARDS = """Item Class: Maps
Rarity: Normal
Map (Tier 16)
--------
Item Level: 83
--------
Monster Level: 83
--------
Area is Influenced by the Originator's Memories
--------
Travel to a Map of this tier or lower by using this in a personal Map Device. Maps can only be used once.
--------
Note: ~b/o 26 mirror
"""

T6_MAGIC_ORIGINATOR = """Item Class: Maps
Rarity: Magic
Valdo's Map of Penetration (Tier 6)
--------
Item Quantity: +32% (augmented)
Item Rarity: +83% (augmented)
Monster Pack Size: +26% (augmented)
--------
Item Level: 73
--------
Monster Level: 73
--------
Area is Influenced by the Originator's Memories
--------
Monster Damage Penetrates 15% Elemental Resistances
Rare monsters in area are Shaper-Touched
--------
Travel to a Map of this tier or lower by using this in a personal Map Device. Maps can only be used once.
"""

PLAIN_T16_MAP = """Item Class: Maps
Rarity: Rare
Blood Bower
Map (Tier 16)
--------
Item Quantity: +79% (augmented)
Item Rarity: +45% (augmented)
Monster Pack Size: +28% (augmented)
--------
Item Level: 83
--------
Monster Level: 83
--------
Players have 25% less Accuracy Rating
--------
Travel to a Map of this tier or lower by using this in a personal Map Device. Maps can only be used once.
"""


@pytest.fixture
def api_item_uncorrupted() -> dict:
    """Shape verified against a live 3.29 trade-API response."""
    return {
        "verified": True,
        "w": 1,
        "h": 1,
        "league": "Allflame",
        "id": "abc123",
        "memoryItem": True,
        "name": "Terror Solace",
        "typeLine": "Map (Tier 16)",
        "baseType": "Map (Tier 16)",
        "rarity": "Rare",
        "ilvl": 84,
        "identified": True,
        "x": 3,
        "y": 5,
        "properties": [
            {"name": "Item Quantity", "values": [["+84%", 1]], "displayMode": 0, "type": 2},
            {"name": "Item Rarity", "values": [["+134%", 1]], "displayMode": 0, "type": 3},
            {"name": "Monster Pack Size", "values": [["+32%", 1]], "displayMode": 0, "type": 4},
            {"name": "More Maps", "values": [["+35%", 1]], "displayMode": 0, "type": 88},
            {"name": "More Currency", "values": [["+47%", 1]], "displayMode": 0, "type": 90},
        ],
        "implicitMods": [
            {
                "description": "Area is Influenced by the Originator's Memories",
                "domain": "implicit",
                "hash": "stat.implicit.stat_2696470877",
            }
        ],
        "explicitMods": [{"description": "23% increased Monster Damage", "domain": "explicit"}],
        "extended": {
            "hashes": {"implicit": [["implicit.stat_2696470877", [0]]]},
        },
        "frameType": 2,
    }


@pytest.fixture
def api_listing_uncorrupted(api_item_uncorrupted) -> dict:
    return {
        "id": "listing-1",
        "listing": {
            "method": "psapi",
            "indexed": "2026-08-22T18:00:00Z",
            "stash": {"name": "~price 9 divine", "x": 3, "y": 5},
            "price": {"type": "~price", "amount": 9.0, "currency": "divine"},
            "account": {"name": "Exile#1234"},
        },
        "item": api_item_uncorrupted,
    }
