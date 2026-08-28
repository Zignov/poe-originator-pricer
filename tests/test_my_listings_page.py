"""The stale-listing view: which maps it selects, and what it lights up in game."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.ui.my_listings import overlay_groups, regex_for_stale, stale_rows

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _row(name, hours, price=150.0, predicted=140.0, low=120.0, **extra):
    row = {
        "id": abs(hash(name)) % 10_000,
        "name": name,
        "base_type": "Terror Solace",
        "price_chaos": price,
        "predicted_chaos": predicted,
        "interval_low_chaos": low,
        "priced_at": (T0 - timedelta(hours=hours)).isoformat(),
        "stash_name": "Sell 3",
        "stash_x": 1,
        "stash_y": 2,
        "ambiguous": 0,
        "exclude_from_learning": 0,
    }
    row.update(extra)
    return row


def test_only_maps_past_the_threshold_are_stale():
    rows = [_row("Glyph Solace", 3), _row("Rune Path", 30), _row("Bleak Lands", 200)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    assert [s["name"] for s in stale] == ["Bleak Lands", "Rune Path"], "oldest first"


def test_each_stale_row_carries_hours_and_a_recommendation():
    stale = stale_rows([_row("Rune Path", 30)], now=T0, stale_after_hours=24)
    assert stale[0]["hours_listed"] == 30
    assert stale[0]["recommendation"].price_chaos < 150.0
    assert stale[0]["recommendation"].source == "ladder"


def test_the_regex_lights_the_stale_maps_and_not_the_fresh_ones():
    rows = [_row("Glyph Solace", 3), _row("Rune Path", 30)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    batches = regex_for_stale(stale, rows)
    assert batches.ok
    # The game's search is case-insensitive, and the builder emits lowercase.
    pattern = batches.pages[0].pattern.lower()
    assert "rune" in pattern or "path" in pattern
    assert "glyph" not in pattern and "solace" not in pattern


def test_no_stale_maps_produces_no_pattern():
    rows = [_row("Glyph Solace", 3)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    assert stale == []
    assert not regex_for_stale(stale, rows).ok


def test_overlay_groups_split_by_tab_because_the_overlay_draws_one_grid():
    rows = [
        _row("Rune Path", 30, stash_name="Sell 3"),
        _row("Bleak Lands", 40, stash_name="Sell 4"),
        _row("Crooked Path", 50, stash_name="Sell 3"),
    ]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    groups = overlay_groups(stale)
    assert sorted(groups) == ["Sell 3", "Sell 4"]
    assert len(groups["Sell 3"]) == 2


def test_a_listing_without_coordinates_is_unplaced_not_guessed():
    rows = [_row("Rune Path", 30, stash_x=None, stash_y=None)]
    stale = stale_rows(rows, now=T0, stale_after_hours=24)
    groups = overlay_groups(stale)
    assert groups == {}, "no cell is better than a guessed cell"
