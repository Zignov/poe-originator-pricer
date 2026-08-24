"""Reading a market rate out of an exchange book.

Every fixture here is shaped from a real book captured live on 2026-08-24
against league "Allflame". The two that matter:

  divine  199, 210, 215, 219, 219, 220, 229, 230, 240, 240
          -- a real spread, with the deep offers sitting at the top end
  exalted 1, 2, 2.95, 3, 20, 25, 50, 50, 90, 150
          -- four bait offers, then the real market

The old estimator read the median of the ten cheapest offers and returned 219
and 15-22.5 respectively, against a market of roughly 230 and 25.
"""
from __future__ import annotations

import pytest

from src.market.currency_source import (
    ExchangeRateError,
    Offer,
    parse_offers,
    rate_from_offers,
    reject_bait,
    weighted_median,
)

# (chaos, amount, stock) exactly as the live divine book came back.
DIVINE_BOOK = [
    Offer(199.0, 1.0, 269),
    Offer(210.0, 1.0, 9),
    Offer(215.0, 1.0, 24),
    Offer(219.0, 1.0, 25),
    Offer(219.0, 1.0, 316),
    Offer(220.0, 1.0, 2),
    Offer(229.0, 1.0, 857),
    Offer(230.0, 1.0, 153),
    Offer(480.0, 2.0, 102),
    Offer(240.0, 1.0, 31),
]

EXALTED_BOOK = [
    Offer(1.0, 1.0, 2),
    Offer(200.0, 100.0, 1270),
    Offer(59.0, 20.0, 271),
    Offer(3.0, 1.0, 52),
    Offer(20.0, 1.0, 5),
    Offer(25.0, 1.0, 119),
    Offer(50.0, 1.0, 1),
    Offer(50.0, 1.0, 83),
    Offer(90.0, 1.0, 3),
    Offer(150.0, 1.0, 10),
]


def test_a_bulk_offer_is_priced_per_unit():
    """480 chaos for 2 divine is a 240 rate, not a 480 one."""
    assert Offer(480.0, 2.0, 102).ratio == 240.0


# ---------------------------------------------------------------------------
# Bait rejection
# ---------------------------------------------------------------------------
def test_bait_at_the_cheap_end_is_rejected():
    """The four sub-3-chaos exalted offers are not a price anyone can fill."""
    kept = {round(o.ratio, 2) for o in reject_bait(EXALTED_BOOK)}
    assert kept == {20.0, 25.0, 50.0, 90.0}, (
        "1, 2, 2.95 and 3 are bait against a 22.5 centre; 150 is the mirror image"
    )


def test_a_real_spread_is_left_intact():
    """Divine trades in a 199-240 band. None of that is an outlier."""
    assert len(reject_bait(DIVINE_BOOK)) == len(DIVINE_BOOK)


def test_bait_is_judged_by_count_not_by_stock():
    """Stock is the part of an offer that is free to inflate.

    The live exalted book put 1270 units behind a 2-chaos offer -- more stock
    than every honest offer combined. If the centre were stock-weighted, that
    one offer would define the market and the real 20-50 cluster would be
    rejected as the outlier instead.
    """
    kept = reject_bait(EXALTED_BOOK)
    assert all(o.ratio > 5 for o in kept)


def test_a_book_too_short_to_judge_is_kept_whole():
    two = [Offer(1.0, 1.0, 1), Offer(300.0, 1.0, 1)]
    assert reject_bait(two) == two, "with two offers there is no centre to be off"


def test_offers_with_no_quantity_are_dropped():
    assert reject_bait([Offer(5.0, 0.0, 1)] * 3) == []


# ---------------------------------------------------------------------------
# The weighted median
# ---------------------------------------------------------------------------
def test_deep_offers_pull_the_rate_towards_themselves():
    """The whole point: 857 units at 229 outweighs 2 units at 220."""
    assert weighted_median(DIVINE_BOOK) == 229.0


def test_the_unweighted_median_is_the_number_being_moved_away_from():
    """Guards the size of the fix, not just its direction.

    Counting offers equally gives 219.5 -- roughly ten chaos under the market,
    which is exactly the error that was reported.
    """
    flat = [Offer(o.chaos, o.amount, 1) for o in DIVINE_BOOK]
    assert weighted_median(flat) == 219.0
    assert weighted_median(DIVINE_BOOK) - weighted_median(flat) == 10.0


def test_an_offer_without_stock_still_counts_once():
    """A missing stock figure must not silently delete the offer."""
    assert weighted_median([Offer(100.0, 1.0, 0)]) == 100.0


def test_an_empty_book_is_an_error_not_a_zero():
    with pytest.raises(ExchangeRateError):
        weighted_median([])


# ---------------------------------------------------------------------------
# End to end over the captured books
# ---------------------------------------------------------------------------
def test_the_divine_rate_lands_on_the_market_not_its_floor():
    quote = rate_from_offers(DIVINE_BOOK, "divine", "Allflame")
    assert quote.chaos == 229.0
    assert quote.offers_used == 10
    assert not quote.thin


def test_the_exalted_rate_ignores_the_bait_it_used_to_average_in():
    quote = rate_from_offers(EXALTED_BOOK, "exalted", "Allflame")
    assert quote.chaos == 25.0, "the real cluster, not the 1-3 chaos bait"
    assert quote.offers_seen == 10
    assert quote.offers_used == 5, "20, 25, 50, 50 and 90 survive"


def test_the_summary_says_how_many_offers_were_thrown_away():
    summary = rate_from_offers(EXALTED_BOOK, "exalted", "Allflame").summary()
    assert "5 rejected as bait" in summary


def test_a_thin_book_is_flagged_rather_than_hidden():
    quote = rate_from_offers([Offer(200.0, 1.0, 5)] * 3, "divine", "Allflame")
    assert quote.thin
    assert "thin book" in quote.summary()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _payload(*offers):
    return {
        "result": {
            str(i): {"listing": {"offers": [o]}} for i, o in enumerate(offers)
        }
    }


def _offer(chaos, amount, want="divine", stock=10):
    return {
        "exchange": {"currency": "chaos", "amount": chaos},
        "item": {"currency": want, "amount": amount, "stock": stock},
    }


def test_offers_are_read_out_of_a_response():
    offers = parse_offers(_payload(_offer(200, 1), _offer(220, 1)), "divine")
    assert [o.ratio for o in offers] == [200.0, 220.0]


def test_every_offer_on_a_listing_is_read_not_just_the_first():
    payload = {"result": {"a": {"listing": {"offers": [_offer(200, 1), _offer(210, 1)]}}}}
    assert len(parse_offers(payload, "divine")) == 2


def test_an_offer_in_another_currency_pair_is_skipped():
    assert parse_offers(_payload(_offer(200, 1, want="exalted")), "divine") == []


@pytest.mark.parametrize("bad", [None, 0, "", "nonsense"])
def test_a_malformed_amount_does_not_crash_the_read(bad):
    payload = _payload(_offer(200, bad))
    assert parse_offers(payload, "divine") == []


def test_an_empty_response_yields_nothing():
    assert parse_offers({}, "divine") == []
    assert parse_offers({"result": {}}, "divine") == []


# ---------------------------------------------------------------------------
# The status choice, which is the least obvious decision in the module
# ---------------------------------------------------------------------------
def test_the_book_is_read_online_not_any():
    """`any` returns more offers and a much worse answer.

    Measured live on 2026-08-24: `any` reported 176 exalted offers, returned the
    100 cheapest because the response caps there and the book is sorted
    ascending, and those ran 1 to 10 chaos with a median of 4 -- against a
    market of 25. `online` returns ten offers and all ten of them.

    This is here because widening the filter is the first thing anyone would try
    on a ten-offer sample, and the reason not to is invisible in the code.
    """
    from src.market.currency_source import DEFAULT_STATUS

    assert DEFAULT_STATUS == "online"


def test_the_truncated_any_page_is_what_the_online_book_avoids():
    """The bottom-100 page of a deep book, shaped from the live capture."""
    page = [Offer(r, 1.0, 10) for r in [1.0] * 16 + [2.0] * 17 + [3.0] * 11
            + [4.0] * 8 + [5.0] * 22 + [6.0] * 5 + [10.0] * 21]
    truncated = rate_from_offers(page, "exalted", "Allflame")
    honest = rate_from_offers(EXALTED_BOOK, "exalted", "Allflame")
    assert truncated.chaos < 6.0 and honest.chaos == 25.0, (
        "no estimator recovers a market from a page that excludes it -- the fix "
        "has to be the query, not the statistics"
    )
