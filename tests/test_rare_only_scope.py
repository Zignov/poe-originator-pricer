"""The model prices *rare* Originator Maps, and only those.

A magic map holds one prefix and one suffix; a rare holds three of each. They
are not the same product priced differently -- they are different products. In
a 4,746-row sample the magic listings averaged 18c against a rare market that
runs from roughly 30c to 3 divine, and carried roughly a third of the reward
values. Mixed into training they teach the model that low reward totals are
normal, and the exalt verdict -- which assumes the rare 3+3 affix ceiling --
is simply wrong about them.

So the rule is enforced in three places, because each one leaks differently:
the search query (nothing new arrives), classify() (nothing already held gets
priced), and the outlier pass (rows already in the database leave training).
"""
from __future__ import annotations

from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.parsing.item_parser import classify
from src.schema import NormalizedMap


def _collector():
    return OfficialTradeApiCollector(
        league="Allflame", user_agent="test", converter=None
    )


# -- 1. the search never asks for anything else ------------------------------
def test_the_search_query_asks_gggs_server_for_rare_only():
    """Verified against /api/trade/data/filters on 2026-08-25: the key is
    type_filters.filters.rarity.option, and "rare" is a valid option id."""
    query = _collector().build_query()
    rarity = query["query"]["filters"]["type_filters"]["filters"]["rarity"]
    assert rarity == {"option": "rare"}


def test_the_rarity_filter_survives_every_slice_variation():
    """The slices override sort, price bounds and stats. None of them may drop
    the scope filter -- one slice collecting magic maps re-poisons the set."""
    from src.market.collectors.official_trade_api import default_slices

    collector = _collector()
    for spec in default_slices():
        filters = collector.build_query(**spec)["query"]["filters"]
        assert filters["type_filters"]["filters"]["rarity"]["option"] == "rare", spec


# -- 2. nothing already in hand gets priced ----------------------------------
def _map(frame: str) -> NormalizedMap:
    return NormalizedMap(
        name="Originator Map",
        is_originator=True,
        map_tier=16,
        corrupted=False,
        item_rarity_frame=frame,
        quantity=80,
    )


def test_a_magic_map_is_unsupported():
    result = classify(_map("Magic"))
    assert not result.supported
    assert "rare" in result.unsupported_reason.lower()


def test_a_normal_map_is_unsupported():
    assert not classify(_map("Normal")).supported


def test_a_rare_map_is_still_priced():
    assert classify(_map("Rare")).supported


def test_an_unknown_rarity_is_not_rejected():
    """Clipboard text and CSV imports do not always carry the header. Refusing
    to price a map because a field was missing would be a regression in the
    common case, and the rewards -- which are what the model reads -- are all
    present."""
    assert classify(_map("")).supported


def test_the_rarity_check_is_case_insensitive():
    assert classify(_map("rare")).supported


# -- 3. rows already in the database leave training --------------------------
def _row(**over):
    from src.schema import FEATURE_NAMES

    row = {
        "id": 1,
        "price_chaos": 40.0,
        "price_currency": "chaos",
        "indexed_at": None,
        "captured_at": None,
        "item_rarity_frame": "Rare",
    }
    row.update({name: 10 for name in FEATURE_NAMES})
    row.update(over)
    return row


def test_a_stored_magic_listing_is_flagged_out_of_training():
    from src.market.outliers import evaluate

    (_, is_outlier, reason), = evaluate([_row(item_rarity_frame="Magic")])
    assert is_outlier
    assert "rare" in reason.lower()


def test_a_stored_rare_listing_is_kept():
    from src.market.outliers import evaluate

    (_, is_outlier, _), = evaluate([_row()])
    assert not is_outlier


def test_a_stored_row_with_no_recorded_rarity_is_kept():
    """Rows captured before the field was populated must not all vanish from
    the training set the first time the outlier pass is re-run."""
    from src.market.outliers import evaluate

    (_, is_outlier, _), = evaluate([_row(item_rarity_frame="")])
    assert not is_outlier
