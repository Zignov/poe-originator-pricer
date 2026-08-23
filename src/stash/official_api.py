"""Reading private stash tabs from pathofexile.com.

Two adapters, one interface.

SessionStashSource
    Uses the POESESSID cookie from the user's own browser session against
    /character-window/get-stash-items. This is the user's own credential for
    their own account, sent to GGG's own site -- no access control is bypassed
    and nothing is spoofed. It is what is actually usable today, because the
    official OAuth API (below) needs a client GGG registers by hand.

OAuthStashSource
    The officially supported route: /api/profile and /api/stash/{league} with a
    Bearer token carrying the account:stashes scope. Implemented behind the same
    interface, but NOT verified -- registering a client requires GGG approval and
    no credentials were available. Treat it as scaffolding until it is exercised
    against a real client_id. See docs/data-access.md.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from src.schema import NormalizedMap
from src.stash.base import StashSource, StashTab, items_from_api_payload, only_maps

LEGACY_BASE = "https://www.pathofexile.com/character-window/get-stash-items"
OAUTH_API_BASE = "https://api.pathofexile.com"


class StashAccessError(RuntimeError):
    pass


class SessionStashSource(StashSource):
    name = "poesessid"
    setup_hint = (
        "Log in to pathofexile.com in your browser, open developer tools -> "
        "Application -> Cookies, and copy the value of POESESSID into .env or "
        "the Settings page. It is your own session for your own account; treat "
        "it like a password and never share it."
    )

    def __init__(
        self,
        poesessid: str | None = None,
        account_name: str | None = None,
        user_agent: str = "poe-originator-pricer/0.1",
        realm: str = "pc",
        min_interval: float = 1.5,
        map_tier: int = 16,
        require_uncorrupted: bool = True,
        session: requests.Session | None = None,
    ):
        self.poesessid = poesessid or os.getenv("POESESSID", "")
        self.account_name = account_name or os.getenv("POE_ACCOUNT_NAME", "")
        self.realm = realm
        self.min_interval = min_interval
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted
        self._last_call = 0.0
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        if self.poesessid:
            self.session.cookies.set("POESESSID", self.poesessid, domain=".pathofexile.com")

    def available(self) -> tuple[bool, str]:
        if not self.poesessid:
            return False, "POESESSID is not set"
        if not self.account_name:
            return False, "POE_ACCOUNT_NAME is not set (include the #1234 discriminator)"
        return True, ""

    def _throttle(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        ok, why = self.available()
        if not ok:
            raise StashAccessError(why)
        self._throttle()
        resp = self.session.get(LEGACY_BASE, params=params, timeout=30)
        if resp.status_code == 403:
            raise StashAccessError(
                "GGG returned 403. The POESESSID is usually expired -- log in again "
                "and copy a fresh one."
            )
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After", "60")
            raise StashAccessError(f"rate limited by GGG; retry in {retry}s")
        if resp.status_code != 200:
            raise StashAccessError(f"stash request returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise StashAccessError(f"stash response was not JSON: {exc}") from exc

    def list_tabs(self, league: str) -> list[StashTab]:
        data = self._get(
            {
                "accountName": self.account_name,
                "realm": self.realm,
                "league": league,
                "tabs": 1,
                "tabIndex": 0,
            }
        )
        tabs: list[StashTab] = []
        for entry in data.get("tabs", []) or []:
            tabs.append(
                StashTab(
                    id=str(entry.get("id") or entry.get("i")),
                    name=entry.get("n") or entry.get("name") or "",
                    index=int(entry.get("i", len(tabs))),
                    type=entry.get("type", ""),
                    colour=_colour_of(entry),
                    raw=entry,
                )
            )
        return tabs

    def fetch_tab(self, league: str, tab: StashTab) -> list[NormalizedMap]:
        data = self._get(
            {
                "accountName": self.account_name,
                "realm": self.realm,
                "league": league,
                "tabs": 0,
                "tabIndex": tab.index,
            }
        )
        items = items_from_api_payload(
            data.get("items", []), tab, self.map_tier, self.require_uncorrupted
        )
        return only_maps(items)


def _colour_of(entry: dict[str, Any]) -> str | None:
    colour = (entry.get("colour") or {}) if isinstance(entry.get("colour"), dict) else {}
    if not colour:
        return None
    try:
        return "#{:02x}{:02x}{:02x}".format(colour["r"], colour["g"], colour["b"])
    except (KeyError, TypeError, ValueError):
        return None


class OAuthStashSource(StashSource):
    """Official OAuth stash access. NOT VERIFIED -- see module docstring."""

    name = "oauth"
    setup_hint = (
        "Requires an OAuth client registered with GGG (contact oauth@grindinggear.com). "
        "Self-service registration does not exist, so this adapter cannot be exercised "
        "without approved credentials."
    )

    def __init__(
        self,
        access_token: str | None = None,
        user_agent: str = "poe-originator-pricer/0.1",
        map_tier: int = 16,
        require_uncorrupted: bool = True,
        session: requests.Session | None = None,
    ):
        self.access_token = access_token or os.getenv("POE_OAUTH_ACCESS_TOKEN", "")
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json"}
        )
        if self.access_token:
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"

    def available(self) -> tuple[bool, str]:
        if not self.access_token:
            return False, "no OAuth access token (requires a GGG-registered client)"
        return True, ""

    def _get(self, path: str) -> dict[str, Any]:
        ok, why = self.available()
        if not ok:
            raise StashAccessError(why)
        resp = self.session.get(f"{OAUTH_API_BASE}{path}", timeout=30)
        if resp.status_code == 401:
            raise StashAccessError("OAuth token rejected or expired")
        if resp.status_code != 200:
            raise StashAccessError(f"{path} returned HTTP {resp.status_code}")
        return resp.json()

    def list_tabs(self, league: str) -> list[StashTab]:
        data = self._get(f"/stash/{league}")
        tabs: list[StashTab] = []
        for index, entry in enumerate(data.get("stashes", []) or []):
            tabs.append(
                StashTab(
                    id=str(entry.get("id")),
                    name=entry.get("name", ""),
                    index=int(entry.get("index", index)),
                    type=entry.get("type", ""),
                    raw=entry,
                )
            )
        return tabs

    def fetch_tab(self, league: str, tab: StashTab) -> list[NormalizedMap]:
        data = self._get(f"/stash/{league}/{tab.id}")
        payload = data.get("stash") or data
        items = items_from_api_payload(
            payload.get("items", []), tab, self.map_tier, self.require_uncorrupted
        )
        return only_maps(items)
