"""Pricing what is in your backpack, and drawing it in the right half of the screen.

Two failures this file exists to prevent, and they are the same failure twice:
a price drawn over a cell that holds something else.

The first is in the reader. A character's `/get-items` response is not an
inventory -- it is everything the character is carrying, wearing and standing
on, and an equipped helmet reports `x: 0, y: 0` inside its own slot. Let one
through and the overlay puts a map's price on the top-left inventory cell,
confidently and permanently.

The second is in the calibration. The inventory panel is on the other side of
the screen from the stash and is 12x5 rather than 12x12, so the two cannot
share an origin, a cell size, or a grid -- and a calibration that quietly
inherited the stash's numbers would still draw a tidy grid of plausible prices,
just nowhere near the maps.
"""
from __future__ import annotations

import json

import pytest

from src.config import ROOT, Config, load_config
from src.overlay.calibration import Calibration, save_to_config
from src.overlay.profiles import (
    INVENTORY_PROFILE,
    PROFILE_KEYS,
    STASH_PROFILE,
    profile_for,
)
from src.overlay.state import DEFAULT_STATE_PATH, state_path_for
from src.stash.character_api import (
    CharacterInventorySource,
    inventory_items,
)
from src.stash.base import StashTab
from src.stash.official_api import StashAccessError


# ---------------------------------------------------------------------------
# A stand-in for requests.Session: routes by URL, records what was asked for.
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

        class _Cookies:
            def set(self, *_a, **_k):
                return None

        self.cookies = _Cookies()

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for fragment, payload in self.routes.items():
            if fragment in url:
                if isinstance(payload, FakeResponse):
                    return payload
                return FakeResponse(payload)
        return FakeResponse({}, status_code=404)


def _map_item(x: int, y: int, inventory_id: str, name: str = "Terror Solace") -> dict:
    """A T16 Originator map, at a position, in some part of the character."""
    return {
        "verified": True,
        "w": 1,
        "h": 1,
        "id": f"{inventory_id}-{x}-{y}",
        "inventoryId": inventory_id,
        "x": x,
        "y": y,
        "name": name,
        "typeLine": "Map (Tier 16)",
        "baseType": "Map (Tier 16)",
        "rarity": "Rare",
        "ilvl": 84,
        "identified": True,
        "frameType": 2,
        "properties": [
            {"name": "Item Quantity", "values": [["+84%", 1]], "type": 2},
            {"name": "Item Rarity", "values": [["+134%", 1]], "type": 3},
            {"name": "Monster Pack Size", "values": [["+32%", 1]], "type": 4},
            {"name": "More Maps", "values": [["+35%", 1]], "type": 88},
        ],
        "implicitMods": [
            {"description": "Area is Influenced by the Originator's Memories"}
        ],
        "extended": {"hashes": {"implicit": [["implicit.stat_2696470877", [0]]]}},
    }


def _source(routes: dict[str, object]) -> CharacterInventorySource:
    return CharacterInventorySource(
        poesessid="cookie",
        account_name="Exile#1234",
        session=FakeSession(routes),
        min_interval=0.0,
    )


# -- reading the backpack ---------------------------------------------------
def test_only_the_backpack_grid_survives_the_filter():
    """Equipped items carry x/y too, and theirs mean something else entirely.

    A helmet in the Helm slot reports x=0, y=0. Drawn on the inventory grid
    that is the top-left cell, which is almost certainly holding a different
    map -- the exact wrong-price-on-the-wrong-cell failure the overlay cannot
    have.
    """
    payload = [
        _map_item(0, 0, "MainInventory"),
        _map_item(0, 0, "Helm"),
        _map_item(1, 0, "Flask"),
        _map_item(2, 3, "MainInventory"),
        {"inventoryId": "MainInventory"},  # no coordinates at all
    ]
    kept = inventory_items(payload)
    assert [(i["x"], i["y"]) for i in kept if "x" in i] == [(0, 0), (2, 3)]
    assert all(i["inventoryId"] == "MainInventory" for i in kept)


def test_inventory_items_tolerates_a_missing_or_odd_items_array():
    assert inventory_items(None) == []
    assert inventory_items([]) == []
    assert inventory_items(["not a dict", 7]) == []


def test_fetch_reads_the_named_character_and_keeps_its_coordinates():
    source = _source(
        {
            "get-items": {
                "items": [
                    _map_item(4, 2, "MainInventory"),
                    _map_item(0, 0, "Weapon"),
                ]
            }
        }
    )
    maps = source.fetch_tab("Allflame", StashTab(id="Bowzerker", name="Bowzerker"))

    assert len(maps) == 1
    assert (maps[0].x, maps[0].y) == (4, 2)
    assert maps[0].is_originator
    # The character name travels with the item, so the overlay's summary line
    # and the table both say whose inventory this was.
    assert maps[0].stash_tab_name == "Bowzerker"
    url, params = source.session.calls[-1]
    assert "get-items" in url
    assert params["character"] == "Bowzerker"


def test_a_character_holding_no_maps_is_an_empty_read_not_an_error():
    source = _source({"get-items": {"items": [_map_item(0, 0, "BodyArmour")]}})
    assert source.fetch_tab("Allflame", StashTab(id="Bowzerker", name="Bowzerker")) == []


# -- picking a character ----------------------------------------------------
def test_characters_are_filtered_to_the_league_being_priced():
    """The endpoint returns every character on the account, forever.

    Standard, hardcore, and three years of dead leagues. Offering all of them
    turns a two-entry picker into a thirty-entry one in which the wrong pick
    silently prices nothing.
    """
    source = _source(
        {
            "get-characters": [
                {"name": "Bowzerker", "league": "Allflame"},
                {"name": "OldFriend", "league": "Standard"},
                {"name": "Trapper", "league": "Allflame"},
            ]
        }
    )
    names = [c.name for c in source.list_tabs("Allflame")]
    assert names == ["Bowzerker", "Trapper"]


def test_a_character_is_offered_as_a_twelve_by_five_grid():
    source = _source({"get-characters": [{"name": "Bowzerker", "league": "Allflame"}]})
    tab = source.list_tabs("Allflame")[0]
    assert (tab.grid_width, tab.grid_height) == (12, 5)
    # The id is the name because /get-items is keyed by name; there is no other
    # stable handle GGG exposes.
    assert tab.id == "Bowzerker"


def test_an_unexpected_character_payload_is_refused_rather_than_guessed_at():
    source = _source({"get-characters": "a plain string"})
    with pytest.raises(StashAccessError):
        source.list_tabs("Allflame")


def test_the_inventory_reader_needs_the_same_credentials_as_the_stash_reader(monkeypatch):
    """No credentials means unavailable -- on a machine that has some, too.

    The adapter falls back to POESESSID in the environment when it is passed
    none, which is the behaviour that makes a .env work. It also meant this
    test passed or failed depending on whether the developer running it had
    ever configured the app: load_config() calls load_dotenv(), so one earlier
    test in the same session was enough to put a real cookie in os.environ and
    make "no credentials" quietly available.
    """
    monkeypatch.delenv("POESESSID", raising=False)
    monkeypatch.delenv("POE_ACCOUNT_NAME", raising=False)
    source = CharacterInventorySource(poesessid="", account_name="", session=FakeSession({}))
    ok, why = source.available()
    assert not ok and why


# -- the second calibration -------------------------------------------------
def test_the_two_profiles_read_different_config_blocks():
    cfg = Config(
        {
            "overlay": {"origin": [10, 20], "cell_px": 53.0, "screen": [2560, 1080]},
            "overlay_inventory": {"origin": [900, 800], "cell_px": 47.5, "screen": [2560, 1080]},
        }
    )
    assert Calibration.from_config(cfg, STASH_PROFILE).origin == (10, 20)
    assert Calibration.from_config(cfg, INVENTORY_PROFILE).origin == (900, 800)
    # Default profile stays the stash, so every existing caller is unchanged.
    assert Calibration.from_config(cfg).origin == (10, 20)


def test_an_inventory_block_with_no_grid_is_twelve_by_five_not_twelve_by_twelve():
    """Inheriting the stash's 12x12 would draw seven rows of tiles over the game."""
    cal = Calibration.from_config(Config({"overlay_inventory": {}}), INVENTORY_PROFILE)
    assert cal.grid == (12, 5)


def test_the_two_profiles_default_to_different_hotkeys():
    """One chord toggling both would defeat the point of having two windows."""
    stash = Calibration.from_config(Config({}), STASH_PROFILE)
    inventory = Calibration.from_config(Config({}), INVENTORY_PROFILE)
    assert stash.hotkey_toggle != inventory.hotkey_toggle


def test_saving_the_inventory_calibration_leaves_the_stash_one_alone(tmp_path):
    """Calibrating one panel must never move the other."""
    cfg = load_config(ROOT / "config.example.yaml")
    stash_before = Calibration.from_config(cfg, STASH_PROFILE)

    inventory = Calibration(
        origin=(1400, 700), cell_px=47.5, grid=(12, 5), screen=(2560, 1080),
        profile=INVENTORY_PROFILE.key,
    )
    path = save_to_config(cfg, inventory, tmp_path / "config.yaml")
    reloaded = load_config(path)

    assert Calibration.from_config(reloaded, INVENTORY_PROFILE).origin == (1400, 700)
    assert Calibration.from_config(reloaded, STASH_PROFILE).origin == stash_before.origin
    assert not Calibration.from_config(reloaded, STASH_PROFILE).calibrated


def test_a_calibration_remembers_which_profile_it_came_from_through_the_nudge_keys():
    """`replace()` runs on every arrow press; forgetting here saves to the wrong block."""
    cal = Calibration.from_config(Config({"overlay_inventory": {}}), INVENTORY_PROFILE)
    moved = cal.moved(3, -2).resized(d_width=12).with_opacity(0.8)
    assert moved.profile == INVENTORY_PROFILE.key
    assert moved.profile_spec is INVENTORY_PROFILE


def test_the_shipped_example_config_documents_both_blocks_and_calibrates_neither():
    cfg = load_config(ROOT / "config.example.yaml")
    for profile in (STASH_PROFILE, INVENTORY_PROFILE):
        cal = Calibration.from_config(cfg, profile)
        assert not cal.calibrated, f"{profile.key} ships pre-calibrated"
    assert Calibration.from_config(cfg, INVENTORY_PROFILE).grid == (12, 5)


# -- the second state file --------------------------------------------------
def test_each_profile_watches_its_own_file():
    """A shared file would mean every write merged or blanked the other half."""
    stash = state_path_for(STASH_PROFILE)
    inventory = state_path_for(INVENTORY_PROFILE)
    assert stash != inventory
    assert stash == DEFAULT_STATE_PATH
    assert inventory.parent == stash.parent


def test_an_unknown_profile_key_falls_back_to_the_stash_rather_than_raising():
    """These come from a hand-edited config or an old shortcut; a window with no
    console cannot report a crash, but it can draw where it always did."""
    assert profile_for("typo") is STASH_PROFILE
    assert profile_for(None) is STASH_PROFILE
    assert profile_for(INVENTORY_PROFILE) is INVENTORY_PROFILE
    assert set(PROFILE_KEYS) == {"stash", "inventory"}


# -- the CLI seam -----------------------------------------------------------
def test_the_overlay_command_accepts_a_profile_and_passes_it_through(monkeypatch):
    from src import cli

    seen: dict = {}

    def fake_run_overlay(cfg, *, calibrate, state_path, profile):
        seen.update(calibrate=calibrate, state_path=state_path, profile=profile)
        return 0

    monkeypatch.setattr("src.overlay.window.run_overlay", fake_run_overlay)
    parser = cli.build_parser()
    args = parser.parse_args(["overlay", "--profile", "inventory"])
    assert cli.cmd_overlay(args) == 0
    assert seen["profile"] is INVENTORY_PROFILE


def test_the_overlay_command_still_defaults_to_the_stash(monkeypatch):
    from src import cli

    seen: dict = {}
    monkeypatch.setattr(
        "src.overlay.window.run_overlay",
        lambda cfg, **kw: seen.update(kw) or 0,
    )
    args = cli.build_parser().parse_args(["overlay"])
    assert cli.cmd_overlay(args) == 0
    assert seen["profile"] is STASH_PROFILE


def test_config_json_is_still_readable_after_both_blocks_exist():
    """Guards the shape, not the values: the defaults are merged, not replaced."""
    cfg = load_config(ROOT / "config.example.yaml")
    json.dumps(cfg.as_dict())
    assert isinstance(cfg.get("overlay_inventory"), dict)


# -- geometry, for a panel that is not square -------------------------------
def test_the_inventory_window_is_five_rows_tall_not_twelve():
    """The window is sized to the grid, so a wrong grid is a wrong window.

    Twelve rows of transparent window hanging below a five-row panel is not
    merely cosmetic: it is twelve rows of per-pixel compositing on every
    redraw, over whatever the game is drawing there.
    """
    from src.overlay.layout import window_geometry

    cal = Calibration(
        origin=(1400, 700), cell_px=50.0, grid=(12, 5), profile=INVENTORY_PROFILE.key
    )
    width, height, x, y = window_geometry(cal)
    assert (width, height) == (600, 250)
    assert (x, y) == (1400, 700)


def test_a_map_in_the_last_inventory_cell_lands_inside_the_window():
    """Cell (11, 4) is the bottom-right of the backpack and must be the corner."""
    cal = Calibration(
        origin=(1400, 700), cell_px=47.5, grid=(12, 5), profile=INVENTORY_PROFILE.key
    )
    assert cal.cell_rect(11, 4)[2:] == cal.grid_rect()[2:]


# ---------------------------------------------------------------------------
# The grid the browser draws, and the window the overlay opens
# ---------------------------------------------------------------------------
def _tab(**kwargs):
    from src.stash.base import StashTab

    defaults = dict(id="t", name="t", index=0)
    defaults.update(kwargs)
    return StashTab(**defaults)


def test_a_character_inventory_is_drawn_five_rows_tall_not_twelve():
    """The browser grid used to pad every tab out to a stash tab's 12x12.

    `render_grid` takes width and height and then only ever *grows* them, so a
    12x5 backpack came out as a 12x12 board with seven empty rows under it --
    on a page whose own caption says it reads the 12x5 inventory.
    """
    from src.ui.stash_pricer import _grid_for

    inventory = _tab(id="Bowzerker", name="Bowzerker", type="character",
                     grid_width=12, grid_height=5)
    assert _grid_for([inventory], []) == (12, 5)


def test_a_quad_tab_still_grows_the_grid_past_the_default():
    """Sizing from the tab must not cap a quad tab at 12x12."""
    from src.ui.stash_pricer import _grid_for

    assert _grid_for([_tab(type="QuadStash")], []) == (24, 24)


def test_two_tabs_on_one_board_are_drawn_at_the_larger_of_the_two():
    from src.ui.stash_pricer import _grid_for

    both = [_tab(type="QuadStash"), _tab(id="n", name="n", type="NormalStash")]
    assert _grid_for(both, []) == (24, 24)


def test_a_source_with_no_tabs_leaves_the_renderer_to_guess():
    """A CSV or clipboard paste has no tab behind it and must not be forced."""
    from src.ui.stash_pricer import _grid_for

    assert _grid_for(None, []) is None
    assert _grid_for([], []) is None


def test_the_overlay_window_grows_to_hold_a_grid_wider_than_the_calibration():
    """OverlayState.grid was written, read back, and used by nothing that drew.

    A quad tab sent to a 12x12 calibration had every tile past column twelve
    fall outside the window -- present in the draw list, absent from the screen.
    """
    from src.overlay.layout import window_geometry
    from src.overlay.state import OverlayState

    cal = Calibration(origin=(100, 200), cell_px=50.0, grid=(12, 12),
                      profile=STASH_PROFILE.key)
    quad = OverlayState(tiles=(), grid=(24, 24))
    assert window_geometry(cal) == (600, 600, 100, 200)
    assert window_geometry(cal, quad) == (1200, 1200, 100, 200)


def test_a_grid_the_calibration_was_not_made_for_is_named_rather_than_drawn_silently():
    """The one failure that looks like success: every tile lands on *a* cell."""
    from src.overlay.layout import grid_mismatch
    from src.overlay.state import OverlayState

    cal = Calibration(origin=(1400, 700), cell_px=50.0, grid=(12, 5),
                      profile=INVENTORY_PROFILE.key)
    assert grid_mismatch(cal, OverlayState(tiles=(), grid=(12, 5))) == ""

    warning = grid_mismatch(cal, OverlayState(tiles=(), grid=(12, 12)))
    assert "12x12" in warning and "12x5" in warning


def test_a_hidden_profile_is_told_apart_from_a_character_that_is_gone():
    """`list_tabs` returns [] for both, and the wrong one sends you hunting."""
    from src.overlay.analyze import AnalyzeError, _tabs_to_fetch
    from src.overlay.state import SOURCE_INVENTORY, SOURCE_STASH, OverlaySource

    inventory = OverlaySource(kind=SOURCE_INVENTORY, names=("Bowzerker",),
                              league="Allflame")
    with pytest.raises(AnalyzeError) as caught:
        _tabs_to_fetch(inventory, [])
    assert "hidden" in str(caught.value).lower()

    # A stash tab that really is gone keeps the original sentence.
    stash = OverlaySource(kind=SOURCE_STASH, names=("Maps",), league="Allflame")
    with pytest.raises(AnalyzeError) as caught:
        _tabs_to_fetch(stash, [])
    assert "not in Allflame any more" in str(caught.value)
