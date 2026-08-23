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
if tab_name:
    chosen = [t for t in tabs if t.name == tab_name]
    if not chosen:
        print(f"no tab named {tab_name!r}. Available:")
        for t in tabs:
            print(f"  - {t.name}")
        raise SystemExit(1)
else:
    print("no tab given. Available tabs:")
    for t in tabs:
        print(f"  - {t.name}")
    print()
    print('Re-run with the one holding your Originator Maps, e.g.:')
    print('  python scripts/inspect_stash_shape.py "Originator Maps"')
    raise SystemExit(0)
print(f"inspecting tab index {chosen[0].index}")

# Deliberately raw: going through the Originator filter reports zero whenever the
# tab holds no in-scope maps, which tells us nothing about the payload shape.
try:
    data = source._get(
        {"league": league, "tabs": 0, "tabIndex": chosen[0].index, "accountName": source.account_name}
    )
except StashAccessError as exc:
    raise SystemExit(f"stash read failed: {exc}")

payloads = [i for i in (data.get("items") or []) if isinstance(i, dict)]
print(f"items in that tab: {len(payloads)}")
rares = [i for i in payloads if i.get("explicitMods")]
print(f"items carrying explicitMods: {len(rares)}")
if not rares:
    raise SystemExit("no modified items in this tab -- try one holding rare maps")

raw = rares[0]
print()
print("top-level keys present:", sorted(raw.keys()))
print("has 'extended':", "extended" in raw)
if isinstance(raw.get("extended"), dict):
    print("  extended keys:", sorted(raw["extended"].keys()))

mods = raw.get("explicitMods")
print()
print("explicitMods type:", type(mods).__name__)
if isinstance(mods, list) and mods:
    first = mods[0]
    print("  entry type:", type(first).__name__)
    if isinstance(first, dict):
        print("  entry keys:", sorted(first.keys()))
        subs = first.get("mods")
        print("  has 'mods' sub-array:", bool(subs))
        if subs and isinstance(subs, list) and isinstance(subs[0], dict):
            print("  sub-keys:", sorted(subs[0].keys()))
            print("  affix name present:", "name" in subs[0])
            print("  affix tier present:", "tier" in subs[0])

with_tiers = sum(
    1
    for p in rares
    for m in (p.get("explicitMods") or [])
    if isinstance(m, dict) and m.get("mods")
)
total = sum(1 for p in rares for m in (p.get("explicitMods") or []))
print()
print(f"modifier lines carrying affix name/tier: {with_tiers} of {total}")
print()
print(
    "VERDICT: affix counting WILL work on this source"
    if with_tiers and with_tiers == total
    else "VERDICT: affix counting CANNOT work on this source as built -- "
    "the payload carries modifier text only"
)
