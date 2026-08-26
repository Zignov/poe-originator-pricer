"""Build the downloadable zip that gets attached to a GitHub release.

    python scripts/package_release.py

Writes `dist/poe-originator-pricer-<version>.zip`, whose single top-level
folder is what the user ends up with after extracting. Inside it, double-
clicking "Start Originator Pricer.bat" is the whole install.

What goes in
------------
Everything git tracks, minus the things that only matter to someone working on
the code (`EXCLUDED_PREFIXES`), plus a short list of files that are gitignored
but that make the download useful on its own:

    models/originator_model.pkl     the trained model
    models/originator_model.json    its metrics and feature schema
    models/affix_catalogue.json     the affix contribution table

Those three are why a downloader gets working prices instead of an empty app
that first wants a 20-minute market collection. They are optional: build on a
machine that has never trained and the zip is still valid, just modelless, and
this script says so rather than failing.

What stays out
--------------
`data/originator.db` (33 MB of collected listings, and one league's snapshot),
`config.yaml` and `.env` -- the last two because they are yours. The launcher
seeds fresh ones from the committed examples on first run.
"""
from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PACKAGE_NAME = "poe-originator-pricer"

#: Tracked paths a downloader has no use for. Matched as path prefixes against
#: git's forward-slash output.
EXCLUDED_PREFIXES = (
    ".claude/",
    ".devcontainer/",
    ".agents/",
    "Claude Code Prompt",
)

#: Gitignored, included when present. See the module docstring.
OPTIONAL_EXTRAS = (
    "models/originator_model.pkl",
    "models/originator_model.json",
    "models/affix_catalogue.json",
)


def version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("No version = \"...\" found in pyproject.toml")
    return match.group(1)


def tracked_files() -> list[str]:
    # quotepath=off: without it git renders the em dash in one tracked
    # filename as octal escapes inside quotes, which then matches no prefix
    # and names no file on disk.
    out = subprocess.run(
        ["git", "-c", "core.quotepath=off", "ls-files"],
        cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout
    return [
        line for line in out.splitlines()
        if line and not line.startswith(EXCLUDED_PREFIXES)
    ]


def main() -> int:
    tag = f"{PACKAGE_NAME}-{version()}"
    DIST.mkdir(exist_ok=True)
    target = DIST / f"{tag}.zip"

    members = tracked_files()
    extras = [name for name in OPTIONAL_EXTRAS if (ROOT / name).exists()]
    missing = [name for name in OPTIONAL_EXTRAS if name not in extras]

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in members + extras:
            archive.write(ROOT / name, f"{tag}/{name}")

    size_mb = target.stat().st_size / 1_048_576
    print(f"{target.relative_to(ROOT)}  ({size_mb:.1f} MB, {len(members) + len(extras)} files)")
    if extras:
        print("  bundled a trained model: " + ", ".join(extras))
    if missing:
        print("  NOT bundled (train first if you want prices out of the box):")
        for name in missing:
            print(f"    {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
