"""Reward properties GGG renames between leagues, and noticing when they do.

Covens is the case these are written from, and it is worth stating what it
actually cost, because the shape of the failure is the reason this file exists.

Two of the seven rewards were renamed. `More Scarabs` became `More Threads` and
kept property type id 89, so the numeric fallback caught it and the value kept
arriving -- under the old name, but arriving. `Item Quantity` became
`Atlas Drops` *and* moved from type 2 to type 112, so neither the name match nor
the id fallback caught it, and quantity read 0 on every map in the league.

Nothing raised. Nothing was logged. 200 collected listings all had quantity 0,
and the only visible symptom was prices sagging on exactly the maps worth the
most -- because near the training floor a lost feature barely moves the number,
and on a good map it halves it.

So there are two things to pin: that the current names parse, and that a name
nobody has taught the parser yet is *reported* rather than dropped.
"""
from __future__ import annotations

import pytest

from src.parsing.implicit_parser import (
    feature_for_label,
    looks_like_a_reward,
    normalise_property_label,
    parse_api_properties,
    parse_property_lines,
)
from src.parsing.item_parser import parse_api_item, parse_item_text
from src.schema import (
    FEATURE_LABELS,
    FEATURE_NAMES,
    FEATURE_SHORT_LABELS,
    PROPERTY_TO_FEATURE,
    PROPERTY_TYPE_ID_TO_FEATURE,
)


# -- GGG's localisation markup ----------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[AtlasDropScaling|Atlas Drops]", "Atlas Drops"),
        ("More [Thread|Threads]", "More Threads"),
        ("[Single]", "Single"),
        ("Item Rarity", "Item Rarity"),
        ("", ""),
        # The display half is kept, never the internal id, because the display
        # half is what players and community exports use.
        ("[InternalName|Shown Name]", "Shown Name"),
    ],
)
def test_the_translatable_markup_resolves_to_the_displayed_name(raw, expected):
    assert normalise_property_label(raw) == expected


def test_markup_in_the_middle_of_a_name_does_not_leave_a_double_space():
    assert normalise_property_label("More [Thread|Threads] Found") == "More Threads Found"


# -- the renames themselves -------------------------------------------------
@pytest.mark.parametrize(
    "label,feature",
    [
        # Covens
        ("Atlas Drops", "quantity"),
        ("[AtlasDropScaling|Atlas Drops]", "quantity"),
        ("More Threads", "more_scarabs"),
        ("More [Thread|Threads]", "more_scarabs"),
        # Allflame and earlier, which must keep working -- old exports, old
        # CSVs and the 16k listings already in the database.
        ("Item Quantity", "quantity"),
        ("More Scarabs", "more_scarabs"),
        ("Item Rarity", "rarity"),
        ("Monster Pack Size", "pack_size"),
        ("More Currency", "more_currency"),
        ("More Maps", "more_maps"),
        ("More Divination Cards", "more_divination_cards"),
    ],
)
def test_every_known_name_resolves_to_its_feature(label, feature):
    assert feature_for_label(label) == feature


def test_nothing_is_ever_removed_from_the_alias_table():
    """Both names for a renamed reward map to the same slot, permanently.

    A map from an older league, an older export or a community CSV still has to
    parse. Replacing a name rather than adding to it would silently zero a
    feature on every historical row the moment the league turned over.
    """
    assert PROPERTY_TO_FEATURE["Item Quantity"] == PROPERTY_TO_FEATURE["Atlas Drops"]
    assert PROPERTY_TO_FEATURE["More Scarabs"] == PROPERTY_TO_FEATURE["More Threads"]


def test_both_quantity_type_ids_are_known():
    """The one the fallback missed. 2 was Allflame's, 112 is Covens'."""
    assert PROPERTY_TYPE_ID_TO_FEATURE[2] == "quantity"
    assert PROPERTY_TYPE_ID_TO_FEATURE[112] == "quantity"


def test_the_thread_rename_kept_its_type_id():
    """Which is why that one survived, and is worth recording as the reason."""
    assert PROPERTY_TYPE_ID_TO_FEATURE[89] == "more_scarabs"


# -- the two live shapes ----------------------------------------------------
def test_a_covens_trade_api_item_parses_every_reward():
    """The exact property shape the live API returns, markup included."""
    item = {
        "properties": [
            {"name": "[AtlasDropScaling|Atlas Drops]", "type": 112, "values": [["+52%", 1]]},
            {"name": "Item Rarity", "type": 3, "values": [["+92%", 1]]},
            {"name": "Monster Pack Size", "type": 4, "values": [["+20%", 1]]},
            {"name": "More Currency", "type": 90, "values": [["+47%", 1]]},
            {"name": "More [Thread|Threads]", "type": 89, "values": [["+35%", 1]]},
        ],
        "implicitMods": ["Area is Influenced by the Originator's Memories"],
    }
    parsed = parse_api_item(item)
    assert parsed.quantity == 52
    assert parsed.rarity == 92
    assert parsed.pack_size == 20
    assert parsed.more_currency == 47
    assert parsed.more_scarabs == 35
    assert parsed.is_originator
    assert not parsed.unknown_rewards


def test_a_covens_clipboard_item_parses_every_reward():
    """Text has no type ids, so the name match is the only thing that can work.

    This is the path the hotkey price checker uses, and the one the numeric
    fallback could never have rescued.
    """
    parsed = parse_item_text(
        "Item Class: Maps\n"
        "Rarity: Rare\n"
        "Dusk Voyage\n"
        "Map (Tier 16)\n"
        "--------\n"
        "Atlas Drops: +52%\n"
        "Item Rarity: +92%\n"
        "Monster Pack Size: +20%\n"
        "More Currency: +47%\n"
        "More Threads: +35%\n"
        "--------\n"
        "Area is Influenced by the Originator's Memories"
    )
    assert parsed.quantity == 52
    assert parsed.more_scarabs == 35
    assert not parsed.unknown_rewards


def test_an_allflame_item_still_parses_identically():
    """The regression that would hurt most: 16k stored rows re-read as zeroes."""
    old = parse_api_item({
        "properties": [
            {"name": "Item Quantity", "type": 2, "values": [["+52%", 1]]},
            {"name": "More Scarabs", "type": 89, "values": [["+35%", 1]]},
        ],
    })
    assert old.quantity == 52
    assert old.more_scarabs == 35


def test_an_id_only_match_still_works_when_the_name_is_unknown():
    """The fallback that saved Threads, kept honest.

    A name nobody has seen with an id we do know is a successful parse, not an
    unknown reward -- reporting it would be a false alarm every league.
    """
    unknown: list[str] = []
    features = parse_api_properties(
        [{"name": "More [Whatsit|Whatsits]", "type": 89, "values": [["+40%", 1]]}],
        unknown,
    )
    assert features["more_scarabs"] == 40
    assert unknown == []


# -- noticing the next one --------------------------------------------------
def test_an_unknown_reward_property_is_reported_not_dropped():
    """The whole point. This is what did not happen for Covens."""
    unknown: list[str] = []
    features = parse_api_properties(
        [{"name": "[NewThing|Atlas Shards]", "type": 999, "values": [["+60%", 1]]}],
        unknown,
    )
    assert all(v == 0 for v in features.values()), "it should not have matched anything"
    assert unknown == ["Atlas Shards"], "the new reward was dropped silently"


def test_an_unknown_reward_in_clipboard_text_is_reported_too():
    parsed = parse_item_text(
        "Item Class: Maps\n"
        "Rarity: Rare\n"
        "Map (Tier 16)\n"
        "--------\n"
        "Item Rarity: +92%\n"
        "More Widgets: +30%\n"
    )
    assert "More Widgets" in parsed.unknown_rewards


@pytest.mark.parametrize(
    "name,value",
    [
        ("Quality", "+20%"),
        ("Quality (Currency)", "+14%"),
        ("Monster Level", "83"),
        ("Map Tier", "16"),
        ("Item Level", "84"),
        # No percentage: a count or a level, not a reward bonus.
        ("More Maps Found Somewhere", "3"),
    ],
)
def test_ordinary_properties_do_not_raise_a_false_alarm(name, value):
    """A warning that fires on `Quality (Currency)` is a warning people learn
    to ignore, and then the real one goes unread too."""
    assert not looks_like_a_reward(name, value)


@pytest.mark.parametrize(
    "name", ["Atlas Shards", "More Widgets", "Item Something", "Monster Thingy"]
)
def test_a_reward_shaped_name_does_raise_one(name):
    assert looks_like_a_reward(name, "+30%")


def test_a_recognised_reward_is_never_reported_as_unknown():
    for label in ("Atlas Drops", "More Threads", "Item Quantity", "More Scarabs"):
        assert not looks_like_a_reward(label, "+30%"), f"{label} is known"


def test_property_lines_only_collect_rewards_not_every_unknown_line():
    """Map modifiers are unrecognised *lines* and are not unrecognised rewards.

    The second return value still carries them, because the caller treats them
    as modifier text; only the narrow list is worth putting in front of a user.
    """
    rewards: list[str] = []
    _features, unknown = parse_property_lines(
        [
            "Item Rarity: +92%",
            "More Widgets: +30%",
            "Monsters have 663% increased Critical Strike Chance",
            "Area is Influenced by the Originator's Memories",
        ],
        rewards,
    )
    assert rewards == ["More Widgets"]
    assert len(unknown) == 3, "modifier lines should still be returned as unknown lines"


def test_collecting_without_the_list_is_still_supported():
    """The argument is optional, so every existing caller keeps working."""
    features, unknown = parse_property_lines(["Atlas Drops: +52%", "Nonsense"])
    assert features["quantity"] == 52
    assert unknown == ["Nonsense"]


# -- labels -----------------------------------------------------------------
def test_every_feature_has_a_label_and_a_short_label():
    for name in FEATURE_NAMES:
        assert FEATURE_LABELS.get(name), f"{name} has no display label"
        assert FEATURE_SHORT_LABELS.get(name), f"{name} has no short label"


def test_the_labels_show_the_current_names():
    """The slot keys do not change; what the user reads does."""
    assert FEATURE_LABELS["quantity"] == "Atlas Drops"
    assert FEATURE_LABELS["more_scarabs"] == "More Threads"


def test_the_feature_keys_did_not_change_with_the_rename():
    """`more_scarabs` holds thread counts in Covens, and that is deliberate.

    The key is a slot identifier, not a claim about what the slot contains --
    GGG said the same thing by keeping type id 89 through the rename. Renaming
    it would mean a database migration and a `feature_list` that no trained
    model matches, to gain what the label already gives.
    """
    assert "more_scarabs" in FEATURE_NAMES
    assert "quantity" in FEATURE_NAMES


def test_short_labels_stay_short_enough_for_a_table_column():
    """The CLI table and the overlay popup both lay these out in fixed widths."""
    for name, label in FEATURE_SHORT_LABELS.items():
        assert len(label) <= 5, f"{name}'s short label {label!r} will break the columns"
