"""A local HTTP boundary over the pricing core.

Built on **Starlette directly, with no new dependencies**. Streamlit already
installs `starlette` and `uvicorn`, so this costs the install nothing -- which
matters, because the previous piece of work existed to get the shipped app from
1.1 GB down to 430 MB and adding FastAPI plus pydantic to re-serve six
endpoints would have given some of that back. FastAPI's validation and OpenAPI
generation are real, but this API is six routes answering one process on one
machine, and hand-written validation of a feature dictionary is a dozen lines.

Why a boundary at all, when the UI can call the core directly: because every
other shell -- a native overlay, Tauri, Electron, a second window -- needs an
answer from the same process that holds the model, and an HTTP port is the one
interface all of them can speak. It also moves the long work off the renderer,
which is Streamlit's own documented remedy for an interaction that blocks the
page.

Security, such as it is for a local service:

- **Bound to 127.0.0.1.** Never 0.0.0.0. Nothing here should be reachable from
  the network, and the price of a mistake is someone else's process reading the
  user's stash contents.
- **A token generated per launch**, required on every route except `/health`.
  Any page in the user's browser can issue requests to localhost, so an
  unauthenticated local API is readable by any site they happen to have open.
  The token is printed once at startup and written to a file only the launching
  process knows about.
- **No CORS headers at all**, so a browser will refuse to read a response even
  if it can send the request.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.service.core import CoreUnavailable, get_core, priced_to_dict

#: Header the token goes in. Not `Authorization`, because this is not a bearer
#: token in any standard sense and saying so invites it being treated like one.
TOKEN_HEADER = "X-Originator-Token"

#: Where the token is written so a sibling process (the overlay) can read it.
#: Beside the database rather than in a temp directory: it is this install's
#: secret, it should die with the folder, and a stale token in /tmp that still
#: unlocks a running service is worse than no file at all.
TOKEN_FILENAME = "service-token"


def _json_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise CoreUnavailable("request body is not valid JSON")
    if not isinstance(payload, dict):
        raise CoreUnavailable("request body must be a JSON object")
    return payload


# -- routes -----------------------------------------------------------------
async def health(request: Request) -> JSONResponse:
    """Liveness only, and deliberately unauthenticated.

    A client needs to know whether the service is up *before* it can prove it
    holds the token, and this leaks nothing: no league, no stash, no prices.
    """
    return JSONResponse({"status": "ok", "service": "originator-pricer"})


async def status(request: Request) -> JSONResponse:
    return JSONResponse(get_core().status())


async def price(request: Request) -> JSONResponse:
    """Price maps given as feature dictionaries.

    Batched on purpose: pricing a whole tab in one call lets the core run the
    model once over every map and share one comparable pool, which is the
    difference between a quad tab taking milliseconds and taking seconds.
    """
    payload = await _body(request)
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise CoreUnavailable("'items' must be a list of feature objects")
    if not all(isinstance(row, dict) for row in rows):
        raise CoreUnavailable("every entry in 'items' must be an object")

    core = get_core()
    priced = core.price_values(rows, strategy=str(payload.get("strategy", "market")))
    return JSONResponse(
        {
            "strategy": payload.get("strategy", "market"),
            "results": [priced_to_dict(p, core.formatter) for p in priced],
        }
    )


async def price_text(request: Request) -> JSONResponse:
    """Price one map from copied item text.

    This is the endpoint a native overlay actually needs: the user presses the
    hotkey, the client reads the clipboard once, and posts the text here.
    """
    payload = await _body(request)
    text = payload.get("text")
    if not isinstance(text, str):
        raise CoreUnavailable("'text' must be a string")

    core = get_core()
    result = core.price_text(text, strategy=str(payload.get("strategy", "market")))
    return JSONResponse(priced_to_dict(result, core.formatter))


ROUTES = [
    Route("/health", health, methods=["GET"]),
    Route("/status", status, methods=["GET"]),
    Route("/price", price, methods=["POST"]),
    Route("/price/text", price_text, methods=["POST"]),
]

#: Routes reachable without the token. Everything else needs it.
OPEN_PATHS = frozenset({"/health"})


def create_app(token: str | None = None, cfg=None) -> Starlette:
    """The application, with the token it will require.

    `token=""` disables the check. That exists for tests and for a client that
    has already isolated the port; it is never the default, because a local
    port with no auth on it is readable by any web page the user has open.
    """
    expected = secrets.token_urlsafe(32) if token is None else token

    async def guard(request: Request, call_next):
        if expected and request.url.path not in OPEN_PATHS:
            supplied = request.headers.get(TOKEN_HEADER, "")
            # Constant-time: the comparison is cheap to do properly and a
            # local attacker can measure a local service very precisely.
            if not secrets.compare_digest(supplied, expected):
                return _json_error(401, f"missing or wrong {TOKEN_HEADER}")
        try:
            return await call_next(request)
        except CoreUnavailable as exc:
            return _json_error(400, str(exc))

    # Passed to the constructor rather than through an `@app.middleware("http")`
    # decorator: that decorator was removed in Starlette 1.x, and this form
    # works across both.
    app = Starlette(
        routes=ROUTES,
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=guard)],
    )
    app.state.token = expected
    app.state.cfg = cfg
    return app


def write_token(token: str, cfg=None) -> Path:
    """Put the token where a sibling process can find it, and nobody else."""
    from src.config import load_config

    cfg = cfg or load_config()
    path = Path(cfg.db_path).parent / TOKEN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        # Owner-only where the platform honours it. Windows ignores this, which
        # is why the token is per-launch and short-lived rather than relying on
        # file permissions to protect it.
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_token(cfg=None) -> str:
    """The running service's token, for a client in the same install."""
    from src.config import load_config

    cfg = cfg or load_config()
    path = Path(cfg.db_path).parent / TOKEN_FILENAME
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
