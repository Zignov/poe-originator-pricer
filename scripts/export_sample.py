"""Export an anonymised listing dataset in the format `import-market` accepts.

    python scripts/export_sample.py                 # 400-row sample_listings.csv
    python scripts/export_sample.py --seed          # the full dataset the release ships
    python scripts/export_sample.py 1000 --out data/mine.csv

Two files come out of this, for two different jobs.

`data/sample_listings.csv` (the default)
    A small sample, so a fresh *checkout* has something to train on without
    collecting first. Kept small on purpose -- it is a fixture, not a dataset.

`data/originator_listings_allflame.csv` (`--seed`)
    The dataset the release zip ships and the launcher imports into an empty
    database on a first run. It is meant to be the whole collected market, so
    that a downloader's model is fitted on what the maintainer's was and their
    first collection *adds* to it. See src/market/seed.py.

Outlier-flagged rows are kept in `--seed` mode and dropped from the sample.
Nothing is lost by keeping them: importing re-runs the outlier pass over the
whole league (`src/market/ingest.store`), so they are re-judged on arrival
rather than trusted, and training excludes whatever that pass flags. Dropping
them would instead ship a dataset that silently disagrees with its own row
count.

Seller names never leave: `listing_rows_to_csv(anonymise=True)` replaces each
account with a sequential label that means nothing outside the one file. The
labels are kept rather than blanked because the train/test split groups on them
-- see the comment on `_pseudonym`.
"""
import argparse
import random
import sys

sys.path.insert(0, ".")

from src.config import ROOT, load_config
from src.database.repository import Repository
from src.market.collectors.manual_import import listing_rows_to_csv
from src.market.seed import FINGERPRINT_SETTING, SEED_CSV, _fingerprint

SAMPLE_PATH = "data/sample_listings.csv"
SEED_PATH = "data/originator_listings_allflame.csv"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "rows", nargs="?", type=int, default=None,
        help="how many rows to write (default: 400 for the sample, all of them for --seed)",
    )
    parser.add_argument(
        "--seed", action="store_true",
        help=f"write the release seed ({SEED_PATH}) instead of the small sample",
    )
    parser.add_argument("--out", default=None, help="write somewhere else instead")
    parser.add_argument(
        "--db", default=None,
        help="export from this database instead of the configured one (opened read-only)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    source = Repository.read_only(args.db) if args.db else Repository(cfg.db_path)
    with source as repo:
        rows = repo.listing_rows(
            league=cfg.get("league"),
            include_outliers=args.seed,
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
        rows = [r for r in rows if r["price_amount"] and r["price_amount"] > 0]

        # Shuffled so that a truncated export is a sample of the market rather
        # than of one afternoon of it. Seeded, so rebuilding the same file twice
        # produces the same file.
        random.Random(7).shuffle(rows)
        wanted = args.rows if args.rows is not None else (None if args.seed else 400)
        if wanted is not None:
            rows = rows[:wanted]

        destination = args.out or (SEED_PATH if args.seed else SAMPLE_PATH)
        with open(destination, "w", newline="", encoding="utf-8") as handle:
            handle.write(listing_rows_to_csv(rows, anonymise=True, in_chaos=True))

        flagged = sum(1 for r in rows if r["is_outlier"])
        sellers = len({(r["seller"] or "").strip() for r in rows if (r["seller"] or "").strip()})
        print(
            f"wrote {destination} with {len(rows)} rows "
            f"({sellers} distinct accounts, anonymised"
            + (f"; {flagged} currently flagged as outliers" if flagged else "")
            + ")"
        )
        if _claim_seed(args, destination, repo):
            print(
                "  recorded it as already loaded here, so the next launch does not "
                "re-import this database's own rows on top of themselves"
            )
    return 0


def _claim_seed(args, destination: str, repo: Repository) -> bool:
    """Tell the source database it already holds the sample just written from it.

    `src.market.seed` decides whether to import the bundled CSV by comparing a
    fingerprint of the file against the one stored in `app_settings`. Writing a
    new seed therefore leaves the very database it was exported from looking
    like it has never seen it -- so the next launch dutifully deletes the
    `bundled_sample` rows and imports the file, and every row that came from
    somewhere else (a collection, a user's own CSV) arrives a second time under
    a new label. Rebuilding the seed for this release did exactly that once, and
    the database went from 9,552 rows to 11,472 before anyone noticed.

    The claim is only made for the real seed path, written from the configured
    database. `--out` somewhere else, `--db` (opened read-only), and the small
    `sample_listings.csv` all leave the setting alone: none of them is the file
    the launcher imports, and a read-only handle could not be written to anyway.
    """
    if not args.seed or args.db or destination != SEED_PATH:
        return False
    repo.set_setting(FINGERPRINT_SETTING, _fingerprint(ROOT / SEED_CSV))
    return True


if __name__ == "__main__":
    raise SystemExit(main())
