"""The first-run seed: what it fills, and everyone it declines to touch."""
from __future__ import annotations

import json

import pytest

from src.config import Config, load_config
from src.database.repository import Repository
from src.market import seed as seed_module
from src.market.seed import seed_if_empty
from src.schema import FEATURE_NAMES

CSV_HEADER = (
    ",".join(FEATURE_NAMES)
    + ",price_amount,price_currency,map_tier,item_level,corrupted,seller,listing_id,indexed_at"
)


def _csv(rows: int, currency: str = "chaos", amount: float = 40.0) -> str:
    lines = [CSV_HEADER]
    for i in range(rows):
        features = ",".join(str(50 + i) for _ in FEATURE_NAMES)
        lines.append(
            f"{features},{amount},{currency},16,84,false,,,2026-08-0{i % 9 + 1}T12:00:00+00:00"
        )
    return "\n".join(lines) + "\n"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A stand-in release layout: a seed CSV and a model sidecar beside it."""
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


def test_fills_an_empty_database(tmp_path, bundle):
    root, write = bundle
    write(_csv(12))
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_if_empty(_config(), repo) == 12
        assert repo.count_listings("Allflame")["total"] == 12


def test_declines_when_anything_is_already_stored(tmp_path, bundle):
    """The guard that stops a seed from re-appearing after a prune."""
    root, write = bundle
    write(_csv(12))
    cfg = _config()
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_if_empty(cfg, repo) == 12
        assert seed_if_empty(cfg, repo) == 0
        assert repo.count_listings("Allflame")["total"] == 12


def test_declines_for_a_league_the_sample_is_not_from(tmp_path, bundle):
    """Another league's prices must not arrive wearing this league's name."""
    root, write = bundle
    write(_csv(12), league="Allflame")
    messages: list[str] = []
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_if_empty(_config(league="Standard"), repo, log=messages.append) == 0
        assert repo.count_listings("Standard")["total"] == 0
    assert messages and "Allflame" in messages[0]


def test_is_a_no_op_without_a_bundled_sample(tmp_path, bundle):
    """A dev checkout that has deleted the CSV still starts."""
    root, write = bundle
    (root / seed_module.MODEL_METADATA).write_text(json.dumps({"league": "Allflame"}), "utf-8")
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_if_empty(_config(), repo) == 0


def test_divine_rows_convert_at_the_rate_of_their_own_day(tmp_path, bundle):
    """The sample is a snapshot; today's rate is the wrong one to price it at.

    The config carries 999 and the model sidecar says the sample was captured
    when a divine was 200 chaos. A 1-divine listing has to land as 200c.
    """
    root, write = bundle
    write(_csv(4, currency="divine", amount=1.0), rate=200.0)
    with Repository(tmp_path / "fresh.db") as repo:
        assert seed_if_empty(_config(divine=999.0), repo) == 4
        prices = repo.usable_prices("Allflame")
    assert prices and all(p == pytest.approx(200.0) for p in prices)
