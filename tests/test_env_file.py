"""Writing .env without destroying what is already in it.

The Settings page saves POESESSID and the account name here, into a file that
also holds the contact address, the OAuth pair, and the comments explaining all
of them. So the writer has to be a surgical edit, not a rewrite: an update in
place for keys already present, an append for new ones, and everything else
left exactly as the user typed it.

Every assertion about what a written file means is checked by reading it back
with python-dotenv itself, since that is what actually loads it at runtime.
"""
from __future__ import annotations

import pytest
from dotenv import dotenv_values

from src.env_file import UnwritableValue, read_values, write_values


def test_creates_the_file_when_it_does_not_exist(tmp_path):
    path = tmp_path / ".env"
    write_values(path, {"POESESSID": "abc123"})
    assert dotenv_values(path) == {"POESESSID": "abc123"}


def test_updates_a_key_in_place_without_duplicating_it(tmp_path):
    path = tmp_path / ".env"
    path.write_text("POESESSID=old\nPOE_CONTACT_EMAIL=me@example.com\n", encoding="utf-8")

    write_values(path, {"POESESSID": "new"})

    assert dotenv_values(path)["POESESSID"] == "new"
    assert path.read_text(encoding="utf-8").count("POESESSID") == 1


def test_leaves_other_keys_and_their_order_alone(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "POE_CONTACT_EMAIL=me@example.com\n"
        "POESESSID=old\n"
        "POE_OAUTH_CLIENT_ID=keep-me\n",
        encoding="utf-8",
    )

    write_values(path, {"POESESSID": "new"})

    values = dotenv_values(path)
    assert values["POE_CONTACT_EMAIL"] == "me@example.com"
    assert values["POE_OAUTH_CLIENT_ID"] == "keep-me"
    assert list(values) == ["POE_CONTACT_EMAIL", "POESESSID", "POE_OAUTH_CLIENT_ID"]


def test_keeps_the_comments(tmp_path):
    """.env.example is mostly comments, and they are the documentation."""
    path = tmp_path / ".env"
    original = (
        "# Optional. Only needed for the POESESSID stash adapter.\n"
        "POESESSID=\n"
        "\n"
        "# Your PoE account name, including the discriminator.\n"
        "POE_ACCOUNT_NAME=\n"
    )
    path.write_text(original, encoding="utf-8")

    write_values(path, {"POESESSID": "abc"})

    text = path.read_text(encoding="utf-8")
    assert "# Optional. Only needed for the POESESSID stash adapter." in text
    assert "# Your PoE account name, including the discriminator." in text


def test_appends_a_key_the_file_does_not_have(tmp_path):
    path = tmp_path / ".env"
    path.write_text("POE_CONTACT_EMAIL=me@example.com\n", encoding="utf-8")

    write_values(path, {"POESESSID": "abc"})

    values = dotenv_values(path)
    assert values["POE_CONTACT_EMAIL"] == "me@example.com"
    assert values["POESESSID"] == "abc"


def test_an_empty_value_clears_the_key_but_keeps_the_line(tmp_path):
    """Forgetting a credential should leave the template behind, not a hole."""
    path = tmp_path / ".env"
    path.write_text("POESESSID=secret\n", encoding="utf-8")

    write_values(path, {"POESESSID": ""})

    assert dotenv_values(path)["POESESSID"] in ("", None)
    assert "POESESSID" in path.read_text(encoding="utf-8")


def test_a_file_without_a_trailing_newline_does_not_glue_lines_together(tmp_path):
    path = tmp_path / ".env"
    path.write_text("POE_CONTACT_EMAIL=me@example.com", encoding="utf-8")

    write_values(path, {"POESESSID": "abc"})

    values = dotenv_values(path)
    assert values["POE_CONTACT_EMAIL"] == "me@example.com"
    assert values["POESESSID"] == "abc"


@pytest.mark.parametrize(
    "value",
    [
        "0123456789abcdef0123456789abcdef",  # what a POESESSID actually looks like
        "Exile#1234",                        # # is a comment character
        "Name With Spaces#1",
        'has a " quote',
        "has a \\ backslash",
        "trailing space ",
    ],
)
def test_values_round_trip_through_dotenv(tmp_path, value):
    path = tmp_path / ".env"
    write_values(path, {"POE_ACCOUNT_NAME": value})
    assert dotenv_values(path)["POE_ACCOUNT_NAME"] == value


def test_refuses_a_value_dotenv_would_interpolate(tmp_path):
    """python-dotenv expands ${...} and there is no escape that survives it.

    Writing one anyway would store a value that reads back as something else --
    silently, and only for the unlucky value. Refusing is the honest option;
    neither a session cookie nor an account name contains ${.
    """
    path = tmp_path / ".env"
    with pytest.raises(UnwritableValue):
        write_values(path, {"POESESSID": "ab${HOME}cd"})
    assert not path.exists()


def test_read_values_returns_what_is_in_the_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("POESESSID=abc\n# comment\nPOE_ACCOUNT_NAME=Exile#1\n", encoding="utf-8")

    assert read_values(path) == {"POESESSID": "abc", "POE_ACCOUNT_NAME": "Exile#1"}


def test_read_values_on_a_missing_file_is_empty_not_an_error(tmp_path):
    assert read_values(tmp_path / "nope.env") == {}


def test_writing_twice_is_stable(tmp_path):
    """Save, save again with the same value, and the file should not grow."""
    path = tmp_path / ".env"
    write_values(path, {"POESESSID": "abc"})
    first = path.read_text(encoding="utf-8")
    write_values(path, {"POESESSID": "abc"})
    assert path.read_text(encoding="utf-8") == first
