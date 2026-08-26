"""The two things the overlay can be drawn over, and what differs between them.

A stash tab and a character's inventory are the same problem twice: a grid of
cells somewhere on the screen, with priced maps sitting in some of them. What
differs is small and entirely data -- the grid is 12x12 rather than 12x5, the
panel is on the other side of the screen so the origin is nowhere near the
same, and the hotkey has to be a different chord because both windows can be up
at once.

So rather than a second copy of the calibration dataclass, the overlay grew a
**profile**: one small record naming which config block holds the calibration,
which file the state is written to, and what the sensible defaults are. Every
other module takes a profile and is otherwise unchanged.

Both profiles run as separate processes on purpose. The alternative -- one
window spanning both panels -- means a single rectangle stretched across the
middle of the screen with a hole in it, which Tk cannot do without either
covering the game between them or maintaining two canvases anyway. Two
processes is the honest shape of two independent windows, and it means the
inventory overlay can be up while the stash one is not.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The default toggle chords. Deliberately different: with both overlays
#: running, one chord toggling both would make the pair useless -- the whole
#: reason to have two is that you look at the inventory while the tab is shut.
STASH_HOTKEY = "ctrl+alt+o"
INVENTORY_HOTKEY = "ctrl+alt+i"


@dataclass(frozen=True)
class OverlayProfile:
    """One drawable surface: where its calibration lives, where its state lives."""

    key: str
    #: The top-level config.yaml block holding this profile's calibration.
    config_key: str
    label: str
    #: Cells across and down. The inventory is 12x5 and, unlike a stash tab,
    #: never anything else -- there is no quad inventory.
    grid: tuple[int, int]
    hotkey: str
    #: File under data/ that the app writes and this profile's window watches.
    state_filename: str
    #: What the calibration screen tells the user to line up with.
    calibration_hint: str


STASH_PROFILE = OverlayProfile(
    key="stash",
    config_key="overlay",
    label="Stash tab",
    grid=(12, 12),
    hotkey=STASH_HOTKEY,
    state_filename="overlay_state.json",
    calibration_hint="Line this up with the stash tab's own cell borders, then press Enter.",
)

INVENTORY_PROFILE = OverlayProfile(
    key="inventory",
    config_key="overlay_inventory",
    label="Inventory",
    grid=(12, 5),
    hotkey=INVENTORY_HOTKEY,
    state_filename="overlay_inventory_state.json",
    calibration_hint=(
        "Line this up with your inventory's own cell borders, then press Enter."
    ),
)

PROFILES: dict[str, OverlayProfile] = {
    profile.key: profile for profile in (STASH_PROFILE, INVENTORY_PROFILE)
}

#: What the CLI's --profile accepts, and the order the UI offers them in.
PROFILE_KEYS: tuple[str, ...] = tuple(PROFILES)

DEFAULT_PROFILE = STASH_PROFILE


def profile_for(key: str | OverlayProfile | None) -> OverlayProfile:
    """Resolve a profile from a key, tolerating a profile or None.

    Unknown keys fall back to the stash rather than raising: every caller of
    this is either a CLI argument argparse has already constrained or a value
    read back out of a config file, and a typo in the latter should leave the
    overlay drawing where it always did instead of failing to start a window
    that has no console to print into.
    """
    if isinstance(key, OverlayProfile):
        return key
    return PROFILES.get(str(key or ""), DEFAULT_PROFILE)
