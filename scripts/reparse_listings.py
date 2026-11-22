"""Re-derive the stored feature columns from each listing's saved payload.

Run this after the parser learns something it did not know when the rows were
collected -- a renamed reward, a new property id, a new modifier phrasing.

It exists because of the Covens rename. `Item Quantity` became `Atlas Drops`
with a new property type id, the parser recognised neither, and 200 collected
listings were written with `quantity = 0`. The raw trade payload was stored
alongside every one of them, so nothing was actually lost: the number was in
the database the whole time, in `raw_data`, simply not read. Re-reading it
costs nothing and spends no rate limit, which makes it strictly better than
deleting the rows and collecting them again.

    python scripts/reparse_listings.py --dry-run
    python scripts/reparse_listings.py --league Covens
    python scripts/reparse_listings.py --all-leagues

Only the seven feature columns are rewritten. Prices, timestamps, sellers and
outlier flags are left exactly as they were -- this repairs a parse, it does
not re-collect a sample, and a row's identity must not change under it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

sys.path.insert(0, ".")

from src.config import load_config
from src.database.repository import Repository
from src.parsing.item_parser import parse_api_item
from src.schema import FEATURE_NAMES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--league", default=None, help="default: the configured league")
    parser.add_argument("--all-leagues", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="also apply updates that clear a feature that currently has a value",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    repo = Repository(cfg.db_path)
    league = None if args.all_leagues else (args.league or cfg.get("league"))

    sql = "SELECT id, league, raw_data, " + ", ".join(FEATURE_NAMES)
    sql += " FROM originator_listings WHERE raw_data IS NOT NULL AND raw_data != ''"
    params: list = []
    if league:
        sql += " AND league = ?"
        params.append(league)

    rows = repo.conn.execute(sql, params).fetchall()
    print(f"{len(rows)} listings with a stored payload" + (f" in {league}" if league else ""))

    changed = 0
    unreadable = 0
    skipped_no_payload = 0
    zeroed = 0
    gained: Counter[str] = Counter()
    unknown: Counter[str] = Counter()

    for row in rows:
        try:
            payload = json.loads(row["raw_data"])
        except (TypeError, ValueError):
            unreadable += 1
            continue
        # Stash and trade payloads are both stored here; the trade one wraps
        # the item in a listing envelope.
        item = payload.get("item", payload) if isinstance(payload, dict) else None
        if not isinstance(item, dict):
            unreadable += 1
            continue

        # The payload is only an authority on the features if it actually
        # carries the properties they are read from.
        #
        # Not a defensive nicety -- it is the whole correctness of this script.
        # A CSV-imported listing stores `{"csv_row": ...}` here and has no
        # `properties` array at all, so re-parsing it yields seven zeroes. Those
        # rows got their features from the CSV's own columns and are perfectly
        # good; without this check a repair run would silently overwrite 15,266
        # complete Allflame listings with zeroes, which is a far worse version
        # of the bug it was written to fix.
        if not isinstance(item.get("properties"), list) or not item["properties"]:
            skipped_no_payload += 1
            continue

        try:
            parsed = parse_api_item(item)
        except Exception:
            unreadable += 1
            continue

        unknown.update(parsed.unknown_rewards)
        updates = {
            name: int(getattr(parsed, name))
            for name in FEATURE_NAMES
            if int(getattr(parsed, name)) != int(row[name] or 0)
        }
        if not updates:
            continue
        # A feature going non-zero -> 0 is the signature of the mistake above:
        # a payload that does not really describe this row. Counted, reported,
        # and refused without --force, because the information it destroys is
        # not recoverable from anywhere else.
        losses = [n for n, v in updates.items() if v == 0 and int(row[n] or 0) != 0]
        if losses and not args.force:
            zeroed += 1
            continue
        changed += 1
        for name, value in updates.items():
            # Worth distinguishing: a feature going 0 -> something is the
            # rename being repaired, and anything else is a parse that changed
            # meaning, which is a much bigger claim and should be rare.
            gained[name if not row[name] else f"{name} (was non-zero)"] += 1
        if not args.dry_run:
            sets = ", ".join(f"{name} = ?" for name in updates)
            repo.conn.execute(
                f"UPDATE originator_listings SET {sets} WHERE id = ?",
                [*updates.values(), row["id"]],
            )

    if not args.dry_run:
        repo.conn.commit()

    verb = "would change" if args.dry_run else "changed"
    print(f"{verb} {changed} listings")
    for name, count in gained.most_common():
        print(f"   {name:<32} on {count}")
    if skipped_no_payload:
        print(
            f"{skipped_no_payload} listings have no properties in their payload "
            "(CSV or manual imports) and were left alone"
        )
    if zeroed:
        print(
            f"REFUSED to clear features on {zeroed} listings whose payload has no "
            "value for them. Re-run with --force only if that is genuinely intended."
        )
    if unreadable:
        print(f"{unreadable} payloads could not be re-parsed and were left alone")
    if unknown:
        print()
        print("STILL UNRECOGNISED -- these rewards are being read as 0:")
        for name, count in unknown.most_common():
            print(f"   {name:<32} on {count} listings")
        print("Add them to PROPERTY_TO_FEATURE in src/schema.py, then run this again.")
    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
