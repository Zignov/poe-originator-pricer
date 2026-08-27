"""Persisted app settings.

The Exalted Orb price is a fetched fact, not a preference -- the sibling
divine-rate button writes its result to the database, and the orb price used to
write nowhere at all, so it reverted to the config default on every restart and
the refresh button looked broken.
"""
from __future__ import annotations

import pytest

from src.database.repository import Repository


@pytest.fixture
def repo(tmp_path) -> Repository:
    r = Repository(tmp_path / "settings.db")
    yield r
    r.close()


def test_a_missing_setting_returns_the_default(repo):
    assert repo.get_setting("exalted_to_chaos") is None
    assert repo.get_setting("exalted_to_chaos", 10.0) == 10.0


def test_a_setting_survives_being_written_and_read(repo):
    repo.set_setting("exalted_to_chaos", 4.0)
    assert repo.get_setting("exalted_to_chaos") == 4.0


def test_writing_the_same_key_replaces_rather_than_duplicates(repo):
    repo.set_setting("exalted_to_chaos", 4.0)
    repo.set_setting("exalted_to_chaos", 3.0)
    assert repo.get_setting("exalted_to_chaos") == 3.0


def test_a_setting_survives_reopening_the_database(tmp_path):
    """The actual complaint: the value must still be there after a restart."""
    path = tmp_path / "settings.db"
    with Repository(path) as first:
        first.set_setting("exalted_to_chaos", 4.0)
    with Repository(path) as second:
        assert second.get_setting("exalted_to_chaos") == 4.0


def test_settings_are_stored_per_key_not_overwritten_by_neighbours(repo):
    repo.set_setting("exalted_to_chaos", 4.0)
    repo.set_setting("something_else", 99.0)
    assert repo.get_setting("exalted_to_chaos") == 4.0
    assert repo.get_setting("something_else") == 99.0


def test_an_older_database_is_migrated_forward_rather_than_left_broken(tmp_path):
    """The real case: a database created before app_settings existed.

    Opening a *fresh* database proves nothing here -- it gets every migration at
    once. This builds one stopped at version 1, the way the working database on
    disk already was, and that is the state where putting the table into the
    released migration 1 instead of a new one fails.
    """
    from src.database.models import MIGRATIONS, connect

    path = tmp_path / "old.db"
    conn = connect(path)
    version, sql = MIGRATIONS[0]
    conn.executescript(sql)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.commit()
    conn.execute("SELECT 1 FROM currency_rates")  # v1 tables are there
    with pytest.raises(Exception):
        conn.execute("SELECT 1 FROM app_settings")  # v2 table is not
    conn.close()

    with Repository(path) as repo:
        repo.set_setting("exalted_to_chaos", 4.0)
        assert repo.get_setting("exalted_to_chaos") == 4.0


def test_migrating_forward_keeps_existing_data(tmp_path):
    path = tmp_path / "existing.db"
    with Repository(path) as first:
        first.set_divine_rate("Allflame", 222.5, source="test")
    with Repository(path) as second:
        second.set_setting("exalted_to_chaos", 4.0)
        assert second.get_divine_rate("Allflame")[0] == 222.5
        assert second.get_setting("exalted_to_chaos") == 4.0


# ---------------------------------------------------------------------------
# The version, and the one place it is written
# ---------------------------------------------------------------------------
def test_the_user_agent_version_is_the_one_in_pyproject():
    """These were two literals once, and they drifted at the first release.

    0.2.0 shipped announcing itself to GGG as 0.1.0. That is not cosmetic: the
    User-Agent is the only thing identifying this client to a rate limiter, and
    a version that never moves is what a badly-behaved scraper looks like.
    """
    import re

    from src.config import ROOT, VERSION

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE).group(1)
    assert VERSION == declared


def test_the_user_agent_carries_the_contact_and_the_version():
    from src.config import Config, user_agent

    agent = user_agent(Config({"http": {"user_agent_contact": "someone@example.com"}}))
    assert agent.startswith("OAuth poe-originator-pricer/")
    assert "someone@example.com" in agent
    assert "unknown" not in agent, "the version did not resolve from pyproject.toml"
