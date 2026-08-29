"""The bundled sample: what it loads, what it replaces, and what it leaves alone."""
from __future__ import annotations

import json

import pytest

from src.config import Config, load_config
from src.database.repository import Repository
from src.market import seed as seed_module
from src.market.seed import BUNDLED_SOURCE, FINGERPRINT_SETTING, seed_bundled_sample
from src.schema import FEATURE_NAMES

CSV_HEADER = (
    ",".join(FEATURE_NAMES)
    + ",price_amount,price_currency,map_tier,item_level,corrupted,seller,listing_id,indexed_at"
)


def _csv(rows: int, currency: str = "chaos", amount: float = 40.0, offset: int = 0) -> str:
    lines = [CSV_HEADER]
    for i in range(offset, offset + rows):
        features = ",".join(str(50 + i) for _ in FEATURE_NAMES)
        lines.append(
            f"{features},{amount},{currency},16,84,false,anon-{i:04d},,"
            f"2026-08-0{i % 9 + 1}T12:00:00+00:00"
        )
    return "\n".join(lines) + "\n"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A stand-in release layout: a sample CSV and a model sidecar beside it."""
    (tmp_path / "data").mkdir()
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(seed_module, "ROOT", tmp_path)

    def write(csv_text: str, league: str = "Allflame", rate: float = 200.0) -> None:
        (tmp_path / seed_module.SEED_CSV).write_text(csv_text, encoding="utf-8")
        (tmp_path / seed_module.MODEL_METADATA).write_text(
            json.dumps({"league": league, "divine_to_chaos_at_training": rate}),
            encoding="utf-8",
        )

    return tmp_path, write


def _config(league: str = "Allflame", divine: float = 999.0) -> Config:
    cfg = load_config()
    cfg.set("league", league)
    cfg.set("currency.divine_to_chaos", divine)
    return cfg


def test_loads_the_sample_into_an_empty_database(tmp_path, bundle):
    _root, write = bundle
    write(_csv(12))
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_bundled_sample(_config(), repo) == 12
        assert repo.count_listings("Allflame")["total"] == 12


def test_does_nothing_on_every_run_after_the_first(tmp_path, bundle):
    _root, write = bundle
    write(_csv(12))
    cfg = _config()
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_bundled_sample(cfg, repo) == 12
        assert seed_bundled_sample(cfg, repo) == 0
        assert seed_bundled_sample(cfg, repo) == 0
        assert repo.count_listings("Allflame")["total"] == 12
        assert repo.get_setting(FINGERPRINT_SETTING) is not None


def test_an_upgrade_to_a_bigger_sample_replaces_the_old_one(tmp_path, bundle):
    """The reason the trigger is the file's content and not an empty table.

    Someone who installed the previous version has to receive the new market,
    or they upgrade into exactly the mismatch this module exists to remove.
    """
    _root, write = bundle
    write(_csv(12))
    cfg = _config()
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_bundled_sample(cfg, repo) == 12

        write(_csv(40))  # the next release ships a larger sample
        assert seed_bundled_sample(cfg, repo) == 40
        # 40, not 52: the previous bundle was replaced, not stacked underneath.
        assert repo.count_listings("Allflame")["total"] == 40


def test_replacing_a_bundle_never_touches_what_the_user_collected(tmp_path, bundle):
    """The whole safety argument for deleting anything at all."""
    _root, write = bundle
    write(_csv(12))
    cfg = _config()
    with Repository(tmp_path / "fresh.db") as repo:
        seed_bundled_sample(cfg, repo)

        # Rows arriving the way a collection or a user's own import arrives.
        repo.conn.executemany(
            "INSERT INTO originator_listings "
            "(dedupe_key, captured_at, first_seen, last_seen, league, source, "
            " price_chaos, map_tier, corrupted) "
            "VALUES (?,?,?,?,?,?,?,16,0)",
            [
                (f"mine-{i}", "2026-08-20T00:00:00+00:00", "2026-08-20T00:00:00+00:00",
                 "2026-08-20T00:00:00+00:00", "Allflame", source, 55.0)
                for i, source in enumerate(["official_trade_api"] * 5 + ["manual_csv"] * 3)
            ],
        )
        repo.conn.commit()
        assert repo.count_listings("Allflame")["total"] == 20

        write(_csv(30))
        seed_bundled_sample(cfg, repo)

        by_source = dict(
            repo.conn.execute(
                "SELECT source, COUNT(*) FROM originator_listings GROUP BY source"
            ).fetchall()
        )
        assert by_source["official_trade_api"] == 5
        assert by_source["manual_csv"] == 3
        assert by_source[BUNDLED_SOURCE] == 30


def test_declines_for_a_league_the_sample_is_not_from(tmp_path, bundle):
    """Another league's prices must not arrive wearing this league's name."""
    _root, write = bundle
    write(_csv(12), league="Allflame")
    messages: list[str] = []
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_bundled_sample(_config(league="Standard"), repo, log=messages.append) == 0
        assert repo.count_listings("Standard")["total"] == 0
    assert messages and "Allflame" in messages[0]


def test_is_a_no_op_without_a_bundled_sample(tmp_path, bundle):
    """A dev checkout that has deleted the CSV still starts."""
    root, _write = bundle
    (root / seed_module.MODEL_METADATA).write_text(json.dumps({"league": "Allflame"}), "utf-8")
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_bundled_sample(_config(), repo) == 0


def test_divine_rows_convert_at_the_rate_of_their_own_day(tmp_path, bundle):
    """The sample is a snapshot; today's rate is the wrong one to price it at.

    The config carries 999 and the model sidecar says the sample was captured
    when a divine was 200 chaos. A 1-divine listing has to land as 200c.
    """
    _root, write = bundle
    write(_csv(4, currency="divine", amount=1.0), rate=200.0)
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_bundled_sample(_config(divine=999.0), repo) == 4
        prices = repo.usable_prices("Allflame")
    assert prices and all(p == pytest.approx(200.0) for p in prices)


def test_bundled_rows_are_labelled_so_recollect_leaves_them_alone(tmp_path, bundle):
    """scripts/recollect.py deletes source='official_trade_api'."""
    _root, write = bundle
    write(_csv(12))
    with Repository(tmp_path / "fresh.db") as repo:
        seed_bundled_sample(_config(), repo)
        sources = {
            row[0]
            for row in repo.conn.execute("SELECT DISTINCT source FROM originator_listings")
        }
    assert sources == {BUNDLED_SOURCE}
    assert BUNDLED_SOURCE != "official_trade_api"


def test_exporting_a_new_seed_stops_the_next_launch_re_importing_it(tmp_path, bundle, monkeypatch):
    """The maintainer's own database must not re-import the file it just wrote.

    Rebuilding the seed leaves the database it came from holding rows the new
    file also contains, but with a stale fingerprint -- so seeding deletes the
    `bundled_sample` rows, imports the file, and every row that came from a
    collection or a user's own CSV lands a second time. `export_sample` claims
    the fingerprint to close that, and this is the assertion that it does.
    """
    root, write = bundle
    write(_csv(6))
    cfg = _config()
    with Repository(tmp_path / "mine.db") as repo:
        assert seed_bundled_sample(cfg, repo) == 6

        # A rebuilt seed: same rows, different bytes, so a different fingerprint.
        write(_csv(6) + _csv(3, offset=100).split("\n", 1)[1])
        assert repo.get_setting(FINGERPRINT_SETTING) != seed_module._fingerprint(
            root / seed_module.SEED_CSV
        )

        import scripts.export_sample as export_sample

        monkeypatch.setattr(export_sample, "ROOT", root)
        args = type("Args", (), {"seed": True, "db": None})()
        assert export_sample._claim_seed(args, export_sample.SEED_PATH, repo) is True

        before = repo.count_listings("Allflame")["total"]
        assert seed_bundled_sample(cfg, repo) == 0
        assert repo.count_listings("Allflame")["total"] == before


def test_the_small_sample_and_a_foreign_database_never_claim_the_seed(tmp_path, bundle):
    """Only the file the launcher actually imports may be claimed.

    `sample_listings.csv` is a fixture for a fresh checkout and `--db` reads
    somebody else's database, so neither says anything about whether *this*
    database holds the release seed.
    """
    root, write = bundle
    write(_csv(4))
    import scripts.export_sample as export_sample

    with Repository(tmp_path / "mine.db") as repo:
        for args, destination in (
            (type("Args", (), {"seed": False, "db": None})(), export_sample.SAMPLE_PATH),
            (type("Args", (), {"seed": True, "db": "other.db"})(), export_sample.SEED_PATH),
            (type("Args", (), {"seed": True, "db": None})(), "data/somewhere-else.csv"),
        ):
            assert export_sample._claim_seed(args, destination, repo) is False
            assert repo.get_setting(FINGERPRINT_SETTING) is None
