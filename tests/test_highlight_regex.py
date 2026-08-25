"""Building a stash-search pattern from a price threshold.

Names here are real ones taken from the collected dataset. That matters: rare
map names are drawn from a shared pool, so they overlap constantly -- "Glyph
Solace" and "Mystic Solace" differ only in the first word, and a pattern built
without checking the rest of the tab highlights both.

The contract every test is really checking: the pattern matches each map the
user asked for, and none of the maps they did not, under the game's own
matching rules -- regex, per line, case-insensitive.
"""
from __future__ import annotations

import re

import pytest

from src.stash.highlight_regex import (
    MIN_FRAGMENT,
    MIN_UNANCHORED_FRAGMENT,
    POE1_SEARCH_LIMIT,
    build_highlight_regex,
)

# Real names, sampled from the collected listings.
NAMES = [
    "Glyph Solace",
    "Mystic Solace",
    "Bleak Lands",
    "Abandoned Realm",
    "Savage Journey",
    "Ghastly Remnant",
    "Rune Path",
    "Entropy Control",
    "Interdimensional Secrets",
    "Miracle Toil",
    "Pandemonium Toil",
    "Warped Frontier",
    "Victory Lands",
    "Voidtouched Bearings",
]

#: Every map in a tab prints these, which is why the exclusion corpus is whole
#: item text rather than names alone.
SHARED_LINES = "Map (Tier 16)\nItem Quantity: +107%\nItem Rarity: +159%"


def _text(name: str) -> str:
    return f"{name}\n{SHARED_LINES}"


def _highlights(pattern: str, name: str) -> bool:
    """Apply the pattern the way the game does: per line, case-insensitive."""
    return bool(re.search(pattern, _text(name), re.IGNORECASE | re.MULTILINE))


def _build(selected, limit=POE1_SEARCH_LIMIT):
    excluded = [_text(n) for n in NAMES if n not in selected]
    return build_highlight_regex(selected, excluded, limit=limit)


# ---------------------------------------------------------------------------
# The core contract
# ---------------------------------------------------------------------------
def test_the_pattern_matches_what_was_asked_for_and_nothing_else():
    selected = ["Bleak Lands", "Rune Path", "Miracle Toil"]
    result = _build(selected)
    assert result.exact
    for name in NAMES:
        assert _highlights(result.pattern, name) == (name in selected), name


def test_two_names_sharing_a_word_are_separated():
    """"Glyph Solace" and "Mystic Solace" -- the shared word is unusable."""
    result = _build(["Glyph Solace"])
    assert result.exact
    assert _highlights(result.pattern, "Glyph Solace")
    assert not _highlights(result.pattern, "Mystic Solace")


def test_a_word_shared_by_both_selected_maps_becomes_the_saving():
    """When both "Solace" maps are wanted, one fragment should serve both."""
    result = _build(["Glyph Solace", "Mystic Solace"])
    assert result.exact
    assert "|" not in result.pattern, "one fragment covers both"
    assert _highlights(result.pattern, "Glyph Solace")
    assert _highlights(result.pattern, "Mystic Solace")


def test_lines_every_map_shares_never_leak_into_the_pattern():
    """A fragment matching "Tier" or "Item Quantity" lights the whole tab."""
    result = _build(["Bleak Lands"])
    for name in NAMES:
        if name != "Bleak Lands":
            assert not _highlights(result.pattern, name), name


def test_selecting_every_map_still_produces_a_working_pattern():
    result = _build(list(NAMES))
    assert result.exact
    assert all(_highlights(result.pattern, n) for n in NAMES)


def test_matching_is_case_insensitive_like_the_game():
    result = _build(["Bleak Lands"])
    assert re.search(result.pattern, "BLEAK LANDS", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Length, which is the whole reason fragments exist
# ---------------------------------------------------------------------------
def test_the_pattern_stays_inside_the_game_search_box():
    result = _build(list(NAMES))
    assert result.length <= POE1_SEARCH_LIMIT


def test_fragments_are_shorter_than_spelling_out_the_names():
    selected = ["Interdimensional Secrets", "Voidtouched Bearings", "Abandoned Realm"]
    result = _build(selected)
    assert result.length < len("|".join(selected))


def test_a_budget_too_small_drops_maps_rather_than_overflowing():
    """Silently emitting 300 characters into a 250-character box is the one
    outcome that must not happen -- it truncates in game and highlights the
    wrong things."""
    result = _build(list(NAMES), limit=12)
    assert result.length <= 12
    assert result.dropped, "something had to give"
    assert not result.exact
    assert "did not fit" in result.explain()


def test_the_maps_that_did_fit_are_still_correct_when_the_budget_binds():
    result = _build(list(NAMES), limit=12)
    for name in result.covered:
        assert _highlights(result.pattern, name), name


def test_no_fragment_is_shorter_than_the_floor():
    """A tab holds items this app never parsed; a two-letter fragment would
    catch them."""
    result = _build(["Bleak Lands"])
    assert all(len(f) >= MIN_FRAGMENT for f in result.pattern.split("|"))


# ---------------------------------------------------------------------------
# Cases where no honest pattern exists
# ---------------------------------------------------------------------------
def test_a_duplicate_name_is_reported_rather_than_half_matched():
    """Two maps in a tab can share a name, one above the threshold and one
    below. No search text distinguishes them, so this must be said out loud
    rather than silently highlighting both."""
    excluded = [_text("Rune Path")]  # the same name, below the threshold
    result = build_highlight_regex(["Rune Path"], excluded)
    assert not result.pattern
    assert result.ambiguous == ("Rune Path",)
    assert "share a name" in result.explain()


def test_one_ambiguous_map_does_not_sink_the_rest():
    excluded = [_text("Rune Path"), _text("Bleak Lands")]
    result = build_highlight_regex(["Rune Path", "Miracle Toil"], excluded)
    assert result.ambiguous == ("Rune Path",)
    assert result.covered == ("Miracle Toil",)
    assert _highlights(result.pattern, "Miracle Toil")
    assert not result.exact


def test_nothing_selected_says_so():
    result = build_highlight_regex([], [_text(n) for n in NAMES])
    assert not result.ok
    assert "nothing to highlight" in result.explain()


@pytest.mark.parametrize("blank", [[""], ["   "], [None]])
def test_blank_names_are_not_treated_as_maps(blank):
    assert not build_highlight_regex([b or "" for b in blank], []).ok


def test_no_maps_to_exclude_still_works():
    result = build_highlight_regex(["Bleak Lands"], [])
    assert result.exact
    assert _highlights(result.pattern, "Bleak Lands")


# ---------------------------------------------------------------------------
# The pattern has to survive being pasted into a regex engine
# ---------------------------------------------------------------------------
def test_the_pattern_always_compiles():
    for size in range(1, len(NAMES) + 1):
        result = _build(NAMES[:size])
        re.compile(result.pattern)  # raises if the fragments were not escaped


def test_regex_metacharacters_in_a_name_are_escaped():
    """Map names are ordinary words today, but an unescaped character would not
    fail loudly -- it would change which maps light up."""
    result = build_highlight_regex(["A+B (test)"], [_text("Bleak Lands")])
    assert re.search(result.pattern, "a+b (test)", re.IGNORECASE)


def test_no_fragment_contains_a_space():
    """A space in the game's search box means "and", not a literal space.

    A fragment like "ic sol" would be read as two independent requirements --
    any map with "ic" somewhere and "sol" somewhere else -- which is a wider
    search than the one asked for, and it would not announce itself.
    """
    for size in (1, 5, len(NAMES)):
        result = _build(NAMES[:size])
        assert " " not in result.pattern, result.pattern
        assert "\ " not in result.pattern, "not even escaped -- see the docstring"


def test_an_anchored_fragment_is_checked_against_lines_not_the_whole_blob():
    """`^cro` means "a line starts with cro".

    If the exclusion corpus were flattened to one line, only the very first
    character of an excluded item could ever answer that -- so every anchored
    fragment would look safe and the pattern would light up maps below the
    threshold. This pins the line-aware check.
    """
    # "Crooked Vault" is excluded, and its name is not the first line of the
    # item text. A blob-flattening check would miss it.
    excluded = ["Map (Tier 16)\nCrooked Vault\nItem Quantity: +100%"]
    result = build_highlight_regex(["Crooked Path"], excluded)
    assert not _matches(result.pattern, "Map (Tier 16)\nCrooked Vault\nItem Quantity: +100%")
    assert _matches(result.pattern, _text("Crooked Path"))


def _matches(pattern: str, text: str) -> bool:
    return bool(pattern) and bool(
        re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    )


def test_random_tabs_never_highlight_the_wrong_map():
    """The real contract, over many shapes rather than one hand-picked tab.

    Anything the pattern claims to cover must light up, and nothing below the
    threshold ever may -- a false highlight is the failure that costs money,
    because it is the one the user acts on without checking.
    """
    import random

    rng = random.Random(11)
    pool = NAMES + [
        "Crooked Vault", "Crooked Path", "Dread Solace", "Bleak Journey",
        "Savage Realm", "Victory Toil", "Warped Lands", "Rune Frontier",
    ]
    for _ in range(300):
        tab = rng.sample(pool, rng.randint(2, len(pool)))
        cut = rng.randint(1, len(tab) - 1)
        selected, excluded = tab[:cut], tab[cut:]
        result = build_highlight_regex(selected, [_text(n) for n in excluded])
        for name in excluded:
            assert not _matches(result.pattern, _text(name)), (
                f"{result.pattern!r} wrongly highlights {name!r}"
            )
        for name in result.covered:
            assert _matches(result.pattern, _text(name)), (
                f"{result.pattern!r} claims {name!r} but misses it"
            )


def test_a_name_whose_words_are_each_shared_is_still_reachable():
    """Neither word is usable alone; the pair is.

    "Hypnotic Frontier" shares "Hypnotic" with one excluded map and "Frontier"
    with another, so every single-word fragment is poisoned. `hyp.*fro` asks for
    both on one line and reaches it.
    """
    excluded = [_text("Hypnotic Vault"), _text("Warped Frontier")]
    result = build_highlight_regex(["Hypnotic Frontier"], excluded)
    assert result.exact, result.explain()
    assert ".*" in result.pattern
    assert _matches(result.pattern, _text("Hypnotic Frontier"))
    for name in ("Hypnotic Vault", "Warped Frontier"):
        assert not _matches(result.pattern, _text(name)), name


def test_a_cross_word_fragment_cannot_reach_onto_a_modifier_line():
    """`.` never matches a newline, which is what keeps `a.*b` inside the name.

    Without that, "hyp.*quantity" would be a legal way to match a map, and it
    would match every map in the tab.
    """
    excluded = [_text("Hypnotic Vault"), _text("Warped Frontier")]
    pattern = build_highlight_regex(["Hypnotic Frontier"], excluded).pattern
    assert not _matches(pattern, "Hypnotic Vault\nItem Quantity: +107%\nfrontier")


# ---------------------------------------------------------------------------
# Text the game searches that this app never parsed
# ---------------------------------------------------------------------------
#: What a listed map actually carries in game beyond anything the parser keeps:
#: the price note the user set, the stock description, and the property lines.
#: None of these reach the exclusion corpus through the parsed modifiers, and
#: all of them are matched by the stash search.
LISTED_EXTRAS = (
    "\nTravel to a Map of this tier or lower by using this in a personal Map "
    "Device. Maps can only be used once.\nItem Level: 84\nRare\n~price 5 chaos"
)


def _listed_text(name: str) -> str:
    return _text(name) + LISTED_EXTRAS


def test_a_fragment_is_never_drawn_from_price_note_vocabulary():
    """The reported bug, at its smallest.

    "Chaos Bearings" yields `cha`, `hao` and `aos`, and every one of them sits
    inside the `~price 5 chaos` note that the seller has put on every map in the
    tab. The parser never sees that note, so nothing in the exclusion corpus
    rules those fragments out -- and the pattern that looks exact here lights up
    the whole tab in game.
    """
    result = build_highlight_regex(["Chaos Bearings"], [_text("Rune Path")])
    assert result.pattern
    assert not _matches(result.pattern, _listed_text("Rune Path")), (
        f"{result.pattern!r} matches a map that is merely priced in chaos"
    )


def test_a_fragment_is_never_drawn_from_the_stock_map_description():
    """"Travel to a Map ... personal Map Device" is printed on every map.

    "Devious Travels" offers `dev`, `tra` and `vel`, all of which are in that
    line. A tab where no *parsed* map happens to contain them would let one
    through.
    """
    result = build_highlight_regex(["Devious Travels"], [_text("Rune Path")])
    if result.pattern:
        assert not _matches(result.pattern, _listed_text("Rune Path")), (
            f"{result.pattern!r} matches the stock map description"
        )


def test_a_fragment_is_never_drawn_from_a_property_or_rarity_line():
    """"Rarity", "Quantity", "Rare", "Item Level" are on every map too."""
    for name in ("Rarity Gambit", "Level Course"):
        result = build_highlight_regex([name], [_text("Rune Path")])
        if result.pattern:
            assert not _matches(result.pattern, _listed_text("Rune Path")), (
                f"{result.pattern!r} (from {name!r}) matches a shared property line"
            )


def test_the_unseen_vocabulary_does_not_make_ordinary_names_unreachable():
    """Blocking that vocabulary must cost coverage, not correctness.

    "Chaos Bearings" loses its first word entirely and is still reachable
    through its second -- which is the whole point of building the pattern out
    of fragments rather than names.
    """
    result = build_highlight_regex(["Chaos Bearings"], [_text("Rune Path")])
    assert result.exact, result.explain()
    assert _matches(result.pattern, _listed_text("Chaos Bearings"))


# ---------------------------------------------------------------------------
# The real haystack: the Ctrl+C item text
# ---------------------------------------------------------------------------
#: What the stash search actually reads, line by line. The parser keeps the
#: name, the base type and the modifier sentences -- everything else here is
#: invisible to it, and the game matches all of it.
#:
#: The parenthetical tags are the dangerous part and the reason this is written
#: out in full rather than summarised: "(implicit)" is printed on every map in
#: the game and it contains "cit".
CTRL_C_SKELETON = """\
Item Class: Maps
Rarity: Rare
{name}
Map (Tier 16)
--------
Map Tier: 16
Item Quantity: +107% (augmented)
Item Rarity: +159% (augmented)
Monster Pack Size: +32% (augmented)
--------
Item Level: 84
--------
Area is Influenced by the Originator's Memories (implicit)
--------
Monsters have 100% increased Poison Duration
Players have 25% less Defences
--------
Travel to a Map of this tier or lower by using this in a personal Map Device. \
Maps can only be used once.
--------
Note: ~price 5 chaos"""


def _ctrl_c(name: str) -> str:
    return CTRL_C_SKELETON.format(name=name)


def _lights_up(pattern: str, name: str) -> bool:
    """Match the way the game does, against the whole item text."""
    return bool(re.search(pattern, _ctrl_c(name), re.IGNORECASE | re.MULTILINE))


def test_a_fragment_is_never_drawn_from_a_parenthetical_tag():
    """The one that survived the first fix.

    "Cosmic Incitement" offers `cit`, nothing the parser can see contains it,
    and every map in the game prints "(implicit)". The pattern called itself
    exact and highlighted the entire tab.
    """
    result = build_highlight_regex(["Cosmic Incitement"], [_text("Rune Path")])
    assert result.pattern
    assert not _lights_up(result.pattern, "Rune Path"), (
        f"{result.pattern!r} matches the (implicit) tag every map carries"
    )


def test_no_fragment_survives_against_a_whole_untouched_item():
    """The general form, over every name in the sample.

    Each name is asked for on its own against one excluded map, and the pattern
    is then matched against a *different* map's full Ctrl+C text. Nothing in
    that text belongs to the selected map, so nothing should match.
    """
    unrelated = "Rune Path"
    for name in NAMES + ["Cosmic Incitement", "Chaos Bearings", "Torment Reach"]:
        if name == unrelated:
            continue
        result = build_highlight_regex([name], [_text(unrelated)])
        if not result.pattern:
            continue
        assert not _lights_up(result.pattern, unrelated), (
            f"{result.pattern!r} (from {name!r}) matches an unrelated item"
        )


def test_an_unanchored_fragment_is_never_only_three_characters():
    """Anchored fragments may be short; bare ones may not.

    `^cos` can only match a line that begins that way. A bare `cos` matches
    those three letters anywhere in the item, and three ordinary letters turn up
    inside ordinary English constantly -- which is how `hao`, `cit`, `dev` and
    `lev` each shipped a pattern that lit up the whole tab.
    """
    for name in NAMES + ["Cosmic Incitement", "Chaos Bearings"]:
        result = build_highlight_regex([name], [_text("Rune Path")])
        if not result.pattern:
            continue
        for alternative in result.pattern.split("|"):
            if alternative.startswith("^") or alternative.endswith("$"):
                continue
            if ".*" in alternative:  # cross-word pairs need both halves on a line
                continue
            assert len(alternative) >= MIN_UNANCHORED_FRAGMENT, (
                f"{alternative!r} from {name!r} is an unanchored "
                f"{len(alternative)}-character fragment"
            )


def test_the_selected_map_still_lights_up_against_its_whole_item_text():
    """The other half of the contract: it must still find what it promised."""
    selected = ["Cosmic Incitement", "Chaos Bearings", "Void Inscription"]
    result = build_highlight_regex(selected, [_text(n) for n in NAMES])
    for name in result.covered:
        assert _lights_up(result.pattern, name), (
            f"{result.pattern!r} promised {name!r} and does not match it"
        )
