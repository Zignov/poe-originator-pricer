"""Import maps from item text copied out of the game with Ctrl+C.

Handles a single item and a batch: several items pasted one after another are
split on the "Item Class:" / "Rarity:" header that begins each one. Grid
positions are synthesised in paste order so the visualiser still has something
to draw, and are marked as approximate.
"""
from __future__ import annotations

import re

from src.parsing.item_parser import ItemParseError, classify, parse_item_text
from src.schema import NormalizedMap
from src.stash.base import StashSource, StashTab

_ITEM_START = re.compile(r"^Item Class:\s*", re.M)
_RARITY_START = re.compile(r"^Rarity:\s*\w+", re.M)


def split_items(text: str) -> list[str]:
    """Split a paste containing several copied items into individual blocks."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    starts = [m.start() for m in _ITEM_START.finditer(text)]
    if not starts:
        starts = [m.start() for m in _RARITY_START.finditer(text)]
    if not starts:
        return [text]

    if starts[0] > 0:
        starts.insert(0, 0)
    blocks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def parse_clipboard(
    text: str,
    tab_name: str = "Clipboard",
    map_tier: int = 16,
    require_uncorrupted: bool = True,
    columns: int = 12,
) -> list[NormalizedMap]:
    items: list[NormalizedMap] = []
    for index, block in enumerate(split_items(text)):
        try:
            nm = parse_item_text(block)
        except ItemParseError:
            continue
        nm.stash_tab_name = tab_name
        nm.stash_tab_id = tab_name
        # Paste order, not real stash coordinates. The UI labels this tab as
        # approximate so the position is never mistaken for the real one.
        nm.x = index % columns
        nm.y = index // columns
        items.append(classify(nm, map_tier=map_tier, require_uncorrupted=require_uncorrupted))
    return items


class ClipboardStashSource(StashSource):
    name = "clipboard_import"
    setup_hint = (
        "In game, hover a map and press Ctrl+C, then paste here. Several maps can "
        "be pasted one after another. Positions reflect paste order, not the real "
        "stash layout."
    )

    def __init__(
        self,
        text: str = "",
        tab_name: str = "Clipboard",
        map_tier: int = 16,
        require_uncorrupted: bool = True,
    ):
        self.text = text
        self.tab_name = tab_name
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted

    def available(self) -> tuple[bool, str]:
        return (bool(self.text.strip()), "" if self.text.strip() else "nothing pasted yet")

    def list_tabs(self, league: str) -> list[StashTab]:
        return [StashTab(id=self.tab_name, name=self.tab_name, index=0)]

    def fetch_tab(self, league: str, tab: StashTab) -> list[NormalizedMap]:
        return parse_clipboard(
            self.text, tab.name, self.map_tier, self.require_uncorrupted
        )


def read_system_clipboard() -> str:
    """Best-effort read of the OS clipboard. Returns '' if unavailable."""
    try:  # Windows, via tkinter, which ships with CPython
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""
