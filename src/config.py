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
        "retrain_interval_hours": 24,
    },
    "display": {"currency": "chaos"},
    "currency": {"divine_to_chaos": 220.0, "exalted_to_chaos": 10.0},
    "market": {
        "max_age_days": 7,
        "plausible_price_chaos": [5, 1000],
        "typical_price_chaos": [30, 660],
        "recency_weights": [[12, 1.0], [24, 0.9], [72, 0.7], [168, 0.4], [999999, 0.2]],
    },
    "pricing": {"strategies": {"quick_sell": 0.94, "market": 0.99, "patient": 1.06}},
    "scope": {"map_tier": 16, "require_uncorrupted": True},
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
VERSION = "0.1.0"


def user_agent(cfg: Config) -> str:
    contact = cfg.get("http.user_agent_contact", "unset@example.com")
    return USER_AGENT_TEMPLATE.format(version=VERSION, contact=contact)
