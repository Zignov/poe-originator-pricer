"""Bulk Buy searches instant-buyout listings only.

The page used to search `any`, which is the whole market -- including sellers
who are offline and listings that need a whisper and a reply. For bulk buying
that is the wrong set. The lots are large and cheap, the cheap end goes in
seconds, and a lot you cannot take *right now* is not a lot you can buy; a scan
that fills the table with unreachable sellers spends the same rate limit to
answer a question nobody asked.

`securable` is how GGG's API spells the trade site's "Instant Buyout". Verified
against /api/trade/data/filters on 2026-11-22, where `status_filters.status`
offers exactly:

    available      Instant Buyout and In Person
    securable      Instant Buyout
    onlineleague   In Person (Online in League)
    online         In Person (Online)
    any            Any
"""
from __future__ import annotations

import pytest

from src.market.bulk import BULK_STATUS, scan_bulk_lots

#: Every value GGG's status filter accepts, so a typo in BULK_STATUS is caught
#: rather than silently returning nothing.
VALID_STATUSES = {"available", "securable", "onlineleague", "online", "any"}


class _Collector:
    league = "Covens"

    class converter:
        @staticmethod
        def to_chaos(amount, _currency):
            return float(amount or 0)

    def __init__(self):
        self.queries = []
        self.cancel = None

    def build_query(self, **kwargs):
        self.queries.append(kwargs)
        return {
            "query": {
                "status": {"option": kwargs.get("status", "any")},
                "filters": {},
            }
        }

    def search(self, _query):
        return "qid", [], 0

    def fetch(self, _batch, _qid):
        return []


def test_the_status_is_a_real_option_ggg_accepts():
    """A wrong string here returns zero results and looks like an empty market."""
    assert BULK_STATUS in VALID_STATUSES


def test_the_status_is_instant_buyout():
    assert BULK_STATUS == "securable"


def test_a_scan_asks_for_instant_buyout_only():
    collector = _Collector()
    scan_bulk_lots(collector, price_max=20.0, min_lot=5)
    assert collector.queries, "no query was built"
    assert collector.queries[0]["status"] == BULK_STATUS


def test_the_status_reaches_the_query_body():
    """Through `build_query`, where GGG actually reads it."""
    from src.market.collectors.official_trade_api import OfficialTradeApiCollector

    real = OfficialTradeApiCollector(
        league="Covens", user_agent="test", converter=None, map_tier=16
    )
    query = real.build_query(status=BULK_STATUS)
    assert query["query"]["status"] == {"option": "securable"}


def test_a_seller_recheck_keeps_the_same_status():
    """Re-checking one lot must not quietly widen the search."""
    collector = _Collector()
    scan_bulk_lots(collector, price_max=20.0, min_lot=5, seller="Someone#1234")
    assert collector.queries[0]["status"] == BULK_STATUS
    assert collector.queries[0]["account"] == "Someone#1234"


def test_instant_buyout_is_the_default_choice():
    """It is what the page opens on; the others are an escape hatch."""
    from src.market.bulk import BULK_STATUS_CHOICES

    first = next(iter(BULK_STATUS_CHOICES.values()))
    assert first == BULK_STATUS == "securable"


def test_every_offered_choice_is_a_real_ggg_option():
    from src.market.bulk import BULK_STATUS_CHOICES

    assert set(BULK_STATUS_CHOICES.values()) <= VALID_STATUSES


def test_a_widened_status_reaches_the_query():
    """The escape hatch has to actually work.

    Instant buyout is a severe narrowing -- measured live, 5 matching against
    98 for `any` on the same search -- so being able to widen it is the
    difference between a filter and a dead end.
    """
    collector = _Collector()
    scan_bulk_lots(collector, price_max=20.0, min_lot=5, status="any")
    assert collector.queries[0]["status"] == "any"


def test_the_trade_site_link_opens_the_same_search():
    """Otherwise the link shows a different set from the table above it.

    The page reports "N matching in total" from the scan and then offers a
    link; if the link carries a wider status, the count on GGG's page does not
    match the one on ours and neither number can be trusted.
    """
    from pathlib import Path

    from conftest import code_only

    code = code_only(Path("src/ui/bulk_buy.py"))
    # Both the scan path and the link builder must name the shared constant.
    assert code.count("BULK_STATUS") >= 2, (
        "the live-search link does not carry the same status as the scan"
    )


def test_only_this_page_is_restricted():
    """The training collector wants the broadest honest sample of asks.

    Restricting collection to instant-buyout listings would bias the dataset
    towards whatever sellers happen to enable it, which is a property of the
    seller rather than of the map.
    """
    from pathlib import Path

    from conftest import code_only

    assert "securable" not in code_only(Path("src/market/ingest.py"))
    assert "securable" not in code_only(Path("src/pricing/market_comparison.py"))


def test_the_page_says_it_is_filtering():
    """A filter that silently drops results is how a user concludes the market
    is empty when it is merely being narrowed for them."""
    from pathlib import Path

    source = Path("src/ui/bulk_buy.py").read_text(encoding="utf-8")
    assert "Instant Buyout" in source


def test_no_raw_html_helper_is_handed_markdown():
    """`hint()` and `page_header()`'s lede inject their text into a <div>.

    Markdown therefore never runs inside them, and `**bold**` reaches the user
    as literal asterisks. It is a small bug that looks like sloppiness on the
    one line a reader is most likely to actually read, and it shipped twice --
    on this page and on My Listings -- because nothing checked.
    """
    import io
    import re
    import tokenize
    from pathlib import Path

    def strings_and_code(path: Path) -> str:
        """Source with comments stripped but string literals kept.

        Both halves matter. The strings are what gets rendered, so they have to
        be checked -- while a comment explaining this very rule necessarily
        contains the asterisks it warns about, and would flag itself.
        """
        text = path.read_text(encoding="utf-8")
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except tokenize.TokenError:  # pragma: no cover - a syntax error fails elsewhere
            return text
        return " ".join(t.string for t in tokens if t.type != tokenize.COMMENT)

    offenders = []
    for path in Path("src/ui").glob("*.py"):
        source = strings_and_code(path)
        for match in re.finditer(r"\bhint\s*\(((?:[^()]|\([^()]*\))*)\)", source, re.S):
            if "**" in match.group(1):
                offenders.append(str(path))
    assert not offenders, (
        "these pass Markdown to a helper that emits raw HTML, so the asterisks "
        f"show up literally; use <b>/<i> instead: {offenders}"
    )
