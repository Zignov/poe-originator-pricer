"""Originator identification, T16 detection, corruption detection, eligibility."""
from __future__ import annotations

import pytest

from src.parsing.item_parser import ItemParseError, classify, parse_api_item, parse_item_text
from tests.conftest import (
    PLAIN_T16_MAP,
    T6_MAGIC_ORIGINATOR,
    T16_CORRUPTED,
    T16_NO_REWARDS,
    T16_UNCORRUPTED_RARE,
)


def test_identifies_originator_from_item_text():
    item = parse_item_text(T16_UNCORRUPTED_RARE)
    assert item.is_originator is True
    assert item.map_tier == 16
    assert item.corrupted is False
    assert item.item_rarity_frame == "Rare"
    assert item.name == "Terror Solace"
    assert item.item_level == 84


def test_plain_map_is_not_an_originator():
    item = classify(parse_item_text(PLAIN_T16_MAP))
    assert item.is_originator is False
    assert item.supported is False
    assert item.unsupported_reason == "Not an Originator Map"


def test_corruption_is_detected_and_makes_the_map_unsupported():
    item = classify(parse_item_text(T16_CORRUPTED))
    assert item.corrupted is True
    assert item.is_originator is True
    assert item.supported is False
    assert "Corrupted" in item.unsupported_reason


def test_corruption_scope_can_be_relaxed():
    item = classify(parse_item_text(T16_CORRUPTED), require_uncorrupted=False)
    assert item.supported is True


def test_wrong_tier_is_unsupported_but_still_recognised():
    item = classify(parse_item_text(T6_MAGIC_ORIGINATOR))
    assert item.is_originator is True
    assert item.map_tier == 6
    assert item.supported is False
    assert "Tier 6" in item.unsupported_reason


def test_originator_with_no_rewards_is_unsupported():
    item = classify(parse_item_text(T16_NO_REWARDS))
    assert item.is_originator is True
    assert item.map_tier == 16
    assert item.feature_vector() == [0] * 7
    assert item.supported is False
    assert "No reward values" in item.unsupported_reason


def test_eligible_map_is_supported():
    item = classify(parse_item_text(T16_UNCORRUPTED_RARE))
    assert item.supported is True
    assert item.unsupported_reason == ""


def test_api_and_text_parsers_agree(api_item_uncorrupted):
    from_api = parse_api_item(api_item_uncorrupted)
    from_text = parse_item_text(T16_UNCORRUPTED_RARE)
    assert from_api.features() == from_text.features()
    assert from_api.map_tier == from_text.map_tier == 16
    assert from_api.corrupted == from_text.corrupted is False


def test_api_parser_preserves_stash_coordinates(api_item_uncorrupted):
    item = parse_api_item(api_item_uncorrupted)
    assert (item.x, item.y) == (3, 5)
    assert item.width == 1 and item.height == 1
    assert item.stash_item_id == "abc123"
    assert item.position_label() == "D6"


def test_api_parser_falls_back_to_stat_hash_when_text_is_absent(api_item_uncorrupted):
    api_item_uncorrupted["implicitMods"] = []
    item = parse_api_item(api_item_uncorrupted)
    assert item.is_originator is True


def test_raw_data_is_preserved():
    item = parse_item_text(T16_UNCORRUPTED_RARE)
    assert "Area is Influenced" in item.raw_item_data["text"]
    assert any("increased Monster Damage" in m for m in item.raw_modifiers)


def test_empty_text_raises():
    with pytest.raises(ItemParseError):
        parse_item_text("   ")


def test_non_dict_api_item_raises():
    with pytest.raises(ItemParseError):
        parse_api_item("not a dict")  # type: ignore[arg-type]


def test_position_label_wraps_past_z():
    item = parse_item_text(T16_UNCORRUPTED_RARE)
    item.x, item.y = 26, 0
    assert item.position_label() == "AA1"
