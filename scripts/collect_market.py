"""One-shot market collection runner (also available as `python -m src.cli collect-market`)."""
import sys, time
sys.path.insert(0, ".")
from src.config import load_config, user_agent
from src.database.repository import Repository
from src.market.currency_source import fetch_divine_to_chaos
from src.market.ingest import collect_from_trade_api

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
cfg = load_config()
repo = Repository(cfg.db_path)
try:
    rate = fetch_divine_to_chaos(cfg.get("league"), user_agent(cfg))
    repo.set_divine_rate(cfg.get("league"), rate, source="official_exchange_api")
    print(f"divine/chaos = {rate:.1f} (official exchange endpoint)", flush=True)
except Exception as exc:
    print(f"could not fetch divine rate ({exc}); using config value", flush=True)

start = time.time()
stats = collect_from_trade_api(cfg, repo, limit=limit, progress=lambda m: print(m, flush=True))
print(f"done in {time.time()-start:.0f}s: {stats}", flush=True)
print(repo.count_listings(cfg.get("league")), flush=True)
repo.close()
