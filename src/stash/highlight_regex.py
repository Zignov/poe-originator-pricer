"""Turning a price threshold into a stash-search pattern you can paste in game.

The game cannot see what this app thinks a map is worth, so "highlight every map
over 100 chaos" has to be expressed as something the stash search *can* match:
the text printed on the items themselves. Rare maps carry a random two-word
name -- "Glyph Solace", "Warped Frontier" -- so the pattern is built out of
whatever fragments of those names pick out the maps above the threshold and
nothing else in the tab.

What the game's search actually does, which shapes everything here:

  * regex, added in 3.14, matched against **each line of the item text**,
    case-insensitively -- equivalent to the `m` and `i` flags;
  * a **250 character limit** on the search box in PoE 1 (PoE 2 is still on 50).

250 characters does not go far when a tab holds sixty maps, so the pattern is
not simply the selected names joined together. Each map is reduced to the
shortest fragment of its name that appears on no *other* map in the tab, and
fragments that happen to cover several selected maps at once are preferred --
"Solace" is shared by "Glyph Solace" and "Mystic Solace", which is a liability
when only one of them is over the threshold and a saving when both are.

The exclusion corpus is the full item text of every map *below* the threshold,
not just their names. That is deliberate: every map in the tab prints
"Map (Tier 16)" and "Item Quantity", so a fragment drawn from a name that
happened to also sit inside a shared line would highlight the whole tab. Testing
fragments against the whole text of the maps being excluded rules that out
without having to special-case any particular line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Collection, Iterable, Sequence

#: PoE 1's stash search box. PoE 2 still has the older 50-character box, so this
#: is deliberately named for the game it is true of rather than left as a
#: nameless number.
POE1_SEARCH_LIMIT = 250

#: Fragments shorter than this are refused even when they are technically
#: unambiguous. A tab holds items this app never parsed -- currency, scarabs,
#: whatever else got dropped in -- and they are invisible to the exclusion
#: corpus. A two-letter fragment is far too likely to appear somewhere on one of
#: them; three letters is short enough to stay inside the budget and specific
#: enough to be about the map.
MIN_FRAGMENT = 3

#: The floor for a fragment that is **not** anchored to the start or end of a
#: line. Three characters is fine for `^cos`, which can only match a line that
#: begins that way, and reckless for a bare `cos`, which matches those three
#: letters anywhere in the item.
#:
#: This is the lesson of every over-matching pattern this generator has
#: produced. `hao` from "Chaos Bearings" matched the `~price 5 chaos` note.
#: `cit` from "Cosmic Incitement" matched "impli(cit)". `dev` matched "Map
#: Device", `lev` matched "Item Level". Each one was three letters, unanchored,
#: and a perfectly ordinary English fragment that happened to sit inside a line
#: printed on every map in the tab.
#:
#: `UNPARSED_ITEM_TEXT` below rules out the lines we know about. This rules out
#: the ones we do not -- the currency, scarabs and fragments sharing the tab,
#: whose text this app never sees at all. On real tabs it costs no coverage and
#: about nine characters of the 250, which is the cheapest insurance here.
MIN_UNANCHORED_FRAGMENT = 4

#: The text the stash search reads off a map that this app never parsed.
#:
#: The game matches each line of the **Ctrl+C item text**, and that text is a
#: much bigger thing than what the parser keeps. The parser keeps the name, the
#: base type and the modifier sentences; the item also prints its item class,
#: its rarity, a property block, an item level, the stock description, the
#: seller's price note, and -- the one that does the most damage -- a
#: parenthetical tag on the end of half those lines.
#:
#: `(implicit)` is the worst of them. A map called "Cosmic Incitement" offers
#: `cit` as a three-character fragment, no map in the tab has "cit" anywhere the
#: parser can see, and every single map in the game prints "(implicit)" on its
#: implicit line. The pattern reports itself as exact and highlights the entire
#: tab. `(augmented)` does the same to any name containing `ent`, `men` or
#: `ment` -- "Incitement", "Torment", "Judgement" -- and those are common in the
#: rare name pool.
#:
#: So this is written as the item text itself rather than as a list of words,
#: with the tags and punctuation in place, because the fragments are checked
#: per line and anchored fragments need real lines to be checked against. It is
#: merged into the corpus on every build, whatever the caller passed.
#:
#: It costs coverage, not correctness: a name that loses a fragment here is
#: reached through another one, and a name that loses all of them is reported as
#: unmatchable instead of being silently wrong.
UNPARSED_ITEM_TEXT = """\
Item Class: Maps
Item Class: Stackable Currency
Rarity: Normal
Rarity: Magic
Rarity: Rare
Rarity: Unique
Rarity: Currency
--------
Map Tier: 16
Atlas Region: Valdo's Rest
Item Quantity: +100% (augmented)
Item Rarity: +100% (augmented)
Monster Pack Size: +100% (augmented)
Quality: +20% (augmented)
More Currency: +100% (augmented)
More Maps: +100% (augmented)
More Scarabs: +100% (augmented)
More Divination Cards: +100% (augmented)
Area Level: 83
Monster Level: 83
Stack Size: 1/10
--------
Item Level: 100
Talisman Tier: 1
--------
Area is Influenced by the Originator's Memories (implicit)
Superior (implicit)
(implicit)
(augmented)
(crafted)
(enchant)
(fractured)
(scourge)
(veiled)
--------
Travel to a Map of this tier or lower by using this in a personal Map Device.
Maps can only be used once.
Travel to this Map by using it in a personal Map Device. Maps can only be used once.
Right click to use. Shift click to unstack.
Can be used in a personal Map Device.
--------
Note: ~price 1 chaos
Note: ~b/o 1 divine
Note: ~price 1 divine
Note: ~b/o 1 exalted
Note: ~price 1 exalt
Note: ~gb/o 1 mirror
price
buyout
chaos
divine
exalted
exalt
mirror
annulment
annul
regal
alchemy
alteration
augmentation
transmutation
scouring
chisel
chromatic
fusing
jeweller
blessed
vaal
gemcutter
ancient
harbinger
engineer
chance
binding
horizon
orb
shard
--------
Corrupted
Unidentified
Mirrored
Split
Fractured Item
Synthesised Item
Shaper Item
Elder Item
Searing Exarch Item
Eater of Worlds Item
Blighted
Delirious
"""


@dataclass(frozen=True)
class HighlightRegex:
    """A pattern, and an honest account of what it will and will not catch."""

    pattern: str = ""
    #: Names the pattern is expected to highlight.
    covered: tuple[str, ...] = ()
    #: Asked for but not representable -- see `ambiguous` and `dropped`.
    ambiguous: tuple[str, ...] = ()
    #: Reachable, but the character budget ran out before they were covered.
    dropped: tuple[str, ...] = ()
    #: No fragment of the name survives the exclusion corpus at all.
    unmatchable: tuple[str, ...] = ()
    selected: int = 0
    limit: int = POE1_SEARCH_LIMIT

    @property
    def ok(self) -> bool:
        return bool(self.pattern)

    @property
    def exact(self) -> bool:
        """True when the pattern highlights every map asked for and no other."""
        return self.ok and not (self.ambiguous or self.dropped or self.unmatchable)

    @property
    def length(self) -> int:
        return len(self.pattern)

    def explain(self) -> str:
        if not self.selected:
            return "No maps are above that price, so there is nothing to highlight."

        parts: list[str] = []
        if self.pattern:
            parts.append(
                f"Highlights {len(self.covered)} of {self.selected} maps "
                f"({self.length}/{self.limit} characters)."
            )
        elif not self.ambiguous:
            parts.append(
                "No pattern can single out those maps: every fragment of their "
                "names also appears on a map below the threshold."
            )

        # Said even when there is no pattern at all: a duplicate name is the
        # reason, and reporting only "no pattern" hides the one fact that tells
        # the user why and which maps to go and look at by hand.
        if self.ambiguous:
            parts.append(
                f"{len(self.ambiguous)} of them share a name with a map below the "
                f"threshold ({', '.join(self.ambiguous)}), so no search text can "
                "tell them apart -- find those by position instead."
            )
        if self.unmatchable:
            parts.append(
                f"{len(self.unmatchable)} could not be separated from the maps "
                "below the threshold -- every part of their names is shared "
                f"({', '.join(self.unmatchable)}). Find those by position."
            )
        if self.dropped:
            parts.append(
                f"{len(self.dropped)} did not fit in the {self.limit}-character "
                "box. Raise the price to fit the rest in."
            )
        return " ".join(parts)


@dataclass
class _Candidate:
    fragment: str
    kind: str = "plain"
    covers: frozenset[int] = field(default_factory=frozenset)

    @property
    def rendered(self) -> str:
        return _render(self.fragment, self.kind)


def _normalise(text: str) -> str:
    """A name, lower-cased and single-spaced. The game's search ignores case."""
    return " ".join((text or "").lower().split())


def _normalise_text(text: str) -> str:
    """Item text, lower-cased **with its line breaks kept**.

    Collapsing the whole blob to one line would quietly break the exclusion
    check for anchored fragments: `^cro` asks whether any *line* starts with
    "cro", and against a one-line corpus only the very first character of the
    item text could ever answer yes. Every anchored fragment would look safe,
    and the pattern would light up maps below the threshold.
    """
    return "\n".join(" ".join(line.lower().split()) for line in (text or "").splitlines())


#: How a fragment is matched against a line. `^` and `$` cost one character and
#: buy a lot: the map's name sits on its own line, so anchoring turns "contains
#: sol" into "the name starts with sol", which separates names that share a word
#: no plain substring can.
PLAIN, STARTS, ENDS, CROSS = "plain", "starts", "ends", "cross"


def _fragments(name: str) -> list[tuple[str, str]]:
    """Candidate `(fragment, kind)` pairs drawn from one name.

    Whole words are not enough on their own: "Glyph" and "Mystic" differ in
    their first two letters, and a fragment allowed to start mid-word says so in
    three characters where the full word takes five.

    Fragments never span a space, and that is a correctness rule rather than a
    tidiness one. **A space in the game's search box separates search terms** --
    it means "and", not a literal space -- so a fragment like `ic sol` would be
    read as two independent requirements, matching any map with `ic` somewhere
    and `sol` somewhere else. That is a different and much wider search than the
    one intended, and it would not announce itself: the pattern would simply
    light up maps nobody asked about.
    """
    words = name.split()
    if not words:
        return []
    out: list[tuple[str, str]] = []
    for word in words:
        for size in range(MIN_UNANCHORED_FRAGMENT, len(word) + 1):
            for start in range(0, len(word) - size + 1):
                out.append((word[start : start + size], PLAIN))
    for size in range(MIN_FRAGMENT, len(words[0]) + 1):
        out.append((words[0][:size], STARTS))
    for size in range(MIN_FRAGMENT, len(words[-1]) + 1):
        out.append((words[-1][-size:], ENDS))
    return out


#: Longest fragment taken from each word when pairing two words together. Pairs
#: are quadratic, so this is what keeps the search bounded; six characters is
#: already more than enough to separate one name from a tab of sixty.
MAX_CROSS_FRAGMENT = 6


def _cross_fragments(name: str) -> list[tuple[str, str]]:
    """`(a.*b, CROSS)` pairs joining a piece of one word to a piece of a later one.

    For the names where neither word is usable on its own. "Hypnotic Frontier"
    shares "Hypnotic" with one map below the threshold and "Frontier" with
    another, so no single-word fragment can reach it -- but `hyp.*fro` can,
    because it asks for both on one line.

    `.` never matches a newline, so `a.*b` cannot span from the name line onto a
    modifier line. That is what makes this safe to use without an anchor, and it
    is why this is expressible without a space -- a space would mean "and"
    across the whole item rather than "then" within one line.
    """
    words = name.split()
    if len(words) < 2:
        return []
    out: list[tuple[str, str]] = []
    for first in range(len(words) - 1):
        for second in range(first + 1, len(words)):
            for left in _pieces(words[first]):
                for right in _pieces(words[second]):
                    out.append((f"{_escape(left)}.*{_escape(right)}", CROSS))
    return out


def _pieces(word: str) -> list[str]:
    top = min(len(word), MAX_CROSS_FRAGMENT)
    return [
        word[start : start + size]
        for size in range(MIN_FRAGMENT, top + 1)
        for start in range(0, len(word) - size + 1)
    ]


def _appears_in(
    fragment: str, kind: str, blob: str, lines: Collection[str]
) -> bool:
    """Whether a fragment would match the corpus the way the game matches it.

    Line by line, because the game's search applies per line -- which is exactly
    what makes anchoring worth the character. `lines` is the blob already split,
    passed in so it is built once rather than per fragment.
    """
    if kind == PLAIN:
        return fragment in blob
    if kind == CROSS:
        # Already a rendered regex; `.` stops at a newline, so the search is
        # line-bounded without splitting.
        return re.search(fragment, blob) is not None
    if kind == STARTS:
        return any(line.startswith(fragment) for line in lines)
    return any(line.endswith(fragment) for line in lines)


def _render(fragment: str, kind: str) -> str:
    if kind == CROSS:
        return fragment  # already rendered, and must not be escaped again
    escaped = _escape(fragment)
    if kind == STARTS:
        return f"^{escaped}"
    if kind == ENDS:
        return f"{escaped}$"
    return escaped


def build_highlight_regex(
    selected_names: Sequence[str],
    excluded_text: Iterable[str],
    limit: int = POE1_SEARCH_LIMIT,
) -> HighlightRegex:
    """A stash-search pattern matching `selected_names` and nothing excluded.

    `excluded_text` is the full item text of everything that must stay dark --
    pass the whole text of each map below the threshold rather than just its
    name, so that lines shared by every map cannot leak into a fragment.
    """
    wanted = [n for n in (s.strip() for s in selected_names) if n]
    if not wanted:
        return HighlightRegex(selected=0, limit=limit)

    corpus = [_normalise_text(t) for t in excluded_text if t and t.strip()]
    lowered = [_normalise(n) for n in wanted]

    # Fragments are checked against the parsed maps *and* the text the parser
    # never sees; the duplicate-name check below is not, because a name that
    # collides with boilerplate is not "shared with a map below the threshold"
    # and saying so would send the user hunting for a map that is not there.
    # It falls through to `unmatchable`, which says the true thing.
    safety_corpus = corpus + [_normalise_text(UNPARSED_ITEM_TEXT)]

    # A map whose name is printed on an excluded map cannot be singled out by
    # any text at all. Report it rather than emitting a pattern that quietly
    # highlights both.
    ambiguous = [
        original
        for original, low in zip(wanted, lowered)
        if any(low in text for text in corpus)
    ]
    targets = [
        (original, low)
        for original, low in zip(wanted, lowered)
        if original not in ambiguous
    ]
    if not targets:
        return HighlightRegex(
            ambiguous=tuple(ambiguous), selected=len(wanted), limit=limit
        )

    candidates = _safe_candidates(targets, safety_corpus)
    reachable = set().union(*(c.covers for c in candidates)) if candidates else set()

    # Cross-word pairs are quadratic, so they are only generated for the maps
    # that single-word fragments could not reach at all -- which in a real tab
    # is a handful out of sixty.
    stranded = [i for i in range(len(targets)) if i not in reachable]
    if stranded:
        extra = _safe_candidates(targets, safety_corpus, only=stranded, cross=True)
        candidates = list(candidates) + extra
        if extra:
            reachable |= set().union(*(c.covers for c in extra))

    # A map no safe fragment reaches is not the same failure as one the budget
    # could not afford, and saying "raise the price to fit the rest in" about it
    # is advice that cannot work. Separate them before the cover runs.
    unmatchable = [name for i, (name, _) in enumerate(targets) if i not in reachable]

    chosen, covered_indices = _cover(candidates, len(targets), limit)
    covered = [targets[i][0] for i in sorted(covered_indices)]
    dropped = [
        name
        for i, (name, _) in enumerate(targets)
        if i in reachable and i not in covered_indices
    ]
    return HighlightRegex(
        pattern="|".join(chosen),
        covered=tuple(covered),
        ambiguous=tuple(ambiguous),
        dropped=tuple(dropped),
        unmatchable=tuple(unmatchable),
        selected=len(wanted),
        limit=limit,
    )


def _safe_candidates(
    targets: Sequence[tuple[str, str]],
    corpus: Sequence[str],
    only: Sequence[int] | None = None,
    cross: bool = False,
) -> list[_Candidate]:
    """Fragments that appear on at least one target and on nothing excluded."""
    generate = _cross_fragments if cross else _fragments
    indices = range(len(targets)) if only is None else only
    coverage: dict[tuple[str, str], set[int]] = {}
    for index in indices:
        for key in generate(targets[index][1]):
            coverage.setdefault(key, set()).add(index)

    # The corpus is searched once as a single blob rather than item by item.
    # Fragments never contain a newline and `.` never crosses one, so nothing
    # can match across the join -- and it turns ~150 substring searches per
    # fragment into one, which is most of the cost on a multi-tab scan.
    blob = "\n".join(corpus)
    lines = frozenset(blob.splitlines())

    safe: list[_Candidate] = []
    for (fragment, kind), covers in coverage.items():
        if _appears_in(fragment, kind, blob, lines):
            continue
        safe.append(
            _Candidate(fragment=fragment, kind=kind, covers=frozenset(covers))
        )
    return safe


def _cover(
    candidates: Sequence[_Candidate], target_count: int, limit: int
) -> tuple[list[str], set[int]]:
    """Greedy set cover, spending characters where they buy the most maps.

    Exact minimum-length cover is NP-hard and the greedy choice is the standard
    answer; what matters more here is the *tie-break*. Ranking purely by how
    many maps a fragment covers spends 8 characters to catch two maps where two
    fragments of 3 would catch three, so the score is maps-per-character.
    """
    remaining = set(range(target_count))
    chosen: list[str] = []
    covered: set[int] = set()
    budget = limit

    while remaining:
        best: _Candidate | None = None
        best_score = 0.0
        for candidate in candidates:
            gain = len(candidate.covers & remaining)
            if not gain:
                continue
            cost = len(candidate.rendered) + (1 if chosen else 0)
            if cost > budget:
                continue
            score = gain / cost
            if score > best_score or (
                score == best_score and best is not None and cost < len(best.rendered)
            ):
                best, best_score = candidate, score
        if best is None:
            break
        rendered = best.rendered
        budget -= len(rendered) + (1 if chosen else 0)
        chosen.append(rendered)
        newly = best.covers & remaining
        covered |= newly
        remaining -= newly

    return chosen, covered


def _escape(fragment: str) -> str:
    """Escape regex metacharacters, so a name is matched as written.

    Map names are ordinary words today, but nothing guarantees that, and an
    unescaped character here would not fail loudly -- it would silently change
    which maps light up.
    """
    return re.escape(fragment)
