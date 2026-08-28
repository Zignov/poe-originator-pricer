"""Put the dataset the shipped model was trained on into the database.

Why this exists
---------------
The downloadable zip ships a trained model but not the database it was trained
on: `scripts/package_release.py` leaves `data/originator.db` out because it is
tens of megabytes of one league's listings. That was right for the model -- a
downloader gets working prices immediately -- and wrong for everything
downstream of it. A fresh install had a model fitted on thousands of listings
sitting over an empty table, so the first "Collect and retrain" trained on the
few hundred rows just pulled and overwrote the shipped model with a far thinner
one. From the outside that looks like the collection *replacing* the dataset
rather than adding to it, because there was never anything there to add to.

`data/originator_listings_allflame.csv` is already tracked and already in the
zip -- it is the anonymised export of the very rows the shipped model was
trained on (`scripts/export_sample.py --seed`). Importing it gives the download
a database that matches its model, so collecting adds to a real baseline and
retraining improves on one instead of starting over.

Running again after an upgrade
------------------------------
Seeding only when the database is empty would have meant that everyone who
already installed a previous version never receives a newer, larger sample --
they upgrade into the same mismatch this module exists to remove. So the
trigger is the *content of the bundled file*, not the emptiness of the table:
a fingerprint of the CSV is stored after each import and compared on each run.

That makes a release able to replace its own sample, which is only safe because
the bundled rows are labelled with a source of their own. Replacing means
deleting rows this module previously inserted and importing the new file --
`BUNDLED_SOURCE` is what separates those from every listing the user collected
or imported themselves, and nothing outside that label is ever deleted here.

Two more guards. The import only runs for the league the bundled model was
trained on, so a player on a different league is not handed another league's
prices under their own league's name. And divine-priced rows convert at the
rate recorded at training time rather than today's: roughly one row in eight is
priced in divine, and the sample is a snapshot weeks old by the time anyone
downloads it.
"""
from __future__ import annotations

import hashlib
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

#: `originator_listings.source` for rows this module inserted. Deliberately not
#: `manual_csv`: that is what a user's own CSV import is labelled, and these
#: rows have to be replaceable without touching those.
BUNDLED_SOURCE = "bundled_sample"

#: `app_settings` key holding the fingerprint of the sample last imported.
FINGERPRINT_SETTING = "bundled_sample_fingerprint"


def _model_metadata() -> dict:
    try:
        return json.loads((ROOT / MODEL_METADATA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fingerprint(path: Path) -> float:
    """A number identifying this exact sample file.

    `app_settings.value` is a REAL column, so the fingerprint has to survive as
    a float: 48 bits of a SHA-256 is well inside the 53 an IEEE double carries
    exactly, and is far more than enough to tell two releases' samples apart.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return float(int(digest, 16))


def seed_bundled_sample(
    cfg: Config, repo: Repository, log: Callable[[str], None] | None = None
) -> int:
    """Import the bundled sample if this database does not already hold it.

    Returns the number of rows inserted -- 0 when any guard declines, which is
    the normal case for every run after the one that imports it.
    """

    def say(message: str) -> None:
        if log:
            log(message)

    path = ROOT / SEED_CSV
    if not path.exists():
        return 0

    league = cfg.get("league")
    meta = _model_metadata()
    trained_league = meta.get("league")
    if trained_league and trained_league != league:
        # Only worth saying to someone who has nothing, so that it reads as an
        # explanation of the empty Market Data page rather than as a warning.
        if not repo.count_listings(league)["total"]:
            say(
                f"no bundled listings for {league!r}: the sample is from "
                f"{trained_league!r}. Collect from the Market Data page to fill it."
            )
        return 0

    fingerprint = _fingerprint(path)
    if repo.get_setting(FINGERPRINT_SETTING) == fingerprint:
        return 0

    replaced = repo.delete_listings_by_source(BUNDLED_SOURCE, league=league)

    rate = meta.get("divine_to_chaos_at_training") or cfg.get("currency.divine_to_chaos")
    collector = ManualCsvCollector(
        converter=CurrencyConverter(float(rate)),
        league=league,
        map_tier=cfg.get("scope.map_tier", 16),
        source_name=BUNDLED_SOURCE,
    )
    stats = store(cfg, repo, collector.collect(path=path))
    repo.set_setting(FINGERPRINT_SETTING, fingerprint)

    inserted = stats.get("inserted", 0)
    if inserted:
        say(
            f"loaded {inserted:,} listings from {SEED_CSV.name} -- the market the "
            "bundled model was trained on"
            + (
                f", replacing {replaced:,} from the previous version"
                if replaced
                else ". Collecting adds to it."
            )
        )
    return inserted
