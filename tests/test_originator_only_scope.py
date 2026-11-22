"""Excluding other influences is a *buying* filter, not a training one.

A map can be Originator and Shaper, Elder or one of the four Conquerors at the
same time. Those are real maps with real rolls, worth about 2.7% of the market
(4,072 listings against 3,961 with them excluded, measured live 2026-11-22).

Where that distinction is allowed to matter is the whole point of this file:

* **Bulk Buy excludes them.** The question there is "what do I want to own".
* **Collection does not.** Narrowing what the model is trained on is a decision
  about the model, and not one a search filter should make quietly.
* **`classify` does not.** Refusing to price a map the user is holding, because
  it carries an extra influence that usually makes it worth *more*, leaves them
  with no number at all -- which is worse than a number with a caveat.

The third point is here because it was briefly got wrong: an earlier version
marked such maps Unsupported, which turned "do not search for these" into
"refuse to price mine".
"""
from __future__ import annotations

import pytest

from src.market.collectors.official_trade_api import OfficialTradeApiCollector
from src.parsing.item_parser import classify, other_influence, parse_item_text
from src.schema import ORIGINATOR_IMPLICIT_STAT_ID, OTHER_INFLUENCE_STAT_IDS


def _collector():
    return OfficialTradeApiCollector(
        league="Covens", user_agent="test", converter=None, map_tier=16
    )


def _not_groups(query):
    return [g for g in query["query"]["stats"] if g.get("type") == "not"]


def _map_text(extra: str = "") -> str:
    return (
        "Item Class: Maps\nRarity: Rare\nDusk Voyage\nMap (Tier 16)\n"
        "--------\n"
        "Atlas Drops: +84%\nItem Rarity: +134%\nMonster Pack Size: +32%\n"
        "--------\n"
        + (extra + "\n" if extra else "")
        + "Area is Influenced by the Originator's Memories"
    )


# -- the search filter ------------------------------------------------------
def test_it_is_off_by_default():
    """Default off means collection keeps the broadest honest sample."""
    assert _not_groups(_collector().build_query()) == []


def test_opting_in_excludes_every_other_influence():
    groups = _not_groups(_collector().build_query(exclude_other_influence=True))
    assert len(groups) == 1
    assert [f["id"] for f in groups[0]["filters"]] == list(OTHER_INFLUENCE_STAT_IDS)


def test_the_originator_requirement_is_never_replaced_by_it():
    query = _collector().build_query(exclude_other_influence=True)
    assert query["query"]["stats"][0] == {
        "type": "and",
        "filters": [{"id": ORIGINATOR_IMPLICIT_STAT_ID}],
    }


def test_all_six_influences_are_covered():
    """Shaper, Elder and the four Conquerors. Missing one leaks that map in."""
    assert len(OTHER_INFLUENCE_STAT_IDS) == 6
    assert len(set(OTHER_INFLUENCE_STAT_IDS)) == 6
    # Shared stat ids distinguished by the `|n` suffix, so a bare id would be
    # the wrong filter rather than a broader one.
    assert all("|" in sid for sid in OTHER_INFLUENCE_STAT_IDS)


def test_extra_stats_survive_alongside_the_exclusion():
    query = _collector().build_query(
        exclude_other_influence=True,
        extra_stats=[{"type": "and", "filters": [{"id": "pseudo.x"}]}],
    )
    kinds = [g["type"] for g in query["query"]["stats"]]
    assert kinds.count("and") == 2 and kinds.count("not") == 1


# -- who opts in ------------------------------------------------------------
def test_bulk_buy_opts_in():
    from pathlib import Path

    from conftest import code_only

    assert "exclude_other_influence" in code_only(Path("src/market/bulk.py"))


def test_collection_and_market_comparison_do_not():
    """"Don't touch training" -- enforced, not just intended."""
    from pathlib import Path

    from conftest import code_only

    for path in ("src/market/ingest.py", "src/pricing/market_comparison.py"):
        assert "exclude_other_influence" not in code_only(Path(path)), (
            f"{path} narrows what is collected; that is a decision about the "
            "model, not a search filter"
        )


# -- what classify must NOT do ---------------------------------------------
@pytest.mark.parametrize(
    "extra",
    [
        "Area is influenced by The Elder",
        "Area is influenced by The Shaper",
        "Map is occupied by The Constrictor",
        "Map is occupied by The Purifier",
    ],
)
def test_a_map_with_another_influence_is_still_priced(extra):
    """The overreach that was reverted.

    These are worth more, not less. Refusing to price one leaves the user with
    nothing for the best map in their tab.
    """
    priced = classify(parse_item_text(_map_text(extra)), map_tier=16)
    assert priced.supported, priced.unsupported_reason
    assert priced.unsupported_reason == ""


def test_a_pure_originator_map_is_priced_too():
    assert classify(parse_item_text(_map_text()), map_tier=16).supported


def test_the_helper_still_reports_the_influence():
    """Kept for reporting; it just must not drive eligibility."""
    assert other_influence(parse_item_text(_map_text("Area is influenced by The Elder")))
    assert not other_influence(parse_item_text(_map_text()))


def test_classify_does_not_consult_it():
    """A source check, because the failure is a line reappearing."""
    import inspect

    assert "other_influence" not in inspect.getsource(classify)
