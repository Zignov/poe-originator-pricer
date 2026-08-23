"""Affix structure of a map: how many, which slots, and is there room for more.

Counting modifier *lines* does not work. A single affix can print two lines --
"of Endurance" grants both a maximum-charge line and a gain-on-hit line -- so a
full 6-affix map can show seven lines and look exaltable when it is not.

The API payloads carry the real structure: every entry in `explicitMods` has a
`mods` list naming the affix that produced it and its tier, where the tier is
`P<n>` for a prefix and `S<n>` for a suffix. Distinct `(name, tier)` pairs are
distinct affixes. Validated against all 1838 stored listings that carry the
block: zero produced an impossible split, and every modifier resolved to a slot.

Corruption raises the ceiling. An uncorrupted rare holds three prefixes and
three suffixes; a Vaal Orb can push it to four of each. That is not a curiosity
to tolerate -- corrupted 8-mod Originator Maps are the high-value end of this
market, so applying the uncorrupted limit to them would blank the affix count on
exactly the maps worth the most.

Clipboard text has none of this, which is why `known` exists. An unknown result
means "this source cannot tell us", never "zero affixes" -- the caller shows
nothing rather than a confident wrong answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A rare item carries at most three of each while it is uncorrupted.
MAX_PREFIXES = 3
MAX_SUFFIXES = 3
MAX_AFFIXES = MAX_PREFIXES + MAX_SUFFIXES

#: Corruption raises the ceiling: a Vaal Orb can push a rare map to four of each.
#: This is not an edge case to tolerate -- corrupted 8-mod Originator Maps are the
#: high-value end of this market, so applying the uncorrupted limit to them would
#: blank the affix count on exactly the maps worth the most.
MAX_PREFIXES_CORRUPTED = 4
MAX_SUFFIXES_CORRUPTED = 4
MAX_AFFIXES_CORRUPTED = MAX_PREFIXES_CORRUPTED + MAX_SUFFIXES_CORRUPTED

PREFIX = "prefix"
SUFFIX = "suffix"


@dataclass(frozen=True)
class Affix:
    """One rolled modifier, identified the way the catalogue keys them."""

    name: str
    tier: str
    slot: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.tier)


@dataclass
class AffixSet:
    """What a source could tell us about a map's affixes.

    `known` is the honesty flag: False means the payload lacked the structured
    block, or produced a split the game's rules forbid. In both cases `count` is
    None and no exalt judgement should be offered.
    """

    affixes: tuple[Affix, ...] = ()
    known: bool = False
    corrupted: bool = False
    reason: str = ""

    @property
    def count(self) -> int | None:
        return len(self.affixes) if self.known else None

    @property
    def prefixes(self) -> tuple[Affix, ...]:
        return tuple(a for a in self.affixes if a.slot == PREFIX)

    @property
    def suffixes(self) -> tuple[Affix, ...]:
        return tuple(a for a in self.affixes if a.slot == SUFFIX)

    @property
    def prefix_count(self) -> int:
        return len(self.prefixes)

    @property
    def suffix_count(self) -> int:
        return len(self.suffixes)

    @property
    def max_prefixes(self) -> int:
        return MAX_PREFIXES_CORRUPTED if self.corrupted else MAX_PREFIXES

    @property
    def max_suffixes(self) -> int:
        return MAX_SUFFIXES_CORRUPTED if self.corrupted else MAX_SUFFIXES

    @property
    def max_affixes(self) -> int:
        return self.max_prefixes + self.max_suffixes

    @property
    def open_prefixes(self) -> int:
        return max(0, self.max_prefixes - self.prefix_count) if self.known else 0

    @property
    def open_suffixes(self) -> int:
        return max(0, self.max_suffixes - self.suffix_count) if self.known else 0

    @property
    def open_slots(self) -> int:
        return self.open_prefixes + self.open_suffixes

    @property
    def is_full(self) -> bool:
        return self.known and self.open_slots == 0

    @property
    def can_be_exalted(self) -> bool:
        """An Exalted Orb needs an uncorrupted item with a free slot."""
        return self.known and not self.corrupted and self.open_slots > 0

    @property
    def keys(self) -> set[tuple[str, str]]:
        return {a.key for a in self.affixes}

    def label(self) -> str:
        if not self.known:
            return "affixes unknown"
        return f"{len(self.affixes)}/{self.max_affixes} affixes"


def _slot_for(tier: Any) -> str | None:
    text = str(tier or "").strip().upper()
    if text.startswith("P"):
        return PREFIX
    if text.startswith("S"):
        return SUFFIX
    return None


def extract_affixes(item: Any) -> AffixSet:
    """Read the affix structure out of a trade-API or stash-API item object."""
    if not isinstance(item, dict):
        return AffixSet(reason="item is not an object")

    corrupted = bool(item.get("corrupted", False))
    entries = item.get("explicitMods") or []
    if not entries:
        return AffixSet(corrupted=corrupted, reason="no explicit modifiers on the item")

    found: dict[tuple[str, str], Affix] = {}
    for entry in entries:
        # Plain strings are what clipboard-derived items and some exports carry.
        # They name the effect but not the affix, so the structure is unreadable.
        if not isinstance(entry, dict):
            return AffixSet(
                corrupted=corrupted,
                reason="modifiers arrived as plain text, which carries no affix structure",
            )
        submods = entry.get("mods") or []
        if not submods:
            return AffixSet(
                corrupted=corrupted, reason="modifiers carry no affix names or tiers"
            )
        for sub in submods:
            if not isinstance(sub, dict):
                continue
            tier = sub.get("tier")
            slot = _slot_for(tier)
            if slot is None:
                return AffixSet(
                    corrupted=corrupted,
                    reason=f"modifier tier {tier!r} is neither a prefix nor a suffix",
                )
            name = str(sub.get("name") or "").strip()
            affix = Affix(name=name, tier=str(tier), slot=slot)
            found.setdefault(affix.key, affix)

    affixes = tuple(found.values())
    prefixes = sum(1 for a in affixes if a.slot == PREFIX)
    suffixes = len(affixes) - prefixes
    max_prefixes = MAX_PREFIXES_CORRUPTED if corrupted else MAX_PREFIXES
    max_suffixes = MAX_SUFFIXES_CORRUPTED if corrupted else MAX_SUFFIXES
    if prefixes > max_prefixes or suffixes > max_suffixes:
        return AffixSet(
            corrupted=corrupted,
            reason=(
                f"resolved to {prefixes} prefixes and {suffixes} suffixes, which "
                f"{'a corrupted' if corrupted else 'an uncorrupted'} map cannot have "
                "-- the payload is not shaped as expected"
            ),
        )

    return AffixSet(affixes=affixes, known=True, corrupted=corrupted)
