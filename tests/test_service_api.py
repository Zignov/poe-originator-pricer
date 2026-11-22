"""The local HTTP boundary: its contract, and the things that must not leak.

These are deliberately contract tests rather than more unit tests of the
pricing layer -- that layer already has a thousand of those, and the point of
the boundary is that those keep working untouched. What is new and therefore
needs pinning is the shape of the JSON, the authentication, and the promise
that a bad request produces a readable error rather than a traceback.
"""
from __future__ import annotations

import pytest

from src.service.api import TOKEN_HEADER, create_app
from src.service.core import CoreUnavailable, map_from_values

try:
    from starlette.testclient import TestClient
except (ImportError, RuntimeError) as exc:  # starlette raises if httpx2 is absent
    pytest.skip(f"starlette TestClient unavailable: {exc}", allow_module_level=True)

TOKEN = "test-token"


@pytest.fixture
def client():
    from src.service.core import reset_core

    reset_core()
    with TestClient(create_app(token=TOKEN)) as c:
        yield c
    reset_core()


def _auth(**extra):
    return {TOKEN_HEADER: TOKEN, **extra}


A_MAP = {"quantity": 90, "rarity": 130, "pack_size": 32, "more_currency": 45}


# -- authentication ---------------------------------------------------------
def test_health_needs_no_token_and_leaks_nothing():
    """A client has to be able to see the service is up before it can authenticate.

    It must also say nothing: no league, no model, no prices. Anything a page
    in the user's browser could read is a thing it should not be told.
    """
    with TestClient(create_app(token=TOKEN)) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "service"}, f"health leaked {set(body)}"


@pytest.mark.parametrize("path,method", [("/status", "get"), ("/price", "post"), ("/price/text", "post")])
def test_every_other_route_refuses_without_the_token(client, path, method):
    call = getattr(client, method)
    resp = call(path) if method == "get" else call(path, json={})
    assert resp.status_code == 401
    assert TOKEN_HEADER in resp.json()["error"]


def test_a_wrong_token_is_refused(client):
    resp = client.get("/status", headers={TOKEN_HEADER: "not-it"})
    assert resp.status_code == 401


def test_auth_can_be_disabled_explicitly_but_is_not_the_default():
    """`token=""` is for tests. `create_app()` with no argument must not be open."""
    with TestClient(create_app(token="")) as open_client:
        assert open_client.get("/status").status_code == 200

    app = create_app()
    assert app.state.token, "a default app must generate a token"
    assert len(app.state.token) >= 32


# -- the contract -----------------------------------------------------------
def test_status_reports_what_a_client_needs_to_decide_anything(client):
    body = client.get("/status", headers=_auth()).json()
    for key in ("league", "model_version", "comparables", "divine_to_chaos", "strategies"):
        assert key in body, f"status is missing {key}"
    assert "market" in body["strategies"]


def test_pricing_a_map_returns_chaos_and_display_side_by_side(client):
    resp = client.post("/price", json={"items": [A_MAP]}, headers=_auth())
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert len(results) == 1
    row = results[0]

    assert row["chaos"]["fair_value"] is not None, "no price came back"
    assert row["chaos"]["fair_value"] > 0
    # Both representations, so a client doing arithmetic uses chaos and a
    # client drawing a tile uses the formatted figure, and neither has to
    # reimplement the other's rules.
    assert row["display"]["formatted"]
    assert row["confidence"]["level"] in {"High", "Medium", "Low"}
    assert row["item"]["quantity"] == A_MAP["quantity"]


def test_the_interval_is_formatted_on_this_side_of_the_boundary(client):
    """Added when the clipboard price checker became the first real consumer.

    It had `chaos.low`, `chaos.high` and the unit, and still could not render
    "330-505c" without reimplementing when a price switches to divine and how
    it rounds -- which is the duplication the `display` block exists to
    prevent. The rule the block follows is that anything the user reads as a
    number is formatted here.
    """
    row = client.post("/price", json={"items": [A_MAP]}, headers=_auth()).json()["results"][0]
    display = row["display"]
    for key in ("low", "high", "range"):
        assert key in display, f"display is missing {key}"
    assert display["range"], "the interval came back with no formatted range"
    # The unit belongs to the figures beside it, so a range in divine must not
    # be read against a chaos suffix.
    assert display["unit"] in display["range"] or display["range"].count("-") == 1


def test_a_batch_is_priced_in_one_call(client):
    """Batching is the reason the route takes a list: one model pass per tab."""
    items = [dict(A_MAP, quantity=q) for q in (40, 70, 100, 130)]
    results = client.post("/price", json={"items": items}, headers=_auth()).json()["results"]
    assert len(results) == 4
    values = [r["chaos"]["fair_value"] for r in results]
    assert all(v is not None for v in values)
    # Monotonicity is enforced in the model and must survive the boundary.
    assert values == sorted(values), f"more quantity priced lower: {values}"


def test_copied_item_text_can_be_priced(client):
    """The endpoint a native overlay actually calls, on real clipboard text."""
    from pathlib import Path

    sample = Path("data/sample_map.txt")
    if not sample.exists():
        pytest.skip("no sample item text in this checkout")
    text = sample.read_text(encoding="utf-8")

    resp = client.post("/price/text", json={"text": text}, headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "chaos" in body and "item" in body
    assert body["item"]["quantity"] > 0, "the rolls were not parsed out of the text"

    # The assertions that matter, and the ones whose absence let a real bug
    # through: this route parsed the item but never called `classify`, so
    # `supported` was False for every map ever posted to it. A 200 with the
    # rolls filled in looked exactly like success.
    assert body["supported"], f"copied text came back unsupported: {body.get('note')}"
    assert body["chaos"]["fair_value"] is not None
    assert body["chaos"]["fair_value"] > 0
    assert body["display"]["formatted"]


def test_copied_text_is_classified_against_the_configured_scope():
    """`parse_item_text` alone is not enough, and that is the whole bug.

    Parsing fills in the rolls. `classify` is what decides whether the item is
    in scope and sets `supported`, which defaults to False -- so the route has
    to run both. Tested at the core rather than over HTTP because this is a
    statement about the core's contract, and it holds for every shell.
    """
    from pathlib import Path

    from src.service.core import PricingCore

    sample = Path("data/sample_map.txt")
    if not sample.exists():
        pytest.skip("no sample item text in this checkout")

    priced = PricingCore.build().price_text(sample.read_text(encoding="utf-8"))
    assert priced.item.supported, "classify was not applied to the copied text"
    assert priced.item.is_originator
    assert priced.supported


def test_an_empty_strategy_list_is_not_silently_accepted(client):
    resp = client.post(
        "/price", json={"items": [A_MAP], "strategy": "nonsense"}, headers=_auth()
    )
    assert resp.status_code == 400
    assert "strategy" in resp.json()["error"]


# -- bad input is answered, not crashed -------------------------------------
@pytest.mark.parametrize(
    "payload,needle",
    [
        ({}, "items"),
        ({"items": "not a list"}, "items"),
        ({"items": [1, 2, 3]}, "object"),
        ({"items": [{"quantity": "abc"}]}, "number"),
    ],
)
def test_a_malformed_price_request_gets_an_explanation(client, payload, needle):
    resp = client.post("/price", json=payload, headers=_auth())
    assert resp.status_code == 400, resp.text
    assert needle in resp.json()["error"]


def test_a_body_that_is_not_json_is_rejected_cleanly(client):
    resp = client.post(
        "/price", content=b"{not json", headers=_auth(**{"content-type": "application/json"})
    )
    assert resp.status_code == 400
    assert "JSON" in resp.json()["error"]


def test_missing_text_is_rejected_rather_than_priced_as_an_empty_map(client):
    for payload in ({}, {"text": ""}, {"text": "   "}, {"text": 42}):
        resp = client.post("/price/text", json=payload, headers=_auth())
        assert resp.status_code == 400, f"{payload} was accepted"


# -- the core underneath ----------------------------------------------------
def test_unknown_keys_in_a_feature_object_are_ignored():
    """A client should be able to post a whole stash row without stripping it."""
    item = map_from_values(dict(A_MAP, some_future_field="x", tab="Maps"))
    assert item.quantity == A_MAP["quantity"]
    assert item.supported


def test_a_non_numeric_feature_names_itself():
    with pytest.raises(CoreUnavailable, match="rarity"):
        map_from_values({"rarity": "lots"})


def test_the_core_imports_without_streamlit(monkeypatch):
    """The contract that makes the UI replaceable.

    If `src.service.core` ever grows a Streamlit import, every alternative
    shell inherits Streamlit's session state and caching, and the boundary
    stops being a boundary. A source check rather than a behavioural one,
    because the failure is an import appearing.
    """
    from pathlib import Path

    for name in ("core.py", "api.py"):
        source = Path("src/service") / name
        text = source.read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith(("import streamlit", "from streamlit"))
        ]
        assert not offenders, f"{name} imports Streamlit: {offenders}"
