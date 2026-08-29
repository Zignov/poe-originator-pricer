"""Start the Streamlit app and open a browser at it.

Run by the double-click launchers (`Start Originator Pricer.bat`, `start.sh`)
once they have a virtual environment with the requirements in it. Doing the
last mile in Python rather than in batch buys two things the shell makes
awkward: picking a free port when 8501 is taken, and opening the browser only
after the server is actually answering instead of after a guessed sleep.

`.streamlit/config.toml` sets `server.headless = true`, so Streamlit will not
open a browser by itself -- that is deliberate, and this script is the other
half of it.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST_PORT = 8501
PORT_ATTEMPTS = 20
#: Cold start pulls in pandas, sklearn and whichever boosting backends are
#: installed. On a slow disk the first import pass has taken most of a minute.
HEALTH_TIMEOUT_S = 120.0


def seed_local_files() -> None:
    """Create config.yaml / .env from their examples on a first run.

    Neither is in the repo (config.yaml carries your contact address, .env a
    session credential), and src/config.py falls back to config.example.yaml,
    so this is a convenience: it gives you a file to edit rather than one to
    create from scratch. Never overwrites.
    """
    pairs = (
        (ROOT / "config.example.yaml", ROOT / "config.yaml"),
        (ROOT / ".env.example", ROOT / ".env"),
    )
    for example, real in pairs:
        if example.exists() and not real.exists():
            shutil.copyfile(example, real)
            print(f"  created {real.name} from {example.name}")


def seed_database() -> None:
    """Load the market the bundled model was trained on into the database.

    Runs on a first install and again after an upgrade that ships a newer
    sample; every other run finds the fingerprint already stored and does
    nothing. The app does this too, on the first page load -- but silently, and
    after a 30-second cold start. Doing it here means the run it happens on says
    so on the console, where a first-time downloader is already looking.

    Imported inside the function because it pulls in the whole data stack.
    """
    # `python scripts/launch.py` puts scripts/ on the path, not the repo root,
    # so `src` is not importable without this.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from src.config import load_config
    from src.database.repository import Repository
    from src.market.seed import seed_bundled_sample

    cfg = load_config()
    with Repository(cfg.db_path) as repo:
        seed_bundled_sample(cfg, repo, log=lambda message: print(f"  {message}"))


def port_is_taken(port: int) -> bool:
    """Is something already listening here?

    Answered by *connecting*, not by trying to bind. Binding is the obvious
    test and it is wrong on Windows: Streamlit's server sets SO_REUSEADDR, and
    Windows then happily lets a second socket bind the same port, so a bind
    probe reports a busy 8501 as free -- after which Streamlit itself refuses
    it with "Port 8501 is not available" and the app dies on startup.
    SO_EXCLUSIVEADDRUSE on the probe does not close that hole either.

    A successful connection is unambiguous: someone is serving there.
    """
    for family, address in ((socket.AF_INET, ("127.0.0.1", port)),
                            (socket.AF_INET6, ("::1", port))):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.4)
                if probe.connect_ex(address) == 0:
                    return True
        except OSError:
            continue
    return False


def free_port(start: int = FIRST_PORT) -> int:
    for port in range(start, start + PORT_ATTEMPTS):
        if not port_is_taken(port):
            return port
    raise SystemExit(
        f"No free port between {start} and {start + PORT_ATTEMPTS - 1}. "
        "Close whatever is using them and try again."
    )


def wait_until_serving(url: str, process: subprocess.Popen, timeout: float) -> bool:
    """Poll Streamlit's health endpoint until it answers, or the server dies."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def main() -> int:
    seed_local_files()
    seed_database()

    port = free_port()
    address = f"http://localhost:{port}"
    command = [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
        "--server.port", str(port),
        "--server.headless", "true",
    ]

    print(f"\n  Starting Originator Pricer on {address}")
    print("  First start takes a moment while the model libraries load.")
    print("  Close this window to stop the app.\n")

    process = subprocess.Popen(command, cwd=str(ROOT))
    try:
        if wait_until_serving(f"{address}/_stcore/health", process, HEALTH_TIMEOUT_S):
            webbrowser.open(address)
        elif process.poll() is None:
            print(f"  Server is slow to start. Open {address} yourself if it does not appear.")
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
