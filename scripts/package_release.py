"""Build the downloadable zip that gets attached to a GitHub release.

    python scripts/package_release.py

Writes `dist/poe-originator-pricer-<version>.zip`, whose single top-level
folder is what the user ends up with after extracting. Inside it, double-
clicking "Start Originator Pricer.bat" is the whole install.

What goes in
------------
Everything git tracks, minus the things that only matter to someone working on
the code (`EXCLUDED_PREFIXES`).

That now includes the trained model, because as of 0.2.0 these three are in the
repo rather than gitignored:

    models/originator_model.pkl     the trained model
    models/originator_model.json    its metrics and feature schema
    models/affix_catalogue.json     the affix contribution table

They are why a downloader gets working prices instead of an empty app that
first wants a 20-minute market collection, and tracking them means any
checkout builds an identical zip -- before, only the machine that had trained
could build a useful one. `REQUIRED_ARTIFACTS` re-checks they are really there,
because a zip that is quietly modelless is the failure worth catching here
rather than in someone's download.

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

#: Tracked, and the zip is not worth publishing without them. See the module
#: docstring.
REQUIRED_ARTIFACTS = (
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

    # Tracked but deleted from the working tree is a state git will happily
    # report, and archive.write() would only discover it as a FileNotFoundError
    # halfway through writing the zip.
    absent = [name for name in members if not (ROOT / name).exists()]
    if absent:
        raise SystemExit(
            "Tracked files are missing from the working tree:\n  "
            + "\n  ".join(absent)
            + "\nRestore them (git checkout -- <path>) before building."
        )

    modelless = [name for name in REQUIRED_ARTIFACTS if name not in members]
    if modelless:
        raise SystemExit(
            "These are meant to be tracked and are not:\n  "
            + "\n  ".join(modelless)
            + "\nWithout them the download prices nothing until the user spends "
              "20 minutes collecting a market. Train (python -m src.cli train), "
              "commit the artifacts, then build."
        )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in members:
            archive.write(ROOT / name, f"{tag}/{name}")

    size_mb = target.stat().st_size / 1_048_576
    print(f"{target.relative_to(ROOT)}  ({size_mb:.1f} MB, {len(members)} files)")
    print("  trained model bundled: " + ", ".join(REQUIRED_ARTIFACTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
