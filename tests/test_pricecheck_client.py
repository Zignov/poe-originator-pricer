"""The HTTP client: what it sends, and what it says when it cannot.

Two halves, tested differently. The request shaping is checked against a real
socket -- a throwaway `http.server` on an ephemeral port -- because the thing
that goes wrong with `urllib` is the method, the headers or the body, and a
mocked `urlopen` would happily accept all three being wrong. The failure
messages are checked directly, because every one of them is a sentence a user
has to act on and the wording is the feature.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.pricecheck.client import (
    PriceCheckClient,
    ServiceError,
    _explain_http_error,
    _explain_timeout,
    _explain_url_error,
)
from src.service.api import TOKEN_HEADER

PRICED = {
    "supported": True,
    "chaos": {"suggested": 412.0},
    "display": {"formatted": "412c", "range": "330-505c"},
    "confidence": {"level": "High", "score": 0.9, "reasons": []},
    "item": {"quantity": 94},
}


class _Recorder(BaseHTTPRequestHandler):
    """Answers the four routes and records what it was asked."""

    seen: list[dict] = []
    status_for: dict = {}

    def _record(self, body: bytes | None):
        type(self).seen.append(
            {
                "path": self.path,
                "method": self.command,
                "token": self.headers.get(TOKEN_HEADER),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(body) if body else None,
            }
        )

    def _reply(self, status: int, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        self._record(None)
        status = type(self).status_for.get(self.path, 200)
        if self.path == "/health":
            self._reply(status, {"status": "ok", "service": "originator-pricer"})
        elif self.path == "/status":
            self._reply(status, {"league": "Covens", "model_version": "v7"})
        else:
            self._reply(404, {"error": "no such route"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self._record(self.rfile.read(length))
        status = type(self).status_for.get(self.path, 200)
        if status != 200:
            self._reply(status, {"error": "no item text was supplied"})
        else:
            self._reply(200, PRICED)

    def log_message(self, *_args):  # keep the test output clean
        pass


@pytest.fixture
def server():
    _Recorder.seen = []
    _Recorder.status_for = {}
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def client(server):
    return PriceCheckClient(token="test-token", port=server.server_address[1])


def _free_port() -> int:
    """A port with nothing on it, for the connection-refused case."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- what it sends ----------------------------------------------------------
def test_pricing_text_posts_json_with_the_token(client):
    client.price_text("Rarity: Rare\nTerror Solace", "patient")
    sent = _Recorder.seen[-1]
    assert sent["method"] == "POST"
    assert sent["path"] == "/price/text"
    assert sent["token"] == "test-token"
    assert sent["content_type"] == "application/json"
    assert sent["body"] == {
        "text": "Rarity: Rare\nTerror Solace",
        "strategy": "patient",
    }


def test_the_reply_comes_back_as_the_dict_the_service_sent(client):
    assert client.price_text("Rarity: Rare")["display"]["formatted"] == "412c"


def test_status_is_a_get_and_health_is_too(client):
    client.status()
    assert _Recorder.seen[-1]["method"] == "GET"
    assert client.health() is True
    assert _Recorder.seen[-1]["path"] == "/health"


def test_health_is_asked_without_the_token(client):
    """It is the one open route, and a client that has no token yet has to be
    able to find out whether anything is listening at all."""
    client.health()
    assert _Recorder.seen[-1]["token"] is None


def test_the_first_request_gets_the_long_timeout_and_later_ones_do_not(client):
    """A cold service loads the model before it can answer, which is seconds.

    Giving every request that budget would mean a hung service showed as a
    frozen popup rather than an error, so the allowance is spent once.
    """
    assert client.warm is False
    client.health()
    assert client.warm is True


def test_the_url_is_always_loopback(client):
    assert client.base_url.startswith("http://127.0.0.1:")


# -- what it says when it cannot --------------------------------------------
def test_no_service_at_all_names_the_command_that_fixes_it():
    """By far the most likely failure, and the user cannot see a port from
    inside a game -- so the message has to carry the whole answer.

    Against a real closed port, deliberately. On Windows this does not come
    back as `ConnectionRefusedError`: the firewall drops the SYN rather than
    answering it, so the attempt *times out*. A mocked `urlopen` raising
    `ConnectionRefusedError` would have passed while the real thing said "did
    not answer in time" and left the user with nothing to do about it.
    """
    client = PriceCheckClient(token="x", port=_free_port(), timeout=1.0)
    with pytest.raises(ServiceError) as exc:
        client.status()
    message = str(exc.value)
    assert "python -m src.service" in message
    assert "127.0.0.1" in message


def test_a_timeout_after_a_successful_reply_is_not_blamed_on_a_missing_service(client):
    """Once something has answered, a timeout is a slow service, not an absent
    one -- and telling the user to start a second one would be wrong."""
    client.health()
    assert client.warm
    message = _explain_timeout(client.port, client.warm, 6.0)
    assert "python -m src.service" not in message
    assert "did not answer" in message


def test_a_service_that_dies_mid_request_is_an_error_not_a_traceback(server):
    """The bug a real run found, and the reason `_request` catches OSError.

    `urllib` only wraps *connection*-stage failures in URLError. A socket error
    once the request is on the wire -- here, a service shut down between two
    requests -- comes straight out as ConnectionResetError and escaped every
    other clause, which in the running app means a traceback out of a Tk
    callback in a process with no console to print it to.
    """
    client = PriceCheckClient(token="x", port=server.server_address[1], timeout=2.0)
    client.health()  # warm, so this is not the "nothing listening" path
    server.shutdown()
    server.server_close()

    with pytest.raises(ServiceError) as exc:
        client.status()
    assert "pricing service" in str(exc.value)


def test_a_reset_is_explained_as_the_service_stopping():
    from src.pricecheck.client import _explain_os_error

    reset = ConnectionResetError(10054, "forcibly closed")
    message = _explain_os_error(reset, 8777, warm=True)
    assert "stopped" in message
    assert "python -m src.service" in message


def test_health_reports_a_dead_service_as_false_rather_than_raising():
    """It is the probe used to decide whether to start one, so it has to be
    safe to call when there is nothing there."""
    client = PriceCheckClient(token="x", port=_free_port(), timeout=1.0)
    assert client.health() is False


def test_a_401_explains_that_the_token_rotates(client):
    """The message has to say *why* a token that worked yesterday does not now.

    The service generates one per launch, so the fix is to restart the checker
    -- which is not guessable from "401 Unauthorized".
    """
    _Recorder.status_for["/status"] = 401
    with pytest.raises(ServiceError) as exc:
        client.status()
    assert "restart" in str(exc.value).lower()


def test_a_400_is_passed_through_in_the_services_own_words(client):
    """Those messages are written to be read -- reinventing them here would
    replace a specific reason with a generic one."""
    _Recorder.status_for["/price/text"] = 400
    with pytest.raises(ServiceError) as exc:
        client.price_text("Rarity: Rare")
    assert "no item text was supplied" in str(exc.value)


def test_a_404_still_produces_a_readable_error(client):
    with pytest.raises(ServiceError) as exc:
        client._request("/nope")
    assert str(exc.value)


# -- the explainers, directly -----------------------------------------------
def test_an_errno_only_refusal_is_recognised():
    """Windows reports 10061 and Linux 111; the class is not always set.

    Falling through to "could not reach the pricing service: ..." would lose
    the one message that tells the user what to run.
    """
    reason = OSError()
    reason.errno = 10061
    message = _explain_url_error(urllib.error.URLError(reason), 8777)
    assert "python -m src.service" in message


def test_a_cold_timeout_is_treated_as_nothing_listening():
    """The Windows behaviour above, pinned at the explainer.

    A client that has never had a reply timing out means there is nothing
    there, whatever the socket layer chose to call it.
    """
    message = _explain_url_error(urllib.error.URLError(TimeoutError()), 8777, warm=False)
    assert "python -m src.service" in message


def test_a_warm_timeout_says_so_rather_than_blaming_the_connection():
    message = _explain_url_error(urllib.error.URLError(TimeoutError()), 8777, warm=True)
    assert "did not answer" in message
    assert "python -m src.service" not in message


def test_an_unreadable_error_body_does_not_mask_the_status():
    class _Unreadable(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://127.0.0.1", 503, "boom", {}, None)

        def read(self):
            raise OSError("no body")

    assert "503" in _explain_http_error(_Unreadable())


def test_a_401_is_explained_even_with_no_body():
    class _Bare(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://127.0.0.1", 401, "nope", {}, None)

        def read(self):
            return b""

    assert "restart" in _explain_http_error(_Bare()).lower()


# -- construction -----------------------------------------------------------
def test_a_client_built_from_config_carries_the_installs_token(tmp_path, monkeypatch):
    from src.pricecheck.client import client_from_config
    from src.service.api import TOKEN_FILENAME

    (tmp_path / TOKEN_FILENAME).write_text("written-by-the-service", encoding="utf-8")

    class _Cfg:
        db_path = tmp_path / "originator.db"

        def get(self, *_a, **_k):
            return None

    assert client_from_config(_Cfg()).token == "written-by-the-service"


def test_a_missing_token_file_is_not_fatal(tmp_path):
    """`--no-auth` is a supported way to run the service, so an empty token has
    to be a usable client rather than an error."""
    from src.pricecheck.client import client_from_config

    class _Cfg:
        db_path = tmp_path / "originator.db"

        def get(self, *_a, **_k):
            return None

    assert client_from_config(_Cfg()).token == ""
