"""Reward-value extraction: every known format, missing values, unknown lines."""
from __future__ import annotations

from src.parsing.implicit_parser import (
    empty_features,
    has_originator_implicit,
    parse_api_properties,
    parse_modifier_sentences,
    parse_property_line,
    parse_property_lines,
)
from src.parsing.item_parser import parse_item_text
from src.schema import FEATURE_NAMES
from tests.conftest import T16_UNCORRUPTED_RARE


def test_all_seven_features_start_at_zero():
    features = empty_features()
    assert set(features) == set(FEATURE_NAMES)
    assert all(value == 0 for value in features.values())


def test_the_brief_example_parses_to_the_expected_json():
    text = """Item Class: Maps
Rarity: Rare
Example Map
Map (Tier 16)
--------
Item Quantity: +84%
Item Rarity: +134%
Monster Pack Size: +32%
More Scarabs: +35%
More Currency: +47%
--------
Item Level: 84
--------
Area is Influenced by the Originator's Memories
"""
    assert parse_item_text(text).features() == {
        "quantity": 84,
        "rarity": 134,
        "pack_size": 32,
        "more_currency": 47,
        "more_maps": 0,
        "more_scarabs": 35,
        "more_divination_cards": 0,
    }


def test_seven_features_stay_independent():
    item = parse_item_text(T16_UNCORRUPTED_RARE)
    features = item.features()
    assert len(features) == 7
    assert features["more_maps"] == 35
    assert features["more_currency"] == 47
    assert features["more_scarabs"] == 0


def test_augmented_suffix_is_ignored():
    assert parse_property_line("Item Quantity: +84% (augmented)") == ("quantity", 84)


def test_missing_plus_and_percent_still_parse():
    assert parse_property_line("Item Rarity: 134") == ("rarity", 134)


def test_negative_values_parse():
    assert parse_property_line("Item Quantity: -12%") == ("quantity", -12)


def test_more_divination_cards_label():
    assert parse_property_line("More Divination Cards: +18%") == ("more_divination_cards", 18)


def test_short_alias_labels():
    assert parse_property_line("Pack Size: +32%") == ("pack_size", 32)
    assert parse_property_line("More Div Cards: +9%") == ("more_divination_cards", 9)


def test_unknown_property_lines_are_reported_not_swallowed():
    features, unknown = parse_property_lines(
        ["Item Quantity: +50%", "Chart Sulphur: +3%", "Some Future Reward: +12%"]
    )
    assert features["quantity"] == 50
    assert "Some Future Reward: +12%" in unknown
    assert "Chart Sulphur: +3%" in unknown


def test_modifier_sentences_are_picked_up():
    found = parse_modifier_sentences(
        ["35% increased Maps found in your Maps", "22% increased Scarabs found in your Maps"]
    )
    assert found == {"more_maps": 35, "more_scarabs": 22}


def test_api_properties_match_by_type_id_when_the_name_changes():
    features = parse_api_properties(
        [
            {"name": "Renamed Quantity", "values": [["+77%", 1]], "type": 2},
            {"name": "Renamed Scarabs", "values": [["+40%", 1]], "type": 89},
        ]
    )
    assert features["quantity"] == 77
    assert features["more_scarabs"] == 40


def test_api_properties_tolerate_empty_values():
    features = parse_api_properties([{"name": "Item Quantity", "values": [], "type": 2}])
    assert features["quantity"] == 0


def test_originator_implicit_detection():
    assert has_originator_implicit(["Area is Influenced by the Originator's Memories"])
    assert has_originator_implicit(["Area is influenced by the originator's memories"])
    assert not has_originator_implicit(["Area is Influenced by The Searing Exarch"])


def test_originator_detection_handles_a_typographic_apostrophe():
    assert has_originator_implicit(["Area is Influenced by the Originator’s Memories"])
