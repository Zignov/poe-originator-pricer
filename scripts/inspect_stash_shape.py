"""Report the *shape* of what your stash endpoint returns, not its contents.

    python scripts/inspect_stash_shape.py "Originator Maps"

Prints only structural facts -- which keys exist, what type the modifier entries
are, whether affix names and tiers are present. No item names, no prices, no
account details, nothing that identifies you. Safe to paste into a chat.

Why this exists: the affix count and exalt verdict need each modifier to name the
affix that produced it and its tier. The trade API supplies that. GGG's
documented Item schema does not -- `explicitMods` is defined as objects carrying
only `description` and `flags` -- so the feature may be unable to work on stash
data at all. This says which it is for your account.
"""
import sys

sys.path.insert(0, ".")

from src.config import load_config  # noqa: E402
from src.stash.official_api import SessionStashSource, StashAccessError  # noqa: E402

tab_name = sys.argv[1] if len(sys.argv) > 1 else None
cfg = load_config()
source = SessionStashSource(
    realm=cfg.get("realm", "pc"),
    map_tier=cfg.get("scope.map_tier"),
    require_uncorrupted=cfg.get("scope.require_uncorrupted"),
)

ok, why = source.available()
if not ok:
    print(f"cannot read the stash: {why}")
    print(source.setup_hint)
    raise SystemExit(1)

league = cfg.get("league")
tabs = source.list_tabs(league)
print(f"tabs visible: {len(tabs)}")
# Find where rare MAPS actually live, then report the mod shape for those only.
def is_rare_map(i):
    base = (i.get("baseType") or "") + " " + (i.get("typeLine") or "")
    return "Map" in base and i.get("frameType") == 2


targets = [t for t in tabs if t.name == tab_name] if tab_name else tabs
found = []
for tab in targets:
    try:
        data = source._get(
            {"league": league, "tabs": 0, "tabIndex": tab.index,
             "accountName": source.account_name}
        )
    except Exception as exc:
        print(f"  {tab.name}: read failed ({exc})")
        continue
    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    maps = [i for i in items if is_rare_map(i)]
    if items:
        print(f"  {tab.name:<20} items {len(items):>3}   rare maps {len(maps):>3}")
    found.extend(maps)

print()
print(f"rare maps found: {len(found)}")
if not found:
    raise SystemExit("no rare maps in any tab -- nothing to inspect")

raw = found[0]
print("top-level keys:", sorted(raw.keys()))
print("has 'extended':", "extended" in raw)
if isinstance(raw.get("extended"), dict):
    print("  extended keys:", sorted(raw["extended"].keys()))
    print("  extended:", {k: v for k, v in raw["extended"].items() if k != "text"})

mods = raw.get("explicitMods")
print()
print("explicitMods type:", type(mods).__name__)
if isinstance(mods, list) and mods:
    first = mods[0]
    print("  entry type:", type(first).__name__)
    if isinstance(first, dict):
        print("  entry keys:", sorted(first.keys()))
        print("  has 'mods' sub-array:", bool(first.get("mods")))
    print("  line count on this map:", len(mods))
    for m in mods:
        text = m if isinstance(m, str) else m.get("description")
        print("    -", text)

with_tiers = sum(
    1 for p in found for m in (p.get("explicitMods") or [])
    if isinstance(m, dict) and m.get("mods")
)
total = sum(1 for p in found for m in (p.get("explicitMods") or []))
print()
print(f"modifier lines carrying affix name/tier: {with_tiers} of {total}")
counts = sorted(len(p.get("explicitMods") or []) for p in found)
print("explicit line counts per map:", counts)
