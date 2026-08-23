"""Import stash contents from a JSON or CSV file the user exports themselves.

This is the fallback that always works and needs no credentials at all. It
accepts any of the shapes people actually have on disk:

  * the raw response of /character-window/get-stash-items  ({"items": [...]})
  * the OAuth /stash response                              ({"stash": {"items": [...]}})
  * a bare list of item objects                            ([{...}, {...}])
  * a dict of tab name -> item list
  * a CSV with one row per map
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable

from src.schema import FEATURE_NAMES, NormalizedMap
from src.stash.base import StashSource, StashTab, items_from_api_payload, only_maps


class JsonImportError(ValueError):
    pass


class JsonStashSource(StashSource):
    name = "json_import"
    setup_hint = (
        "Save a stash JSON export (or a CSV with one row per map) and load it here. "
        "No account credentials are needed."
    )

    def __init__(
        self,
        payload: Any = None,
        path: str | Path | None = None,
        map_tier: int = 16,
        require_uncorrupted: bool = True,
    ):
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted
        if path is not None:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.payload = payload
        self._tabs: dict[str, list[dict[str, Any]]] = _split_into_tabs(payload)

    def available(self) -> tuple[bool, str]:
        if not self._tabs:
            return False, "no items found in the imported payload"
        return True, ""

    def list_tabs(self, league: str) -> list[StashTab]:
        return [
            StashTab(id=name, name=name, index=index)
            for index, name in enumerate(self._tabs)
        ]

    def fetch_tab(self, league: str, tab: StashTab) -> list[NormalizedMap]:
        raw_items = self._tabs.get(tab.id, [])
        items = items_from_api_payload(raw_items, tab, self.map_tier, self.require_uncorrupted)
        return only_maps(items)


def _split_into_tabs(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if payload is None:
        return {}
    if isinstance(payload, list):
        return _group_by_tab_name(payload)
    if isinstance(payload, dict):
        if "items" in payload and isinstance(payload["items"], list):
            name = _tab_name_from_payload(payload)
            return _group_by_tab_name(payload["items"], default=name)
        if "stash" in payload and isinstance(payload["stash"], dict):
            inner = payload["stash"]
            return _group_by_tab_name(inner.get("items", []), default=inner.get("name", "Imported"))
        if "stashes" in payload and isinstance(payload["stashes"], list):
            out: dict[str, list[dict[str, Any]]] = {}
            for stash in payload["stashes"]:
                out[stash.get("name") or stash.get("id") or "Imported"] = stash.get("items", [])
            return {k: v for k, v in out.items() if v}
        # tab name -> item list
        if all(isinstance(v, list) for v in payload.values()) and payload:
            return {str(k): v for k, v in payload.items() if v}
    raise JsonImportError("unrecognised stash export shape")


def _tab_name_from_payload(payload: dict[str, Any]) -> str:
    tabs = payload.get("tabs") or []
    index = payload.get("tabIndex")
    if isinstance(tabs, list) and isinstance(index, int):
        for tab in tabs:
            if tab.get("i") == index:
                return tab.get("n") or tab.get("name") or "Imported"
    return "Imported"


def _group_by_tab_name(items: Iterable[dict[str, Any]], default: str = "Imported") -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("_tabName") or item.get("inventoryId") or default
        out.setdefault(str(name), []).append(item)
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
CSV_COLUMNS = (
    "position",
    "tab",
    "map_tier",
    "item_level",
    "corrupted",
    *FEATURE_NAMES,
)


def parse_csv(text: str, map_tier: int = 16, require_uncorrupted: bool = True) -> list[NormalizedMap]:
    """Read a CSV with one row per map.

    Only the seven reward columns are required; everything else is optional and
    defaults to an uncorrupted map of the configured tier.
    """
    from src.parsing.item_parser import classify

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise JsonImportError("CSV has no header row")
    missing = [c for c in FEATURE_NAMES if c not in reader.fieldnames]
    if missing:
        raise JsonImportError(f"CSV is missing required columns: {missing}")

    out: list[NormalizedMap] = []
    for index, row in enumerate(reader):
        position = (row.get("position") or "").strip()
        x, y = _parse_position(position)
        nm = NormalizedMap(
            stash_tab_name=(row.get("tab") or "CSV import").strip(),
            x=x if x is not None else index % 12,
            y=y if y is not None else index // 12,
            base_type=f"Map (Tier {row.get('map_tier') or map_tier})",
            map_tier=int(row.get("map_tier") or map_tier),
            item_level=int(row["item_level"]) if (row.get("item_level") or "").strip() else None,
            corrupted=str(row.get("corrupted", "")).strip().lower() in ("1", "true", "yes"),
            is_originator=True,
            raw_item_data={"csv_row": row},
            **{name: int(float(row.get(name) or 0)) for name in FEATURE_NAMES},
        )
        out.append(classify(nm, map_tier=map_tier, require_uncorrupted=require_uncorrupted))
    return out


def _parse_position(label: str) -> tuple[int | None, int | None]:
    """'A1' -> (0, 0). Anything unparseable returns (None, None)."""
    label = label.strip().upper()
    if not label:
        return None, None
    letters = "".join(ch for ch in label if ch.isalpha())
    digits = "".join(ch for ch in label if ch.isdigit())
    if not letters or not digits:
        return None, None
    column = 0
    for ch in letters:
        column = column * 26 + (ord(ch) - ord("A") + 1)
    return column - 1, int(digits) - 1
