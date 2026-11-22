"""Saving POESESSID from the Settings page has to actually persist it.

The page used to say, accurately, that it never wrote the credential to disk;
you had to hand-edit .env to keep it. These tests drive the real page through
Streamlit's AppTest so that "Save" is exercised as a click, not as a call to
the helper underneath it -- the helper being right while the button is wired to
nothing is exactly the failure worth catching.

Every test redirects ENV_PATH into tmp_path first. A test that wrote the real
.env would overwrite the developer's own credentials.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

import src.ui.settings as settings

APP = str(Path(__file__).resolve().parent.parent / "app.py")

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings, "ENV_PATH", path)
    return path


def _settings_page(tab="Account", **session):
    app = AppTest.from_file(APP, default_timeout=120)
    # Settings renders only the open tab, so the credential fields do not exist
    # until the strip is on Account. Seeded through the mirror key tab_strip()
    # restores from, which is what a user coming back to the page would hit.
    app.session_state["tab_strip::settings_tab"] = tab
    for key, value in session.items():
        app.session_state[key] = value
    app.run()
    app.sidebar.radio[0].set_value("Settings").run()
    assert not app.exception, f"Settings raised: {[str(e) for e in app.exception]}"
    return app


def _button(app, label):
    matches = [b for b in app.button if label in str(b.label)]
    assert matches, f"no button labelled {label!r}; saw {[str(b.label) for b in app.button]}"
    return matches[0]


def test_save_writes_the_credentials_to_the_env_file(env_path):
    app = _settings_page(poesessid="abc123", account_name="Exile#1234")

    _button(app, "Save to .env").click().run()
    assert not app.exception, [str(e) for e in app.exception]

    assert env_path.exists(), "Save did not create the file"
    values = dotenv_values(env_path)
    assert values["POESESSID"] == "abc123"
    assert values["POE_ACCOUNT_NAME"] == "Exile#1234"


def test_a_saved_credential_comes_back_on_the_next_run(env_path):
    """The point of the feature: start the app again and it is still there."""
    env_path.write_text('POESESSID="abc123"\nPOE_ACCOUNT_NAME="Exile#1234"\n', encoding="utf-8")

    # No session state, exactly like a freshly started app.
    app = _settings_page()

    values = [str(i.value) for i in app.text_input]
    assert "abc123" in values, f"POESESSID not restored; fields were {values}"
    assert "Exile#1234" in values, f"account name not restored; fields were {values}"


def test_forget_clears_what_was_saved(env_path):
    env_path.write_text('POESESSID="abc123"\nPOE_ACCOUNT_NAME="Exile#1234"\n', encoding="utf-8")

    app = _settings_page()
    _button(app, "Forget").click().run()
    assert not app.exception, [str(e) for e in app.exception]

    assert dotenv_values(env_path).get("POESESSID") in ("", None)


def test_saving_does_not_disturb_the_rest_of_the_file(env_path):
    """.env also holds the contact address and the OAuth pair."""
    env_path.write_text(
        "# keep this comment\n"
        "POE_CONTACT_EMAIL=me@example.com\n"
        "POE_OAUTH_CLIENT_ID=keep-me\n",
        encoding="utf-8",
    )

    app = _settings_page(poesessid="abc123", account_name="Exile#1234")
    _button(app, "Save to .env").click().run()

    text = env_path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    values = dotenv_values(env_path)
    assert values["POE_CONTACT_EMAIL"] == "me@example.com"
    assert values["POE_OAUTH_CLIENT_ID"] == "keep-me"
    assert values["POESESSID"] == "abc123"


def test_the_page_does_not_claim_the_credential_stays_off_disk(env_path):
    """The old copy promised the opposite of what the button now does."""
    app = _settings_page()
    rendered = " ".join(str(m.value) for m in app.markdown) + " ".join(
        str(c.value) for c in app.caption
    )
    assert "not written to disk" not in rendered
