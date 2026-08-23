"""Export a small, anonymised sample dataset so a new checkout has something to train on.

    python scripts/export_sample.py [rows]

Writes data/sample_listings.csv in the format `python -m src.cli import-market`
accepts. Seller names are dropped.
"""
import random
import sys

sys.path.insert(0, ".")

from src.config import load_config
from src.database.repository import Repository
from src.market.collectors.manual_import import listing_rows_to_csv

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

# Shares one writer with the Market Data page's dataset export, so the two
# cannot drift out of the importer's schema independently again.
with open("data/sample_listings.csv", "w", newline="", encoding="utf-8") as handle:
    handle.write(listing_rows_to_csv(rows, anonymise=True))
print(f"wrote data/sample_listings.csv with {len(rows)} rows")
repo.close()
