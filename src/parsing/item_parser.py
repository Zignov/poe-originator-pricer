"""Turn either shape of PoE item data into a NormalizedMap.

Two entry points:
  parse_api_item(dict)  -- trade API / stash API item JSON
  parse_item_text(str)  -- text produced by Ctrl+C in the game client
"""
from __future__ import annotations

import re
from typing import Any

from src.parsing.implicit_parser import (
    empty_features,
    has_originator_implicit,
    parse_api_properties,
    parse_modifier_sentences,
    parse_property_lines,
)
from src.schema import (
    ORIGINATOR_IMPLICIT_STAT_ID,
    ORIGINATOR_IMPLICIT_TEXT,
    NormalizedMap,
)

_TIER_IN_NAME = re.compile(r"\(\s*Tier\s+(\d+)\s*\)", re.I)
_TIER_LINE = re.compile(r"^Map Tier:\s*(\d+)", re.I)
_ILVL_LINE = re.compile(r"^Item Level:\s*(\d+)", re.I)
_QUALITY_LINE = re.compile(r"^Quality:\s*\+?(\d+)%", re.I)
_RARITY_LINE = re.compile(r"^Rarity:\s*(\w+)", re.I)
_ITEM_CLASS_LINE = re.compile(r"^Item Class:\s*(.+)$", re.I)

_FRAME_TYPES = {0: "Normal", 1: "Magic", 2: "Rare", 3: "Unique"}


class ItemParseError(ValueError):
    pass


# ---------------------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------------------
def parse_api_item(item: dict[str, Any]) -> NormalizedMap:
    """Parse a trade-API or stash-API item object."""
    if not isinstance(item, dict):
        raise ItemParseError("item must be a dict")

    implicit_lines = _flatten_mods(item.get("implicitMods"))
    explicit_lines = _flatten_mods(item.get("explicitMods"))
    enchant_lines = _flatten_mods(item.get("enchantMods"))
    crafted_lines = _flatten_mods(item.get("craftedMods"))

    is_originator = has_originator_implicit(implicit_lines)
    if not is_originator:
        hashes = (item.get("extended") or {}).get("hashes") or {}
        for entry in hashes.get("implicit") or []:
            if entry and str(entry[0]) == ORIGINATOR_IMPLICIT_STAT_ID:
                is_originator = True
                break

    features = parse_api_properties(item.get("properties"))
    # A few sources express rewards as modifier text instead of properties.
    for feature, value in parse_modifier_sentences(explicit_lines + implicit_lines).items():
        if features.get(feature, 0) == 0:
            features[feature] = value

    type_line = item.get("typeLine") or ""
    base_type = item.get("baseType") or type_line
    tier = _tier_from_any(item.get("properties"), type_line, base_type, item.get("name") or "")

    return NormalizedMap(
        stash_item_id=item.get("id"),
        x=item.get("x"),
        y=item.get("y"),
        width=item.get("w") or 1,
        height=item.get("h") or 1,
        name=(item.get("name") or "").strip(),
        base_type=base_type,
        map_tier=tier,
        item_level=item.get("ilvl") or item.get("itemLevel"),
        quality=_quality_from_properties(item.get("properties")),
        corrupted=bool(item.get("corrupted", False)),
        item_rarity_frame=item.get("rarity") or _FRAME_TYPES.get(item.get("frameType"), ""),
        is_originator=is_originator,
        raw_modifiers=implicit_lines + explicit_lines + enchant_lines + crafted_lines,
        raw_item_data=item,
        **features,
    )


def _flatten_mods(mods: Any) -> list[str]:
    """Mods arrive either as plain strings or as objects with a `description`."""
    out: list[str] = []
    for m in mods or []:
        if isinstance(m, str):
            out.extend(m.split("\n"))
        elif isinstance(m, dict):
            desc = m.get("description") or m.get("name") or ""
            out.extend(str(desc).split("\n"))
    return [line.strip() for line in out if line and line.strip()]


def _quality_from_properties(properties: list[dict] | None) -> int:
    for prop in properties or []:
        if str(prop.get("name", "")).strip().lower() == "quality":
            values = prop.get("values") or []
            if values and values[0]:
                m = re.search(r"(\d+)", str(values[0][0]))
                if m:
                    return int(m.group(1))
    return 0


def _tier_from_any(properties: list[dict] | None, *texts: str) -> int | None:
    for prop in properties or []:
        if str(prop.get("name", "")).strip().lower() in ("map tier", "tier"):
            values = prop.get("values") or []
            if values and values[0]:
                m = re.search(r"(\d+)", str(values[0][0]))
                if m:
                    return int(m.group(1))
    for text in texts:
        m = _TIER_IN_NAME.search(text or "")
        if m:
            return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Copied item text
# ---------------------------------------------------------------------------
def parse_item_text(text: str) -> NormalizedMap:
    """Parse the text the game puts on the clipboard for an item."""
    if not text or not text.strip():
        raise ItemParseError("empty item text")

    raw_sections = re.split(r"\n\s*-{4,}\s*\n", text.replace("\r\n", "\n").strip())
    sections = [[ln.strip() for ln in s.split("\n") if ln.strip()] for s in raw_sections]
    sections = [s for s in sections if s]
    if not sections:
        raise ItemParseError("no parseable sections in item text")

    header = sections[0]
    item_class = ""
    rarity_frame = ""
    name_lines: list[str] = []
    for line in header:
        m = _ITEM_CLASS_LINE.match(line)
        if m:
            item_class = m.group(1).strip()
            continue
        m = _RARITY_LINE.match(line)
        if m:
            rarity_frame = m.group(1).strip().title()
            continue
        name_lines.append(line)

    if len(name_lines) >= 2:
        name, base_type = name_lines[0], name_lines[-1]
    elif name_lines:
        name, base_type = "", name_lines[0]
    else:
        name, base_type = "", ""

    features = empty_features()
    quality = 0
    tier: int | None = None
    ilvl: int | None = None
    corrupted = False
    is_originator = False
    modifier_lines: list[str] = []

    for section in sections[1:]:
        parsed, unknown = parse_property_lines(section)
        for key, value in parsed.items():
            if value:
                features[key] = value

        for line in section:
            if line.lower() == "corrupted":
                corrupted = True
            mq = _QUALITY_LINE.match(line)
            if mq:
                quality = int(mq.group(1))
            mt = _TIER_LINE.match(line)
            if mt:
                tier = int(mt.group(1))
            mi = _ILVL_LINE.match(line)
            if mi:
                ilvl = int(mi.group(1))

        if has_originator_implicit(section):
            is_originator = True

        # Anything that is not a recognised property line and not stock
        # descriptive footer text is treated as a modifier.
        for line in unknown:
            if _is_boilerplate(line):
                continue
            modifier_lines.append(line)

    for feature, value in parse_modifier_sentences(modifier_lines).items():
        if features.get(feature, 0) == 0:
            features[feature] = value

    if tier is None:
        tier = _tier_from_any(None, base_type, name)

    return NormalizedMap(
        name=name,
        base_type=base_type,
        map_tier=tier,
        item_level=ilvl,
        quality=quality,
        corrupted=corrupted,
        item_rarity_frame=rarity_frame,
        is_originator=is_originator,
        raw_modifiers=modifier_lines,
        raw_item_data={"text": text, "item_class": item_class},
        **features,
    )


_BOILERPLATE_PREFIXES = (
    "travel to a map",
    "item level:",
    "monster level:",
    "map tier:",
    "quality:",
    "corrupted",
    "unidentified",
    "note:",
    "requires level",
    "area level:",
    "can have up to",
    "map device",
    "atlas region",
)


def _is_boilerplate(line: str) -> bool:
    low = line.strip().lower()
    if low.startswith(_BOILERPLATE_PREFIXES):
        return True
    return low == ORIGINATOR_IMPLICIT_TEXT.lower()


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------
def classify(
    nm: NormalizedMap, map_tier: int = 16, require_uncorrupted: bool = True
) -> NormalizedMap:
    """Decide whether the model should price this map, and say why if not.

    Anything ineligible stays visible in the UI marked Unsupported rather than
    receiving a questionable prediction.
    """
    if not nm.is_originator:
        nm.supported = False
        nm.unsupported_reason = "Not an Originator Map"
    elif nm.map_tier is None:
        nm.supported = False
        nm.unsupported_reason = "Map tier could not be determined"
    elif nm.map_tier != map_tier:
        nm.supported = False
        nm.unsupported_reason = f"Tier {nm.map_tier}, model covers T{map_tier} only"
    elif require_uncorrupted and nm.corrupted:
        nm.supported = False
        nm.unsupported_reason = "Corrupted (model scope is uncorrupted only)"
    elif all(v == 0 for v in nm.feature_vector()):
        nm.supported = False
        nm.unsupported_reason = "No reward values -- unrolled map"
    else:
        nm.supported = True
        nm.unsupported_reason = ""
    return nm
