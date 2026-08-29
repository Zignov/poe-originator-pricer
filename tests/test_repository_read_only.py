"""`Repository.read_only` is what lets a script look at a database it must not touch."""
from __future__ import annotations

import sqlite3

import pytest

from src.database.repository import Repository


@pytest.fixture
def existing(tmp_path):
    path = tmp_path / "live.db"
    with Repository(path) as repo:
        repo.set_setting("exalted_to_chaos", 1.25)
    return path


def test_reads_an_existing_database(existing):
    with Repository.read_only(existing) as repo:
        assert repo.get_setting("exalted_to_chaos") == 1.25


def test_a_write_raises_instead_of_landing_in_someone_elses_data(existing):
    with Repository.read_only(existing) as repo:
        with pytest.raises(sqlite3.OperationalError):
            repo.set_setting("exalted_to_chaos", 99.0)
    with Repository.read_only(existing) as repo:
        assert repo.get_setting("exalted_to_chaos") == 1.25


def test_refuses_a_database_that_is_not_there(tmp_path):
    """The normal constructor would create one, which is the opposite of the point."""
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        Repository.read_only(missing)
    assert not missing.exists()


def test_does_not_migrate_what_it_opens(tmp_path):
    """Opening a database read-only must not try to write a schema into it."""
    path = tmp_path / "bare.db"
    sqlite3.connect(path).close()
    with Repository.read_only(path) as repo:
        tables = {
            row[0]
            for row in repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables == set()
