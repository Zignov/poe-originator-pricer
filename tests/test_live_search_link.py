"""Live search is handed to GGG's page rather than reimplemented.

Live search is the right answer to lots vanishing in under a minute, and it is
deliberately not built here. It runs on a WebSocket endpoint that appears nowhere
in GGG's developer documentation -- which lists every endpoint they publish and
contains no real-time interface at all -- over a standing authenticated
connection, continuously. That combination is reverse-engineering an undocumented
endpoint plus exactly the automated monitoring the Terms of Use prohibit, and the
account risk would land on the user.

GGG's own trade page has an Activate Live Search button, so the app builds the
query and hands it over.
"""
from __future__ import annotations

import json
import urllib.parse

from src.config import load_config
from src.ui.bulk_buy import live_search_url


def _decode(url: str) -> dict:
    return json.loads(urllib.parse.unquote(url.split("?q=", 1)[1]))


def test_the_link_points_at_ggg_and_not_at_a_socket():
    url = live_search_url(load_config(), 15.0)
    assert url.startswith("https://www.pathofexile.com/trade/search/")
    assert "ws://" not in url and "wss://" not in url


def test_the_link_carries_the_scan_price_ceiling():
    query = _decode(live_search_url(load_config(), 12.0))
    price = query["query"]["filters"]["trade_filters"]["filters"]["price"]
    assert price["max"] == 12.0


def test_the_link_keeps_the_scope_the_rest_of_the_app_uses():
    cfg = load_config()
    query = _decode(live_search_url(cfg, 15.0))
    tier = query["query"]["filters"]["map_filters"]["filters"]["map_tier"]
    assert tier["min"] == cfg.get("scope.map_tier")
    stat_ids = [f["id"] for group in query["query"]["stats"] for f in group["filters"]]
    assert any("2696470877" in s for s in stat_ids), "the Originator implicit must be kept"


def test_the_link_is_uncollapsed_so_a_seller_shows_every_map():
    query = _decode(live_search_url(load_config(), 15.0))
    trade = query["query"]["filters"]["trade_filters"]["filters"]
    assert "collapse" not in trade


def test_no_websocket_client_exists_in_the_codebase():
    """A standing guard: this is a deliberate omission, not an oversight."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src"
    offenders = [
        path.name
        for path in root.rglob("*.py")
        if "websocket" in path.read_text(encoding="utf-8").lower()
        or "wss://" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"a live-search socket client appeared in {offenders}"
