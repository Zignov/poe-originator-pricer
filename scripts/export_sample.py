"""Export a small, anonymised sample dataset so a new checkout has something to train on.

    python scripts/export_sample.py [rows]

Writes data/sample_listings.csv in the format `python -m src.cli import-market`
accepts. Seller names are dropped.
"""
import csv
import random
import sys

sys.path.insert(0, ".")

from src.config import load_config
from src.database.repository import Repository
from src.market.collectors.manual_import import CSV_TEMPLATE_COLUMNS
from src.schema import FEATURE_NAMES

rows_wanted = int(sys.argv[1]) if len(sys.argv) > 1 else 400
cfg = load_config()
repo = Repository(cfg.db_path)
rows = repo.listing_rows(
    league=cfg.get("league"),
    include_outliers=False,
    map_tier=cfg.get("scope.map_tier"),
    require_uncorrupted=cfg.get("scope.require_uncorrupted"),
)
rows = [r for r in rows if r["price_chaos"] and r["price_chaos"] > 0]
random.Random(7).shuffle(rows)
rows = rows[:rows_wanted]

with open("data/sample_listings.csv", "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(CSV_TEMPLATE_COLUMNS)
    for row in rows:
        writer.writerow(
            [*(row[name] for name in FEATURE_NAMES),
             row["price_amount"], row["price_currency"], row["map_tier"],
             row["item_level"], "true" if row["corrupted"] else "false",
             "", "", row["indexed_at"] or ""]
        )
print(f"wrote data/sample_listings.csv with {len(rows)} rows")
repo.close()
