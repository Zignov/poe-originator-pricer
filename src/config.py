"""Configuration loading.

Reads config.yaml if present, otherwise config.example.yaml, then overlays
anything set in the environment / .env.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a convenience only
    def load_dotenv(*_a, **_k):
        return False

ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS: dict[str, Any] = {
    "league": "Allflame",
    "realm": "pc",
    "database": {"path": "data/originator.db"},
    "model": {
        "directory": "models",
        "backends": ["catboost", "catboost_mae", "lightgbm", "lightgbm_mae", "xgboost", "sklearn_hgb"],
        "target": "log1p",
    },
    "collection": {
        # Passive mode: scan, go quiet long enough for the rate limit to forget
        # the scan, scan again. See src/market/passive.py. The cooldown is
        # politeness, not safety -- RateLimiter obeys GGG's own headers
        # unconditionally and would allow this far more often.
        "passive": {
            "maps_per_scan": 400,
            "cooldown_minutes": 30,
            "auto_off_after_hours": 0,
            "auto_retrain": True,
            "retrain_every_scans": 4,
        }
    },
    "display": {"currency": "chaos"},
    "currency": {"divine_to_chaos": 210.0, "exalted_to_chaos": 1.25},
    "market": {
        "max_age_days": 7,
        "plausible_price_chaos": [5, 1000],
        "typical_price_chaos": [30, 660],
        "recency_weights": [[12, 1.0], [24, 0.9], [72, 0.7], [168, 0.4], [999999, 0.2]],
    },
    "market_comparison": {
        "tolerance_schedule": [1.0, 2.0, 4.0],
        "min_comparables": 6,
        "max_comparables": 30,
        "request_budget": 200,
        "cache_ttl_minutes": 30,
        "max_listing_age_hours": 48,
        "status": "online",
        "cache_path": "data/market_comparison_cache.json",
    },
    "pricing": {"strategies": {"quick_sell": 0.94, "market": 0.99, "patient": 1.06}},
    "scope": {"map_tier": 16, "require_uncorrupted": True},
    # The desktop overlay's calibration. No `screen` key by default, and that
    # absence is what marks it as never calibrated -- see
    # src/overlay/calibration.py.
    "overlay": {
        "origin": [0, 0],
        "cell_px": 53.4,
        "grid": [12, 12],
        "opacity": 0.55,
        "monitor": 0,
        "hotkey_toggle": "ctrl+alt+o",
        "hotkey_analyze": "ctrl+alt+a",
    },
    # The same block again for the inventory panel, which sits on the other
    # side of the screen and is 12x5 rather than 12x12, so it cannot share a
    # single calibration. Its own chord, because both overlays can be up at
    # once and one key toggling both would defeat the point of having two.
    "overlay_inventory": {
        "origin": [0, 0],
        "cell_px": 53.4,
        "grid": [12, 5],
        "opacity": 0.55,
        "monitor": 0,
        "hotkey_toggle": "ctrl+alt+i",
        "hotkey_analyze": "ctrl+alt+shift+a",
    },
    # Watching your own instant-buyout listings: what you asked, how long it
    # sat, and what that says the price should be. Off until POE_ACCOUNT_NAME is
    # set, because there is nothing to poll without it.
    "listing_tracker": {
        "enabled": False,
        "poll_interval_minutes": 15,
        # A listing this new is polled on the fast cadence. Its first hour holds
        # the fast-sale signal; a three-day-old map does not need a five-minute
        # check to confirm it is still sitting.
        "young_listing_minutes": 60,
        "young_poll_interval_minutes": 5,
        # A "sold" map that comes back this soon was never sold -- a sold map
        # cannot return. Beyond the window it is treated as a different map.
        "reappearance_window_hours": 6,
        # Page filter: what counts as "sitting".
        "stale_after_hours": 24,
        # Seeds the fast mark until there are enough of your own sales to take
        # quartiles from, which is when it is replaced by what your maps do.
        "fast_sale_hours": 1,
        # W in "sells within W": the window the fit is asked about, and the
        # window the reprice recommendation aims at.
        "target_window_hours": 24,
        "target_sell_probability": 0.6,
        "calibration": {"enabled": True, "min_sold": 20, "clamp": [0.8, 1.3]},
        "ladder": {
            "steps": [[24, 0.92], [72, 0.85], [168, 0.78]],
            "floor_ratio": 0.75,
        },
    },
    "http": {"user_agent_contact": "unset@example.com", "min_request_interval_seconds": 2.0},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict[str, Any]):
        self._d = data

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted lookup, e.g. cfg.get('model.target')."""
        node: Any = self._d
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self._d
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def as_dict(self) -> dict:
        return copy.deepcopy(self._d)

    # -- derived paths -------------------------------------------------
    @property
    def db_path(self) -> Path:
        return (ROOT / self.get("database.path", "data/originator.db")).resolve()

    @property
    def model_dir(self) -> Path:
        p = (ROOT / self.get("model.directory", "models")).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self, path: Path | None = None) -> Path:
        path = path or (ROOT / "config.yaml")
        path.write_text(yaml.safe_dump(self._d, sort_keys=False), encoding="utf-8")
        return path


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    if path is not None:
        src = Path(path)
    elif (ROOT / "config.yaml").exists():
        src = ROOT / "config.yaml"
    else:
        src = ROOT / "config.example.yaml"

    data = _DEFAULTS
    if src.exists():
        loaded = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        data = _deep_merge(_DEFAULTS, loaded)

    cfg = Config(data)
    if os.getenv("POE_LEAGUE"):
        cfg.set("league", os.environ["POE_LEAGUE"])
    if os.getenv("POE_CONTACT_EMAIL"):
        cfg.set("http.user_agent_contact", os.environ["POE_CONTACT_EMAIL"])
    return cfg


#: GGG's developer docs require the form
#: "OAuth {clientId}/{version} (contact: {contact})".
#: No OAuth client id exists for this tool -- GGG is not accepting new
#: applications -- so the client name takes that slot, which is what community
#: tools do. The contact address is real and required.
USER_AGENT_TEMPLATE = "OAuth poe-originator-pricer/{version} (contact: {contact})"


def _version() -> str:
    """The version, read from pyproject.toml -- the one place it is written.

    It used to be a literal here as well, and the two drifted the first time
    anybody cut a release: 0.2.0 shipped announcing itself to GGG as 0.1.0.
    That is not cosmetic. The User-Agent is the only thing identifying this
    client to a rate limiter, and a version that never changes is exactly what
    a badly-behaved scraper looks like.

    pyproject.toml is in the release zip as well as in a checkout, so this
    resolves in both. The fallbacks are for neither -- an odd install with no
    metadata -- and deliberately do not guess a number, because a wrong version
    is worse than an honest "unknown".
    """
    try:
        import tomllib

        with (ROOT / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        pass
    try:
        from importlib.metadata import version as installed_version

        return installed_version("poe-originator-pricer")
    except Exception:
        return "unknown"


VERSION = _version()


def user_agent(cfg: Config) -> str:
    contact = cfg.get("http.user_agent_contact", "unset@example.com")
    return USER_AGENT_TEMPLATE.format(version=VERSION, contact=contact)
