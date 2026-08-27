"""Read and write `.env` in place.

The Settings page persists POESESSID and the account name here. That file is
not ours alone -- it also carries the contact address, the OAuth pair and the
comments that explain them -- so writing it means editing the lines we own and
leaving every other byte where it was. Rewriting the file from a dict would
throw away the comments, which in a file seeded from `.env.example` are most
of it.

`.env` is gitignored and never leaves the machine. It is still plain text: this
module makes a credential persist, it does not make it secret.
"""
from __future__ import annotations

import re
from pathlib import Path

#: Matches `KEY=value`, tolerating leading whitespace and an `export ` prefix,
#: which dotenv accepts and someone's hand-written file may well use.
_ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=)(.*)$")


class UnwritableValue(ValueError):
    """A value that cannot survive a round trip through python-dotenv."""


def _quote(value: str) -> str:
    r"""Render a value as a double-quoted .env literal.

    Double quotes rather than single: single-quoted dotenv values have no
    escape for an embedded apostrophe, and `it's` makes the parser give up on
    the line entirely. Inside double quotes, `\` and `"` both round trip once
    escaped -- verified against python-dotenv in tests/test_env_file.py, which
    reads every written value back with dotenv itself rather than trusting this
    docstring.
    """
    if "${" in value:
        # dotenv interpolates ${NAME} even inside quotes, and `\$` does not
        # stop it -- it interpolates anyway and leaves the backslash behind.
        # With no escape that works, storing this would mean storing something
        # that reads back as a different string.
        raise UnwritableValue(
            "A value containing '${' cannot be stored in .env: python-dotenv "
            "would expand it into something else when reading it back."
        )
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_values(path: str | Path) -> dict[str, str]:
    """The assignments in the file, as a plain dict. Missing file reads empty."""
    from dotenv import dotenv_values

    path = Path(path)
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def write_values(path: str | Path, values: dict[str, str]) -> Path:
    """Set each key in `values`, updating in place and appending what is new.

    An empty string clears a key while keeping its line, so forgetting a
    credential leaves the template behind rather than a hole where the reader
    of the file expected a setting.
    """
    path = Path(path)
    rendered = {key: _quote(value) for key, value in values.items()}

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()

    remaining = dict(rendered)
    for index, line in enumerate(lines):
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        prefix, key, equals, _old = match.groups()
        if key in remaining:
            lines[index] = f"{prefix}{key}{equals}{remaining.pop(key)}"

    for key, literal in remaining.items():
        lines.append(f"{key}={literal}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
