"""Seed the listings table from the bundled sample, once, on a first run.

Why this exists
---------------
The downloadable zip ships a trained model but not the database it was trained
on: `scripts/package_release.py` leaves `data/originator.db` out because it is
33 MB of one league's listings. That was right for the model -- a downloader
gets working prices immediately -- and wrong for everything downstream of it.
A fresh install had a model built on ~4,300 listings sitting over an empty
table, so the first "Collect and retrain" trained on the ~500 rows just pulled
and overwrote the shipped model with a far thinner one. From the outside that
looks like the collection *replacing* the dataset rather than adding to it,
because there was never anything there to add to.

`data/originator_listings_allflame.csv` is already tracked and already in the
zip -- it is the anonymised export of the very rows the shipped model was
trained on. Importing it on a first run gives the download a database that
matches its model, so collecting adds to a real baseline and retraining
improves on one instead of starting over.

Three guards keep this from touching anyone it should not:

* it only runs when the configured league holds no listings at all;
* it only runs when that league is the one the bundled model was trained on,
  so a player on a different league is not handed another league's prices
  under their own league's name;
* it is a normal import through `ManualCsvCollector`, so the rows land with
  `source = 'manual_csv'` and `scripts/recollect.py`, which discards the
  trade-API sample, leaves them alone.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from src.config import ROOT, Config
from src.database.repository import Repository
from src.market.collectors.manual_import import ManualCsvCollector
from src.market.ingest import store
from src.pricing.currency import CurrencyConverter

#: The anonymised export that ships with the release. Relative to the repo root.
SEED_CSV = Path("data") / "originator_listings_allflame.csv"

#: Written by training; read here for the league and the divine rate of the day.
MODEL_METADATA = Path("models") / "originator_model.json"


def _model_metadata() -> dict:
    try:
        return json.loads((ROOT / MODEL_METADATA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def seed_if_empty(
    cfg: Config, repo: Repository, log: Callable[[str], None] | None = None
) -> int:
    """Import the bundled sample if this league has nothing stored yet.

    Returns the number of rows inserted -- 0 when any guard declines, which is
    the normal case for everyone past their first run.
    """

    def say(message: str) -> None:
        if log:
            log(message)

    league = cfg.get("league")
    if repo.count_listings(league)["total"]:
        return 0

    meta = _model_metadata()
    trained_league = meta.get("league")
    if trained_league and trained_league != league:
        say(
            f"no seed data for {league!r}: the bundled sample is from "
            f"{trained_league!r}. Collect from the Market Data page to fill it."
        )
        return 0

    path = ROOT / SEED_CSV
    if not path.exists():
        return 0

    # The sample is a snapshot, so its divine-priced rows have to be converted at
    # the rate of the day they were captured, not at today's. Roughly one row in
    # eight is priced in divine; converting those at a rate a league out of date
    # would bend the whole top of the price distribution.
    rate = meta.get("divine_to_chaos_at_training") or cfg.get("currency.divine_to_chaos")
    collector = ManualCsvCollector(
        converter=CurrencyConverter(float(rate)),
        league=league,
        map_tier=cfg.get("scope.map_tier", 16),
    )
    stats = store(cfg, repo, collector.collect(path=path))
    inserted = stats.get("inserted", 0)
    if inserted:
        say(
            f"seeded {inserted} listings from {SEED_CSV.name} -- the sample the "
            "bundled model was trained on. Collecting adds to it."
        )
    return inserted
