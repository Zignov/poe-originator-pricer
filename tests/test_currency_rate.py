"""Reading currency rates out of a poe.ninja overview.

Fixtures are trimmed from the real payload captured on 2026-08-24 for league
"Allflame", cross-checked against what poe.ninja's own page renders (Divine Orb
showed "211" and "9.7M" for a `primaryValue` of 210.9 and a
`volumePrimaryValue` of 9668224).

The number that matters most here is the Exalted Orb at 1.25 chaos. An earlier
version of this module read GGG's live order book instead, decided the 1-3 chaos
offers on it were bait, and returned 25. See `test_the_exalted_orb_is_cheap`.
"""
from __future__ import annotations

import pytest

from src.market.currency_source import (
    EXCHANGE,
    STASH,
    ExchangeRateError,
    RateQuote,
    fetch_rate_quote,
    parse_overview,
)

EXCHANGE_PAYLOAD = {
    "lines": [
        {"id": "chaos", "primaryValue": 1, "volumePrimaryValue": 9668224},
        {"id": "divine", "primaryValue": 210.9, "volumePrimaryValue": 9668224},
        {"id": "exalted", "primaryValue": 1.25, "volumePrimaryValue": 46873},
        {"id": "mirror", "primaryValue": 194310, "volumePrimaryValue": 21309336},
        {"id": "tainted-chaos-orb", "primaryValue": 9.47, "volumePrimaryValue": 402.3},
    ]
}

# The older pay/receive shape, keyed and priced differently.
STASH_PAYLOAD = {
    "lines": [
        {"currencyTypeName": "Divine Orb", "chaosEquivalent": 189.2},
        {"currencyTypeName": "Exalted Orb", "chaosEquivalent": 3.02},
    ]
}


def _rates(payload=EXCHANGE_PAYLOAD, source=EXCHANGE):
    return parse_overview(payload, "Allflame", source)


# ---------------------------------------------------------------------------
# Reading the exchange overview
# ---------------------------------------------------------------------------
def test_a_currency_is_priced_in_chaos():
    assert _rates()["divine"].chaos == 210.9


def test_the_exalted_orb_is_cheap():
    """The whole reason this module was rewritten.

    Reading GGG's order book and rejecting its 1-3 chaos cluster as bait
    returned 25 chaos for an Exalted Orb. poe.ninja puts it at 1.25 against
    47k volume an hour. The discarded cluster was the market.
    """
    exalted = _rates()["exalted"]
    assert exalted.chaos == 1.25
    assert exalted.volume == 46873


def test_volume_comes_across_so_a_rate_can_be_doubted():
    assert _rates()["divine"].volume == 9668224
    assert not _rates()["divine"].thin


def test_an_illiquid_currency_is_flagged_rather_than_hidden():
    """A rate is still returned -- the caller is told not to lean on it."""
    quiet = _rates()["tainted-chaos-orb"]
    assert quiet.chaos == 9.47
    assert quiet.thin
    assert "low volume" in quiet.summary()


def test_the_summary_names_its_source_and_volume():
    summary = _rates()["divine"].summary()
    assert "1 divine = 210.9 chaos" in summary
    assert "poe.ninja exchange" in summary
    assert "9.7M chaos/hour" in summary, "written the way poe.ninja's page writes it"


# ---------------------------------------------------------------------------
# Reading the stash overview, which is a different number on purpose
# ---------------------------------------------------------------------------
def test_the_stash_shape_is_understood_too():
    rates = _rates(STASH_PAYLOAD, STASH)
    assert rates["divine"].chaos == 189.2
    assert rates["exalted"].chaos == 3.02


def test_the_two_sources_key_the_same_way():
    """"Divine Orb" and "divine" have to land on the same key, or a caller
    asking for a rate gets one from the exchange and nothing from the stash."""
    assert set(_rates(STASH_PAYLOAD, STASH)) <= set(_rates())


def test_the_stash_overview_says_which_source_it_came_from():
    """The two disagree by 10% on divine and 2.4x on exalted, so a rate that
    does not say where it came from is not enough to act on."""
    assert "poe.ninja stash" in _rates(STASH_PAYLOAD, STASH)["divine"].summary()


# ---------------------------------------------------------------------------
# Malformed payloads
# ---------------------------------------------------------------------------
def test_an_empty_payload_yields_nothing():
    assert parse_overview({}, "Allflame") == {}
    assert parse_overview({"lines": []}, "Allflame") == {}


@pytest.mark.parametrize(
    "line",
    [
        {"id": "divine"},                                # no price at all
        {"id": "divine", "primaryValue": None},
        {"id": "divine", "primaryValue": "nonsense"},
        {"id": "divine", "primaryValue": 0},             # a zero rate divides badly
        {"id": "divine", "primaryValue": -5},
        {"primaryValue": 210.9},                         # no id to file it under
        "not a dict at all",
    ],
)
def test_a_malformed_line_is_skipped_not_guessed(line):
    assert parse_overview({"lines": [line]}, "Allflame") == {}


# ---------------------------------------------------------------------------
# The fetch layer, driven through a stub session
# ---------------------------------------------------------------------------
class _NoLimit:
    """The limiter is real everywhere else; here it would only add sleeps."""

    def acquire(self, *args, **kwargs):
        return 0.0

    def update_from_headers(self, headers):
        pass

    def note_retry_after(self, seconds):
        pass


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return self._response


def _fetch(payload, want="divine", status=200):
    session = _Session(_Response(payload, status))
    quote = fetch_rate_quote("Allflame", "ua", want, session, limiter=_NoLimit())
    return quote, session


def test_a_rate_is_fetched_and_returned():
    """poe.ninja still answers -- but it is the fallback now, not the first call.

    GGG's own Currency Exchange is tried ahead of it, so the poe.ninja request
    is no longer `calls[0]`; this finds it by URL rather than by position.
    """
    quote, session = _fetch(EXCHANGE_PAYLOAD)
    assert isinstance(quote, RateQuote) and quote.chaos == 210.9
    ninja_calls = [c for c in session.calls if c["url"] == EXCHANGE]
    assert ninja_calls, f"poe.ninja was never asked; calls were {session.calls}"
    assert ninja_calls[0]["params"] == {"league": "Allflame", "type": "Currency"}


def test_one_response_carries_every_currency():
    """Two rates used to mean two requests against GGG's endpoint."""
    assert set(parse_overview(EXCHANGE_PAYLOAD, "Allflame")) >= {"divine", "exalted"}


def test_an_unknown_league_is_named_rather_than_read_as_no_data():
    """poe.ninja answers an unrecognised league with HTTP 200 and no lines.

    Silence is the worst possible shape for that failure -- it looks like the
    market having nothing to say, so it has to be called out explicitly.
    """
    with pytest.raises(ExchangeRateError, match="League setting"):
        _fetch({"lines": []})


def test_a_missing_currency_is_distinguished_from_a_missing_league():
    with pytest.raises(ExchangeRateError, match="priced 5 other currencies"):
        _fetch(EXCHANGE_PAYLOAD, want="ancient-orb")


def test_an_http_error_is_surfaced():
    with pytest.raises(ExchangeRateError, match="HTTP 503"):
        _fetch(EXCHANGE_PAYLOAD, status=503)
