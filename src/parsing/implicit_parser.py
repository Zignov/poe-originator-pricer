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
# Brackets and a pipe are allowed in the label so that GGG's localisation
# markup survives to `normalise_property_label` below, which is what strips it.
_PROPERTY_LINE = re.compile(
    r"^\s*(?P<label>[A-Za-z'\[\]| ]+?)\s*:\s*(?P<sign>[+-]?)\s*(?P<value>\d+(?:\.\d+)?)\s*%?"
    r"(?:\s*\(augmented\))?\s*$"
)

# GGG wraps a translatable property name as `[InternalId|Display Text]`, and
# sometimes as `[Word]` with no alternative. Covens is where this started
# mattering: the trade API returns `[AtlasDropScaling|Atlas Drops]` and
# `More [Thread|Threads]`, so a label match against the raw string fails for
# both. The displayed half is the one players and community exports use, so
# that is the half kept.
_MARKUP = re.compile(r"\[([^\]|]*)(?:\|([^\]]*))?\]")


def normalise_property_label(label: str) -> str:
    """`More [Thread|Threads]` -> `More Threads`. Plain labels pass through."""
    if not label:
        return ""
    resolved = _MARKUP.sub(lambda m: (m.group(2) or m.group(1) or "").strip(), str(label))
    # The substitution can leave a double space where markup sat between words.
    return re.sub(r"\s+", " ", resolved).strip()

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
    # Covens. `atlas drops` is `item quantity` renamed -- same slot, new name
    # and a new type id; `more threads` is `more scarabs` renamed, and GGG kept
    # the type id. See the table in src/schema.py.
    "atlas drops": "quantity",
    "atlasdropscaling": "quantity",
    "more threads": "more_scarabs",
    "threads": "more_scarabs",
}

# What a property has to look like before an unrecognised one is worth
# reporting. A map carries plenty of properties this app has no interest in --
# `Quality (Currency)`, `Map Tier`, `Monster Level` -- and warning about those
# would train the user to ignore the warning that matters.
#
# A reward is a percentage bonus whose name is phrased as more-of-something, so
# that is the test: a percent value, and a name that opens with one of these.
_REWARD_SHAPED = re.compile(r"^\s*(more|item|monster|atlas|increased)\b", re.I)
_NOT_A_REWARD = re.compile(r"^\s*(quality|monster level|map tier|item level)\b", re.I)


def looks_like_a_reward(label: str, value: str = "%") -> bool:
    """Is this an unrecognised property that the pricer probably should know?

    The whole point of the Covens rename was that it cost nothing to notice and
    a great deal to miss: `Atlas Drops` was simply dropped on the floor, every
    map read as quantity 0, and the only visible symptom was prices drifting
    low on exactly the maps worth the most. Anything shaped like a reward is
    therefore reported rather than silently skipped.
    """
    name = normalise_property_label(label)
    if not name or _NOT_A_REWARD.match(name):
        return False
    if feature_for_label(name) is not None:
        return False  # recognised; nothing to report
    return bool(_REWARD_SHAPED.match(name)) and "%" in str(value)


def empty_features() -> dict[str, int]:
    """Absence of a reward genuinely means no bonus, so absent normalizes to 0."""
    return {name: 0 for name in FEATURE_NAMES}


def feature_for_label(label: str) -> str | None:
    """The feature a property name maps to, markup and league aliases included."""
    resolved = normalise_property_label(label)
    key = resolved.lower()
    if key in _LABEL_ALIASES:
        return _LABEL_ALIASES[key]
    return PROPERTY_TO_FEATURE.get(resolved)


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


def parse_property_lines(
    lines: Iterable[str], unknown_rewards: list[str] | None = None
) -> tuple[dict[str, int], list[str]]:
    """Return (features, unrecognised_lines) for a block of property lines.

    `unknown_rewards`, when given, additionally collects the unrecognised lines
    that *look like rewards* -- see `looks_like_a_reward`. The second return
    value is every unrecognised line, rewards and map modifiers alike, and the
    caller still treats those as modifier text; this is the narrower list that
    is worth putting in front of a user.
    """
    features = empty_features()
    unknown: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parsed = parse_property_line(line)
        if parsed:
            features[parsed[0]] = parsed[1]
            continue
        unknown.append(line)
        if unknown_rewards is not None:
            m = _PROPERTY_LINE.match(line)
            if m and looks_like_a_reward(m.group("label"), line):
                unknown_rewards.append(normalise_property_label(m.group("label")))
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


def parse_api_properties(
    properties: list[dict[str, Any]] | None,
    unknown_rewards: list[str] | None = None,
) -> dict[str, int]:
    """Extract features from the trade/stash API `properties` array.

    Matches on the property name first and falls back to the numeric `type` id,
    so a renamed-but-same-id property still parses. Covens proved that fallback
    is necessary but not sufficient: `More Scarabs` became `More Threads` and
    kept type 89, so the id saved it -- while `Item Quantity` became
    `Atlas Drops` *and* moved from type 2 to type 112, so nothing saved it. A
    property that neither match recognises is now collected into
    `unknown_rewards` rather than silently skipped, which is the only way the
    next rename gets noticed before it has cost a month of prices.
    """
    features = empty_features()
    for prop in properties or []:
        name = prop.get("name", "")
        feature = feature_for_label(name)
        if feature is None:
            feature = PROPERTY_TYPE_ID_TO_FEATURE.get(prop.get("type"))
        if feature is None:
            values = prop.get("values") or []
            shown = str(values[0][0]) if values and values[0] else ""
            if unknown_rewards is not None and looks_like_a_reward(name, shown):
                unknown_rewards.append(normalise_property_label(name))
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
