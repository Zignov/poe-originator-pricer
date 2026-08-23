"""Remove listings that have sat unsold far longer than the market-data window.

A month-old ask is evidence the price was wrong, not evidence of value. Keeping
them also produced a bimodal age distribution that made the chronological
train/test split compare two different market regimes.
"""
import sys
sys.path.insert(0, ".")
from src.config import load_config
from src.database.repository import Repository

cutoff_days = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
cfg = load_config()
repo = Repository(cfg.db_path)
removed = repo.conn.execute(
    "DELETE FROM originator_listings WHERE COALESCE(indexed_at, captured_at) < "
    "datetime('now', ?)",
    (f"-{cutoff_days} days",),
).rowcount
repo.conn.commit()
print(f"removed {removed} listings older than {cutoff_days} days")
print(repo.count_listings(cfg.get("league")))
repo.close()
