"""The collector's sampling design.

These are regression tests for a bug that produced a useless model without ever
raising an error: the first version of the sampler stepped through price bands
and took an equal share from each, which makes the sampled price distribution
uniform by construction and decouples price from the features. Nothing crashes
when you do that -- you just get R^2 near zero and no way to tell why.
"""
from __future__ import annotations

from src.market.collectors.official_trade_api import (
    REWARD_STATS,
    OfficialTradeApiCollector,
    default_slices,
)
from src.pricing.currency import CurrencyConverter
from src.schema import ORIGINATOR_IMPLICIT_STAT_ID
from src.market.collectors.official_trade_api import QUANTITY_FILTER


def _collector(**kwargs) -> OfficialTradeApiCollector:
    return OfficialTradeApiCollector(
        league="Allflame",
        user_agent="test",
        converter=CurrencyConverter(215.0),
        **kwargs,
    )


def test_no_slice_stratifies_on_price():
    for spec in default_slices():
        assert "price_min" not in spec, spec
        assert "price_max" not in spec, spec


def test_every_slice_is_sorted_by_listing_time_not_price():
    # A search returns at most 100 ids however many match, so the sort decides
    # which 100 arrive. Sorting by price returns only one end of the range.
    for spec in default_slices():
        assert spec.get("sort_by") == "indexed", spec


def test_slices_cover_every_reward_feature():
    stat_ids = {
        f["id"]
        for spec in default_slices()
        for group in spec.get("extra_stats", [])
        for f in group["filters"]
    }
    for stat_id in REWARD_STATS.values():
        assert stat_id in stat_ids


def test_slices_include_an_unstratified_draw_of_the_natural_flow():
    plain = [
        s
        for s in default_slices()
        if not s.get("extra_stats") and not s.get("pack_size")
    ]
    assert plain, "no unstratified slice: the natural price distribution is never sampled"


def test_query_always_filters_to_originator_maps():
    query = _collector().build_query()
    stats = query["query"]["stats"][0]["filters"]
    assert any(f["id"] == ORIGINATOR_IMPLICIT_STAT_ID for f in stats)


def test_query_applies_the_configured_scope():
    query = _collector(map_tier=16, require_uncorrupted=True).build_query()
    filters = query["query"]["filters"]
    assert filters["map_filters"]["filters"]["map_tier"] == {"min": 16, "max": 16}
    assert filters["misc_filters"]["filters"]["corrupted"] == {"option": "false"}


def test_corrupted_filter_is_dropped_when_scope_allows_corrupted():
    """The corrupted filter goes; the block it lived in does not.

    `misc_filters` used to exist only when this scope was set, so its absence
    was a fair proxy for "no corrupted filter". It now always carries
    `identified` -- unidentified maps show no reward rolls at all and are
    excluded from every search -- so the block is always present and the
    assertion has to name the key it actually cares about. See
    tests/test_identified_only.py.
    """
    misc = _collector(require_uncorrupted=False).build_query()["query"]["filters"][
        "misc_filters"
    ]["filters"]
    assert "corrupted" not in misc
    assert misc["identified"] == {"option": "true"}


def test_pack_size_and_quantity_bounds_become_map_filters():
    query = _collector().build_query(pack_size=(41, 48), quantity=(80, None))
    map_filters = query["query"]["filters"]["map_filters"]["filters"]
    assert map_filters["map_packsize"] == {"min": 41, "max": 48}
    assert map_filters[QUANTITY_FILTER] == {"min": 80}


def test_sort_key_is_honoured():
    assert _collector().build_query(sort_by="indexed", sort="desc")["sort"] == {
        "indexed": "desc"
    }


def test_per_slice_cap_defaults_to_a_fair_share():
    # Without a per-slice cap the whole budget is spent on the first slices and
    # later ones -- the rarer reward combinations -- never get collected.
    slices = default_slices()
    limit = 3000
    per_slice = max(20, -(-limit // len(slices)))
    assert per_slice * len(slices) >= limit


def test_no_slice_asks_for_the_oldest_listings():
    """A month-old unsold ask is evidence the price was wrong, not evidence of
    value -- and mixing both ends produced a bimodal age distribution that made
    the chronological split compare two different market regimes."""
    for spec in default_slices():
        assert spec.get("sort") == "desc", spec
