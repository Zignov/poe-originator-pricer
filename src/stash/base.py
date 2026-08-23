"""Stash source interface.

Every adapter -- official API, session-cookie API, JSON export, clipboard --
returns the same StashTab / NormalizedMap objects, so the rest of the app never
knows or cares where the items came from. If GGG changes an endpoint, only one
adapter is replaced.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Sequence

from src.parsing.item_parser import classify, parse_api_item
from src.schema import NormalizedMap


@dataclass
class StashTab:
    id: str
    name: str
    index: int = 0
    type: str = ""
    colour: str | None = None
    #: Grid width/height when the tab type is a grid. None for non-grid tabs
    #: (currency, fragment, quad tabs report 24x24).
    grid_width: int | None = None
    grid_height: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class StashSource(abc.ABC):
    name: str = "base"
    #: Human explanation shown in the UI when the source is unavailable.
    setup_hint: str = ""

    @abc.abstractmethod
    def list_tabs(self, league: str) -> list[StashTab]:
        """Tabs the user can choose from."""

    @abc.abstractmethod
    def fetch_tab(self, league: str, tab: StashTab) -> list[NormalizedMap]:
        """Every item in one tab, normalized."""

    def available(self) -> tuple[bool, str]:
        return True, ""

    def fetch_tabs(self, league: str, tabs: Sequence[StashTab]) -> list[NormalizedMap]:
        out: list[NormalizedMap] = []
        for tab in tabs:
            out.extend(self.fetch_tab(league, tab))
        return out


# ---------------------------------------------------------------------------
# Shared item conversion
# ---------------------------------------------------------------------------
DEFAULT_GRID = 12
QUAD_GRID = 24


def grid_size_for(tab: StashTab, items: Sequence[NormalizedMap]) -> tuple[int, int]:
    """Best available guess at the tab's grid dimensions.

    GGG's stash payload does not always state the grid size, so this falls back
    to the standard 12x12, widening to 24x24 (quad) if any item sits outside.
    """
    if tab.grid_width and tab.grid_height:
        return tab.grid_width, tab.grid_height
    if (tab.type or "").lower() in ("quadstash", "quad"):
        return QUAD_GRID, QUAD_GRID
    max_x = max((i.x or 0) + (i.width or 1) for i in items) if items else 0
    max_y = max((i.y or 0) + (i.height or 1) for i in items) if items else 0
    if max_x > DEFAULT_GRID or max_y > DEFAULT_GRID:
        return QUAD_GRID, QUAD_GRID
    return DEFAULT_GRID, DEFAULT_GRID


def items_from_api_payload(
    items: Sequence[dict[str, Any]],
    tab: StashTab | None,
    map_tier: int = 16,
    require_uncorrupted: bool = True,
) -> list[NormalizedMap]:
    """Convert a stash payload's `items` array into classified NormalizedMaps."""
    out: list[NormalizedMap] = []
    for raw in items or []:
        try:
            nm = parse_api_item(raw)
        except Exception:
            continue
        if tab is not None:
            nm.stash_tab_id = tab.id
            nm.stash_tab_name = tab.name
        out.append(classify(nm, map_tier=map_tier, require_uncorrupted=require_uncorrupted))
    return out


def only_maps(items: Sequence[NormalizedMap]) -> list[NormalizedMap]:
    """Drop things that are clearly not maps, so a mixed tab still works."""
    kept: list[NormalizedMap] = []
    for item in items:
        klass = (item.raw_item_data.get("item_class") or "") if item.raw_item_data else ""
        looks_like_map = (
            item.is_originator
            or "map" in (item.base_type or "").lower()
            or klass.lower() == "maps"
        )
        if looks_like_map:
            kept.append(item)
    return kept
