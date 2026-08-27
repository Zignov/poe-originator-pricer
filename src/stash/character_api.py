"""Reading the maps sitting in a character's inventory.

The stash adapters answer "what is in this tab". This one answers "what is in
my backpack", which is the other half of the same question: maps come out of a
map device into the inventory, and that is exactly the moment you want to know
what they are worth -- before they get dumped into a tab and forgotten.

It is the same credential and the same endpoint family as
`SessionStashSource`, one directory over on GGG's site:

    /character-window/get-characters   -> the account's characters
    /character-window/get-items        -> one character's equipment + inventory

so it subclasses that adapter rather than restating the throttle, the session
cookie and the 403/429 translation.

Two things make it look different from a stash tab, and neither reaches the
rest of the app:

- **A "tab" here is a character.** `list_tabs` returns one `StashTab` per
  character in the league, so the picker, the fetch loop and everything
  downstream work unchanged. The tab id is the character name because that is
  what /get-items is keyed by; GGG exposes no other stable handle.
- **`get-items` returns the whole character.** Equipped items, flasks, the
  jewels in the tree and the map in the map device all arrive in one array,
  distinguished only by `inventoryId`. Only `MainInventory` is a grid with
  meaningful x/y, so everything else is dropped -- an equipped item reports
  x/y inside its *slot*, and drawing those over the inventory panel would put
  prices on cells that hold something else entirely.
"""
from __future__ import annotations

from typing import Any

from src.schema import NormalizedMap
from src.stash.base import StashTab, items_from_api_payload, only_maps
from src.stash.official_api import SessionStashSource, StashAccessError

CHARACTERS_URL = "https://www.pathofexile.com/character-window/get-characters"
ITEMS_URL = "https://www.pathofexile.com/character-window/get-items"

#: The `inventoryId` of the 12x5 backpack. Everything else in a /get-items
#: response is an equipment slot, a flask slot, the map device or the passive
#: tree, and none of those are cells on the inventory grid.
MAIN_INVENTORY = "MainInventory"

#: The inventory panel is 12 across and 5 down, and unlike a stash tab it is
#: never anything else.
INVENTORY_GRID = (12, 5)


class CharacterInventorySource(SessionStashSource):
    """The maps in one character's inventory, over the POESESSID session."""

    name = "character_inventory"
    setup_hint = (
        "Uses the same POESESSID and account name as the stash reader. Your "
        "characters must be visible on your public profile for GGG to return "
        "their items — if the character list comes back empty, turn off "
        '"Hide characters" in your privacy settings on pathofexile.com.'
    )

    def list_tabs(self, league: str) -> list[StashTab]:
        """The account's characters in this league, as pickable "tabs".

        Filtered by league because the endpoint returns every character on the
        account, standard and hardcore and last league's, and offering all of
        them turns a two-entry picker into a thirty-entry one where the wrong
        pick silently prices nothing.
        """
        data = self._request(
            CHARACTERS_URL, {"accountName": self.account_name, "realm": self.realm}
        )
        if isinstance(data, dict):
            # An error body, or a future shape that wraps the list.
            data = data.get("characters") or []
        if not isinstance(data, list):
            raise StashAccessError("character list response was not a list of characters")

        characters: list[StashTab] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if league and str(entry.get("league") or "") != league:
                continue
            name = str(entry.get("name") or "")
            if not name:
                continue
            characters.append(
                StashTab(
                    id=name,
                    name=name,
                    index=len(characters),
                    type="character",
                    grid_width=INVENTORY_GRID[0],
                    grid_height=INVENTORY_GRID[1],
                    raw=entry,
                )
            )
        return characters

    def fetch_tab(self, league: str, tab: StashTab) -> list[NormalizedMap]:
        """Every map in that character's inventory grid."""
        data = self._request(
            ITEMS_URL,
            {
                "accountName": self.account_name,
                "realm": self.realm,
                "character": tab.id or tab.name,
            },
        )
        if not isinstance(data, dict):
            raise StashAccessError("character items response was not a JSON object")
        items = items_from_api_payload(
            inventory_items(data.get("items")),
            tab,
            self.map_tier,
            self.require_uncorrupted,
        )
        return only_maps(items)


def inventory_items(items: Any) -> list[dict[str, Any]]:
    """Just the backpack, out of a whole character's items.

    Split out from the adapter so it can be tested against a captured payload
    without a session: this filter is the one place where a wrong answer puts a
    price on the wrong cell, which is the failure the overlay must not have.
    """
    return [
        item
        for item in (items or [])
        if isinstance(item, dict) and item.get("inventoryId") == MAIN_INVENTORY
    ]
