#!/usr/bin/env bash
# macOS / Linux counterpart of "Start Originator Pricer.bat". Same contract:
# build a virtual environment beside this script on first run, reuse it after,
# then hand off to scripts/launch.py.
#
# The web app itself is portable; the desktop overlay (README section 7) is
# Windows-only and simply will not offer itself here.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
VPY="$VENV/bin/python"
STAMP="$VENV/.deps-stamp"

echo
echo "  PoE Originator Map Stash Pricer"
echo "  ==============================="
echo

find_python() {
    for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x "$VPY" ]; then
    if ! BASE_PY="$(find_python)"; then
        echo "  No Python 3.11 or newer was found. Install it from https://www.python.org/downloads/"
        exit 1
    fi
    echo "  Creating a private Python environment in $VENV ..."
    "$BASE_PY" -m venv "$VENV"
    echo
fi

want_hash="$("$VPY" -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())")"
have_hash="$(cat "$STAMP" 2>/dev/null || true)"

if [ "$want_hash" != "$have_hash" ]; then
    echo "  Installing dependencies. The first run takes several minutes."
    echo
    "$VPY" -m pip install --upgrade pip --quiet --disable-pip-version-check
    "$VPY" -m pip install -r requirements.txt --disable-pip-version-check
    printf '%s' "$want_hash" > "$STAMP"
    echo
fi

exec "$VPY" scripts/launch.py
