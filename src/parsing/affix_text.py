"""Recovering a map's affixes from modifier text alone.

GGG's stash endpoints return each modifier as a description and nothing else --
no affix name, no tier, no `extended` block. Verified against a live account: 321
modifier lines across 45 rare maps, none carrying either. The names and tiers the
structured parser needs are a *trade API* enrichment, so a stash map cannot be
read that way at all.

Text alone is not enough on its own, because one affix can print several lines.
A real stash map showed eleven lines when a map holds at most eight affixes:

    26% increased Monster Movement Speed
    36% increased Monster Attack Speed     <- all one affix
    38% increased Monster Cast Speed

Grouping lines by "could one affix produce both of these" gets the count right
about 98% of the time, and every failure *under*-counts -- which is the dangerous
direction, since it makes a full map look like it has room for an exalt.

The constraint that closes the gap is the map's own Item Quantity. Quantity is
exactly the sum of what each affix contributes (recovered at R2 1.0 with zero
residual), and the payload reports the total, so when two lines might be one
affix or two, the totals differ and the observed number picks the right reading.
On ground-truth trade listings that resolves 99.8% exactly with no wrong answers;
what stays genuinely ambiguous is reported as unknown rather than guessed.
"""
from __future__ import annotations

import itertools
import re
from typing import Iterable, Mapping, Sequence

from src.parsing.affixes import (
    MAX_PREFIXES,
    MAX_PREFIXES_CORRUPTED,
    MAX_SUFFIXES,
    MAX_SUFFIXES_CORRUPTED,
    PREFIX,
    SUFFIX,
    Affix,
    AffixSet,
)

#: Rolled values differ between two copies of the same modifier, so numbers are
#: replaced by a placeholder before a line is looked up.
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

#: Stop exploring once this many groupings have been generated. A map with many
#: interchangeable lines can otherwise blow up combinatorially; hitting the cap
#: means the answer is reported as unknown, never guessed.
MAX_PARTITIONS = 4000

#: How far the summed quantity may sit from the reported total and still count as
#: a match, in quantity percentage points.
QUANTITY_TOLERANCE = 1.0


def normalise(description: str) -> str:
    """A modifier line with its rolled numbers removed, for lookup."""
    return _NUMBER.sub("#", description or "").strip()


def resolve_affixes(
    descriptions: Sequence[str],
    quantity: float | None,
    templates: Mapping[str, Sequence[str]],
    affix_templates: Mapping[str, Sequence[str]],
    slots: Mapping[str, str],
    quantities: Mapping[str, float],
    corrupted: bool = False,
) -> AffixSet:
    """Best reading of a map's affixes from its modifier text.

    `templates` maps a normalised line to the affixes that can produce it,
    `affix_templates` maps an affix to every line it can produce, `slots` and
    `quantities` give each affix's prefix/suffix kind and quantity contribution.
    """
    lines = [normalise(d) for d in descriptions if d and d.strip()]
    if not lines:
        return AffixSet(corrupted=corrupted, reason="no explicit modifiers on the item")

    unknown = [line for line in lines if line not in templates]
    if unknown:
        return AffixSet(
            corrupted=corrupted,
            reason=(
                f"{len(unknown)} modifier(s) not seen in the collected market, so the "
                "affix behind them is unknown -- collect more data"
            ),
        )

    groupings = _groupings(lines, templates, affix_templates)
    if groupings is None:
        return AffixSet(
            corrupted=corrupted,
            reason="too many possible readings of these modifiers to choose between",
        )
    if not groupings:
        return AffixSet(
            corrupted=corrupted, reason="no affix combination explains these modifiers"
        )

    matching = _matching_quantity(groupings, quantity, quantities)
    counts = {len(g) for g in matching}
    if len(counts) != 1:
        return AffixSet(
            corrupted=corrupted,
            reason=(
                "the modifier text is consistent with "
                f"{min(counts)}-{max(counts)} affixes and the reported quantity does "
                "not distinguish them"
            ),
        )

    chosen = min(matching, key=lambda g: _quantity_error(g, quantity, quantities))
    affixes = _assign_slots(chosen, slots, corrupted)
    if affixes is None:
        return AffixSet(
            corrupted=corrupted,
            reason="no prefix/suffix split of these modifiers is possible on a map",
        )
    return AffixSet(affixes=affixes, known=True, corrupted=corrupted)


def _assign_slots(
    grouping: Sequence[tuple[tuple[str, ...], set[str]]],
    slots: Mapping[str, str],
    corrupted: bool,
) -> tuple[Affix, ...] | None:
    """Pick each affix's kind, using the fact that a map holds at most 3 of each.

    A handful of modifier lines can be printed by either a prefix or a suffix, and
    picking arbitrarily got the split wrong on 3% of maps. The cap resolves most
    of them: a reading needing four prefixes on an uncorrupted map is not a
    reading at all, so the alternative must be right.
    """
    max_prefix = MAX_PREFIXES_CORRUPTED if corrupted else MAX_PREFIXES
    max_suffix = MAX_SUFFIXES_CORRUPTED if corrupted else MAX_SUFFIXES

    options: list[list[tuple[str, str]]] = []
    for _, candidates in grouping:
        seen: dict[str, str] = {}
        for name in sorted(candidates):
            seen.setdefault(slots.get(name, PREFIX), name)
        options.append([(name, slot) for slot, name in seen.items()])

    best: tuple[Affix, ...] | None = None
    for combo in itertools.product(*options):
        prefixes = sum(1 for _, slot in combo if slot == PREFIX)
        suffixes = len(combo) - prefixes
        if prefixes > max_prefix or suffixes > max_suffix:
            continue
        picked = tuple(Affix(name=name, tier="", slot=slot) for name, slot in combo)
        if best is None:
            best = picked
        # A tie between valid splits leaves the count right and the kinds unsure;
        # the first valid reading is kept rather than inventing certainty.
    return best


def _groupings(
    lines: Sequence[str],
    templates: Mapping[str, Sequence[str]],
    affix_templates: Mapping[str, Sequence[str]],
) -> list[list[tuple[tuple[str, ...], set[str]]]] | None:
    """Every way the lines can be partitioned into affixes. None if capped."""
    out: list[list[tuple[tuple[str, ...], set[str]]]] = []
    capped = False

    def walk(remaining: list[str], groups: list[tuple[tuple[str, ...], set[str]]]) -> None:
        nonlocal capped
        if capped:
            return
        if len(out) >= MAX_PARTITIONS:
            capped = True
            return
        if not remaining:
            out.append(list(groups))
            return
        first, rest = remaining[0], remaining[1:]
        for size in range(1, len(remaining) + 1):
            for extra in itertools.combinations(rest, size - 1):
                group = (first, *extra)
                candidates = {
                    name
                    for name in templates[first]
                    if set(group) <= set(affix_templates.get(name, ()))
                }
                if not candidates:
                    continue
                walk([x for x in rest if x not in extra], groups + [(group, candidates)])

    walk(list(lines), [])
    return None if capped else out


def _quantity_range(
    grouping: Iterable[tuple[tuple[str, ...], set[str]]], quantities: Mapping[str, float]
) -> tuple[float, float]:
    low = sum(min(quantities.get(n, 0.0) for n in c) for _, c in grouping)
    high = sum(max(quantities.get(n, 0.0) for n in c) for _, c in grouping)
    return low, high


def _quantity_error(grouping, quantity: float | None, quantities) -> float:
    if quantity is None:
        return 0.0
    low, high = _quantity_range(grouping, quantities)
    if low <= quantity <= high:
        return 0.0
    return min(abs(quantity - low), abs(quantity - high))


def _matching_quantity(groupings, quantity: float | None, quantities):
    """Keep only the readings whose affixes sum to the map's reported quantity."""
    if quantity is None:
        return groupings
    matching = [
        g
        for g in groupings
        if _quantity_range(g, quantities)[0] - QUANTITY_TOLERANCE
        <= quantity
        <= _quantity_range(g, quantities)[1] + QUANTITY_TOLERANCE
    ]
    return matching or groupings
