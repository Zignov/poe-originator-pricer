"""Extraction of the seven pricing values.

Naming note: the project calls these "implicits" throughout because that is how
players talk about them, but live 3.29 trade data shows they arrive as item
*properties* (Item Quantity, More Currency, ...) sitting above the divider,
alongside a genuine implicit line that only marks the map as Originator-
influenced. Both shapes are handled here. See docs/originator-modifiers.md.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from src.schema import (
    FEATURE_NAMES,
    ORIGINATOR_IMPLICIT_TEXT,
    PROPERTY_TO_FEATURE,
    PROPERTY_TYPE_ID_TO_FEATURE,
)

# "Item Quantity: +110% (augmented)" / "More Currency: 47%" / "Item Rarity: +65"
_PROPERTY_LINE = re.compile(
    r"^\s*(?P<label>[A-Za-z' ]+?)\s*:\s*(?P<sign>[+-]?)\s*(?P<value>\d+(?:\.\d+)?)\s*%?"
    r"(?:\s*\(augmented\))?\s*$"
)

# Some sources render the same information as a modifier sentence rather than a
# property, e.g. "35% increased Maps found in your Maps".
_MOD_SENTENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"([+-]?\d+)%\s+increased\s+Maps\s+found", re.I), "more_maps"),
    (re.compile(r"([+-]?\d+)%\s+increased\s+Scarabs\s+found", re.I), "more_scarabs"),
    (re.compile(r"([+-]?\d+)%\s+increased\s+Currency\s+found", re.I), "more_currency"),
    (re.compile(r"([+-]?\d+)%\s+increased\s+Divination\s+Cards?\s+found", re.I), "more_divination_cards"),
    (re.compile(r"([+-]?\d+)%\s+increased\s+Quantity\s+of\s+Items", re.I), "quantity"),
    (re.compile(r"([+-]?\d+)%\s+increased\s+Rarity\s+of\s+Items", re.I), "rarity"),
    (re.compile(r"([+-]?\d+)%\s+increased\s+Pack\s+Size", re.I), "pack_size"),
]

# Label aliases seen in the wild / in community exports.
_LABEL_ALIASES: dict[str, str] = {
    "monster pack size": "pack_size",
    "pack size": "pack_size",
    "item quantity": "quantity",
    "quantity": "quantity",
    "item rarity": "rarity",
    "rarity": "rarity",
    "more currency": "more_currency",
    "more maps": "more_maps",
    "more scarabs": "more_scarabs",
    "more divination cards": "more_divination_cards",
    "more div cards": "more_divination_cards",
}


def empty_features() -> dict[str, int]:
    """Absence of a reward genuinely means no bonus, so absent normalizes to 0."""
    return {name: 0 for name in FEATURE_NAMES}


def feature_for_label(label: str) -> str | None:
    key = label.strip().lower()
    if key in _LABEL_ALIASES:
        return _LABEL_ALIASES[key]
    return PROPERTY_TO_FEATURE.get(label.strip())


def parse_property_line(line: str) -> tuple[str, int] | None:
    """Parse one 'Label: +NN%' line into (feature_name, value), or None."""
    m = _PROPERTY_LINE.match(line)
    if not m:
        return None
    feature = feature_for_label(m.group("label"))
    if feature is None:
        return None
    value = float(m.group("value"))
    if m.group("sign") == "-":
        value = -value
    return feature, int(round(value))


def parse_property_lines(lines: Iterable[str]) -> tuple[dict[str, int], list[str]]:
    """Return (features, unrecognised_lines) for a block of property lines."""
    features = empty_features()
    unknown: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parsed = parse_property_line(line)
        if parsed:
            features[parsed[0]] = parsed[1]
        else:
            unknown.append(line)
    return features, unknown


def parse_modifier_sentences(lines: Iterable[str]) -> dict[str, int]:
    """Pick up reward values expressed as modifier sentences rather than properties."""
    found: dict[str, int] = {}
    for line in lines:
        for pattern, feature in _MOD_SENTENCE_PATTERNS:
            m = pattern.search(line)
            if m:
                found[feature] = found.get(feature, 0) + int(m.group(1))
    return found


def parse_api_properties(properties: list[dict[str, Any]] | None) -> dict[str, int]:
    """Extract features from the trade/stash API `properties` array.

    Matches on the property name first and falls back to the numeric `type` id,
    so a renamed-but-same-id property still parses.
    """
    features = empty_features()
    for prop in properties or []:
        name = prop.get("name", "")
        feature = feature_for_label(name)
        if feature is None:
            feature = PROPERTY_TYPE_ID_TO_FEATURE.get(prop.get("type"))
        if feature is None:
            continue
        values = prop.get("values") or []
        if not values or not values[0]:
            continue
        raw = str(values[0][0])
        m = re.search(r"([+-]?\d+(?:\.\d+)?)", raw)
        if m:
            features[feature] = int(round(float(m.group(1))))
    return features


def has_originator_implicit(lines: Iterable[str]) -> bool:
    target = ORIGINATOR_IMPLICIT_TEXT.lower().replace("'", "'")
    for line in lines:
        norm = line.strip().lower().replace("\u2019", "'")
        if norm == target or norm == ORIGINATOR_IMPLICIT_TEXT.lower():
            return True
    return False
