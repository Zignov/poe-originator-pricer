"""Every trade search asks for identified maps only.

An unidentified map hides all of its reward rolls. The Originator implicit is
still visible -- it is an implicit -- so these come back from an Originator
search looking entirely plausible, while carrying no quantity, no rarity, no
pack size and nothing any part of this app can read.

They are not harmless. They parse as seven zeroes, the outlier pass flags them
"no reward values on the item", and they are discarded after the request has
already been paid for. In a 10,918-listing Covens sample, **164 of the 166**
rows carrying that reason were unidentified maps. On a search under 20c the
filter halved the results: 6 matching without it, 3 with.

Requests are the scarce resource on this endpoint, so the right place to drop
a row whose only destiny is to be thrown away is the query, not the parser.
"""
from __future__ import annotations

import pytest

from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.schema import ORIGINATOR_IMPLICIT_STAT_ID


def _collector(**kwargs):
    base = dict(league="Covens", user_agent="test", converter=None, map_tier=16)
    return OfficialTradeApiCollector(**{**base, **kwargs})


def _misc(query):
    return query["query"]["filters"].get("misc_filters", {}).get("filters", {})


def test_every_query_requires_an_identified_map():
    assert _misc(_collector().build_query())["identified"] == {"option": "true"}


def test_it_survives_uncorrupted_being_switched_off():
    """The two are independent; one must not take the other with it.

    They used to share a line -- `misc_filters` was only created at all when
    `require_uncorrupted` was set -- so writing this carelessly would silently
    drop the identified filter for anyone who allows corrupted maps.
    """
    misc = _misc(_collector(require_uncorrupted=False).build_query())
    assert misc["identified"] == {"option": "true"}
    assert "corrupted" not in misc


def test_uncorrupted_still_works_alongside_it():
    misc = _misc(_collector(require_uncorrupted=True).build_query())
    assert misc["corrupted"] == {"option": "false"}
    assert misc["identified"] == {"option": "true"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"price_max": 20.0},
        {"account": "Someone#1234"},
        {"status": "securable"},
        {"collapse": False, "sort_by": "indexed", "sort": "desc"},
        {"quantity": (80, 100)},
    ],
)
def test_no_query_shape_loses_the_filter(kwargs):
    """Every caller builds through here, so one escape route is enough to leak."""
    assert _misc(_collector().build_query(**kwargs))["identified"] == {"option": "true"}


def test_the_originator_implicit_is_still_required():
    """It always was, and that is worth stating.

    The unidentified maps were *not* a case of the Originator filter being
    missing -- they carry the implicit, which is exactly why they looked like
    valid results. Removing the stat filter while fixing the other thing would
    turn every search into a sweep of all T16 rares.
    """
    stats = _collector().build_query()["query"]["stats"]
    assert stats[0] == {"type": "and", "filters": [{"id": ORIGINATOR_IMPLICIT_STAT_ID}]}


def test_extra_stats_are_added_after_the_implicit_not_instead_of_it():
    query = _collector().build_query(
        extra_stats=[{"type": "and", "filters": [{"id": "pseudo.x"}]}]
    )
    stats = query["query"]["stats"]
    assert len(stats) == 2
    assert stats[0]["filters"][0]["id"] == ORIGINATOR_IMPLICIT_STAT_ID
