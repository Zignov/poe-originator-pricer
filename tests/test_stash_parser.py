"""Stash adapters: coordinates survive, every source yields the same schema."""
from __future__ import annotations

import json

import pytest

from src.stash.base import StashTab, grid_size_for, items_from_api_payload, only_maps
from src.stash.clipboard_import import ClipboardStashSource, parse_clipboard, split_items
from src.stash.json_import import JsonImportError, JsonStashSource, parse_csv
from tests.conftest import T16_CORRUPTED, T16_UNCORRUPTED_RARE


def test_stash_coordinates_are_preserved_through_the_adapter(api_item_uncorrupted):
    tab = StashTab(id="tab-1", name="Originator Maps", index=0)
    items = items_from_api_payload([api_item_uncorrupted], tab)
    assert len(items) == 1
    item = items[0]
    assert (item.x, item.y) == (3, 5)
    assert item.stash_tab_id == "tab-1"
    assert item.stash_tab_name == "Originator Maps"
    assert item.position_label() == "D6"


def test_json_source_accepts_a_legacy_stash_response(api_item_uncorrupted):
    payload = {"tabs": [{"i": 0, "n": "Maps"}], "tabIndex": 0, "items": [api_item_uncorrupted]}
    source = JsonStashSource(payload=payload)
    tabs = source.list_tabs("Allflame")
    assert [t.name for t in tabs] == ["Maps"]
    items = source.fetch_tab("Allflame", tabs[0])
    assert len(items) == 1 and items[0].supported


def test_json_source_accepts_a_bare_item_list(api_item_uncorrupted):
    source = JsonStashSource(payload=[api_item_uncorrupted])
    items = source.fetch_tabs("Allflame", source.list_tabs("Allflame"))
    assert len(items) == 1


def test_json_source_accepts_an_oauth_shaped_response(api_item_uncorrupted):
    payload = {"stash": {"name": "Quad", "items": [api_item_uncorrupted]}}
    source = JsonStashSource(payload=payload)
    assert [t.name for t in source.list_tabs("Allflame")] == ["Quad"]


def test_json_source_rejects_an_unknown_shape():
    with pytest.raises(JsonImportError):
        JsonStashSource(payload={"unexpected": 1})


def test_clipboard_splits_multiple_pasted_items():
    blocks = split_items(T16_UNCORRUPTED_RARE + "\n" + T16_CORRUPTED)
    assert len(blocks) == 2
    assert "Terror Solace" in blocks[0]
    assert "Hollow Crosscut" in blocks[1]


def test_clipboard_assigns_paste_order_positions():
    items = parse_clipboard(T16_UNCORRUPTED_RARE + "\n" + T16_CORRUPTED)
    assert [(i.x, i.y) for i in items] == [(0, 0), (1, 0)]
    assert items[0].stash_tab_name == "Clipboard"


def test_clipboard_source_reports_when_empty():
    ok, why = ClipboardStashSource("").available()
    assert ok is False and why


def test_csv_import_builds_the_same_schema():
    csv_text = (
        "position,tab,quantity,rarity,pack_size,more_currency,more_maps,"
        "more_scarabs,more_divination_cards\n"
        "A1,Maps,84,134,32,47,35,0,0\n"
        "C3,Maps,90,100,30,0,0,42,0\n"
    )
    items = parse_csv(csv_text)
    assert len(items) == 2
    assert items[0].features()["more_currency"] == 47
    assert (items[0].x, items[0].y) == (0, 0)
    assert (items[1].x, items[1].y) == (2, 2)
    assert all(i.supported for i in items)


def test_csv_import_rejects_missing_reward_columns():
    with pytest.raises(JsonImportError):
        parse_csv("quantity,rarity\n84,134\n")


def test_only_maps_filters_out_non_map_items():
    from src.schema import NormalizedMap

    kept = only_maps(
        [
            NormalizedMap(base_type="Map (Tier 16)"),
            NormalizedMap(base_type="Chaos Orb"),
            NormalizedMap(base_type="Vaal Regalia", is_originator=False),
        ]
    )
    assert [k.base_type for k in kept] == ["Map (Tier 16)"]


def test_grid_size_widens_to_quad_when_items_sit_outside_the_default():
    from src.schema import NormalizedMap

    tab = StashTab(id="t", name="t")
    assert grid_size_for(tab, [NormalizedMap(x=2, y=2)]) == (12, 12)
    assert grid_size_for(tab, [NormalizedMap(x=20, y=3)]) == (24, 24)


def test_json_source_reads_a_file(tmp_path, api_item_uncorrupted):
    path = tmp_path / "stash.json"
    path.write_text(json.dumps({"items": [api_item_uncorrupted]}), encoding="utf-8")
    source = JsonStashSource(path=path)
    assert len(source.fetch_tabs("Allflame", source.list_tabs("Allflame"))) == 1
