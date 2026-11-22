"""The HTTP side: ask the local pricing service what an item is worth.

Standard library only, on purpose. `urllib.request` is clumsier than `httpx`
for about fifteen lines, and those fifteen lines are the whole client -- four
routes on one loopback port, no redirects, no connection pooling worth having
and no streaming. The install is already 430 MB and a dependency added for
ergonomics in one small module is not a good trade.

What this module is careful about is the failure messages, because every one of
them is something the user has to fix and cannot see from inside a game:

- the service is not running (by far the most likely),
- it is running but the token on disk is stale,
- it is running without a model trained,
- the item on the clipboard is not a map this app prices.

Each of those gets its own sentence rather than a status code.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.service.api import TOKEN_HEADER, read_token

#: The port `python -m src.service` listens on. Imported from there rather than
#: redeclared, so a client and a server can never disagree about the default.
from src.service.__main__ import DEFAULT_PORT

#: Generous for a loopback request against an in-process model, and short
#: enough that a hung service shows as an error rather than a frozen popup.
#: The first request after the service starts pays for loading the model, so it
#: is allowed considerably longer than the rest.
TIMEOUT_SECONDS = 6.0
FIRST_REQUEST_TIMEOUT_SECONDS = 30.0


class ServiceError(RuntimeError):
    """The service could not answer, and the message is for the user to read."""


@dataclass
class PriceCheckClient:
    """A client for one running pricing service.

    `host` is not a parameter. The service binds 127.0.0.1 and nothing else,
    and a client that could be pointed somewhere else would be a client that
    could be pointed at a machine on the network -- which is the one thing the
    service's docstring says must never happen.
    """

    token: str = ""
    port: int = DEFAULT_PORT
    timeout: float = TIMEOUT_SECONDS

    #: Set once any request has succeeded. Until then requests are given the
    #: longer timeout, because the first one through builds the pricing core:
    #: it opens the database and loads the model, and on a cold start that is
    #: seconds, not milliseconds.
    warm: bool = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- routes ----------------------------------------------------------
    def health(self) -> bool:
        """Is anything listening and answering? Never raises."""
        try:
            self._request("/health", authenticate=False)
            return True
        except ServiceError:
            return False

    def status(self) -> dict[str, Any]:
        return self._request("/status")

    def price_text(self, text: str, strategy: str = "market") -> dict[str, Any]:
        """Price one copied item. `text` is the clipboard, verbatim."""
        return self._request(
            "/price/text", payload={"text": text, "strategy": strategy}
        )

    # -- transport -------------------------------------------------------
    def _request(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        authenticate: bool = True,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if authenticate and self.token:
            headers[TOKEN_HEADER] = self.token
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers,
            method="POST" if body is not None else "GET",
        )
        timeout = self.timeout if self.warm else FIRST_REQUEST_TIMEOUT_SECONDS
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ServiceError(_explain_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise ServiceError(_explain_url_error(exc, self.port, self.warm)) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ServiceError(_explain_timeout(self.port, self.warm, timeout)) from exc
        except OSError as exc:
            # Broad, and it has to be. `urllib` only wraps failures from the
            # *connection* stage in URLError; a socket error once the request
            # is on the wire comes straight out. The case that found this was a
            # service shut down between two requests, which raises
            # ConnectionResetError from `getresponse()` and escaped every
            # clause above -- a traceback out of a Tk callback, in a process
            # with no console to print it to.
            raise ServiceError(_explain_os_error(exc, self.port, self.warm)) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise ServiceError("the pricing service sent a reply that was not JSON") from exc

        self.warm = True
        if not isinstance(decoded, dict):
            raise ServiceError("the pricing service sent a reply that was not an object")
        return decoded


def _explain_http_error(exc: urllib.error.HTTPError) -> str:
    """Turn a status code into the thing the user has to do about it."""
    # The service puts its reason in {"error": "..."} and those messages are
    # written to be read -- "'text' must be a string", "no item text was
    # supplied" -- so they are preferred over anything invented here.
    detail = ""
    try:
        body = json.loads(exc.read().decode("utf-8"))
        if isinstance(body, dict):
            detail = str(body.get("error") or "")
    except (OSError, ValueError, json.JSONDecodeError):
        pass

    if exc.code == 401:
        return (
            "the pricing service refused this token. It generates a new one each "
            "time it starts, so restart the price checker to pick the current one up."
        )
    if exc.code == 400 and detail:
        return detail
    return detail or f"the pricing service answered {exc.code}"


def _not_listening(port: int) -> str:
    """The most likely failure of all, with the whole answer in it.

    The user is in a game and cannot see a port, so this says what to run
    rather than what went wrong.
    """
    return (
        f"nothing is listening on 127.0.0.1:{port}. Start the pricing service "
        "with `python -m src.service`, or run the price checker without "
        "`--no-spawn` and it will start one itself."
    )


def _explain_timeout(port: int, warm: bool, timeout: float) -> str:
    """A timeout means two completely different things, and `warm` tells them apart.

    This is not a nicety. Connecting to a *closed* loopback port does not
    reliably refuse on Windows -- the firewall drops the SYN instead of
    answering it, so the connection attempt times out. The single most likely
    failure in this whole feature, the service not being started, therefore
    arrives as a timeout and not as `ConnectionRefusedError`. Reporting it as
    "did not answer in time" would be technically true and completely useless:
    nothing was ever there to answer.

    A client that has already had one reply is a different situation. Something
    *is* listening, so a timeout is a slow or wedged service, and telling the
    user to start a second one would be wrong.
    """
    if not warm:
        return _not_listening(port)
    return f"the pricing service did not answer within {timeout:.0f}s"


#: Connection refused, as Windows and Linux respectively number it.
_REFUSED_ERRNOS = (10061, 111)
#: Connection reset. A service that was there and is not any more.
_RESET_ERRNOS = (10054, 104)


def _is_gone(reason: object) -> bool:
    """Does this mean there is nothing at the other end any more?"""
    if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)):
        return True
    return isinstance(reason, OSError) and getattr(reason, "errno", None) in (
        _REFUSED_ERRNOS + _RESET_ERRNOS
    )


def _explain_os_error(exc: OSError, port: int, warm: bool) -> str:
    """A socket failure with no URLError wrapper around it."""
    if _is_gone(exc):
        return (
            f"the pricing service on 127.0.0.1:{port} stopped while answering. "
            "Start it again with `python -m src.service`."
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return _explain_timeout(port, warm, TIMEOUT_SECONDS)
    return f"could not reach the pricing service: {exc}"


def _explain_url_error(
    exc: urllib.error.URLError, port: int, warm: bool = False
) -> str:
    """The transport failures, mapped onto what the user has to do."""
    reason = getattr(exc, "reason", exc)
    if _is_gone(reason):
        return _not_listening(port)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _explain_timeout(port, warm, TIMEOUT_SECONDS)
    return f"could not reach the pricing service: {reason}"


def client_from_config(
    cfg=None, port: int = DEFAULT_PORT, token: str | None = None
) -> PriceCheckClient:
    """A client holding the token the running service wrote for this install.

    An empty token is not an error here. The service can be run with
    `--no-auth`, and a client that refused to start without a token would make
    that mode unusable; the 401 message covers the case where one was needed.
    """
    return PriceCheckClient(
        token=token if token is not None else read_token(cfg), port=port
    )
