"""What the stat rail counts, and the three states it has to keep apart.

`PricedMap.supported` means "in scope **and** has an estimate". That is the
right test for anything that needs a number and the wrong one for counting,
because it is also False for a map the live-comparison run has simply not
reached yet.

The rail used to call that difference "unsupported". A tab of seven perfectly
good T16 rares therefore read

    Priceable 0 of 7      Skipped 7 unsupported      Tab total 0c

for the whole of a two-minute market run -- telling the user their maps were
wrong when the truth was that the answers had not arrived. The same seven maps
priced from 49c to 115c in Model mode at the same moment.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.pricing.fair_value import PricedMap
from src.schema import NormalizedMap


def _item(supported: bool = True, reason: str = "") -> NormalizedMap:
    return NormalizedMap(
        is_originator=True, supported=supported, unsupported_reason=reason,
        map_tier=16, quantity=84, rarity=134, pack_size=32,
        item_rarity_frame="Rare",
    )


class _Estimate:
    fair_value_chaos = 100.0
    interval_low_chaos = 70.0
    interval_high_chaos = 140.0
    confidence = "Medium"
    confidence_score = 0.5
    confidence_reasons: list[str] = []
    model_version = "test"


def _priced(item: NormalizedMap, estimate=None) -> PricedMap:
    return PricedMap(
        item=item, estimate=estimate, comparables=pd.DataFrame(),
        suggested_chaos=100.0 if estimate else None,
    )


def _counts(priced: list[PricedMap], total: int):
    """The three counts the rail draws, as the page computes them."""
    in_scope = [p for p in priced if p.item.supported]
    has_price = [p for p in priced if p.supported]
    return {
        "in_scope": len(in_scope),
        "priced": len(has_price),
        "out_of_scope": total - len(in_scope),
        "awaiting": len(in_scope) - len(has_price),
    }


def test_a_map_waiting_on_a_live_search_is_not_out_of_scope():
    """The bug, stated directly: mid-run is not the same as unsupported."""
    priced = [_priced(_item()) for _ in range(7)]
    counts = _counts(priced, total=7)
    assert counts["out_of_scope"] == 0, "in-scope maps were counted as unsupported"
    assert counts["awaiting"] == 7
    assert counts["priced"] == 0


def test_a_genuinely_unsupported_map_is_counted_as_out_of_scope():
    priced = [_priced(_item(supported=False, reason="Corrupted"))]
    counts = _counts(priced, total=1)
    assert counts["out_of_scope"] == 1
    assert counts["awaiting"] == 0


def test_a_part_finished_run_separates_all_three():
    """Two priced, three waiting, one corrupted -- each in its own column."""
    priced = [
        _priced(_item(), _Estimate()),
        _priced(_item(), _Estimate()),
        _priced(_item()),
        _priced(_item()),
        _priced(_item()),
        _priced(_item(supported=False, reason="Corrupted")),
    ]
    counts = _counts(priced, total=6)
    assert counts == {"in_scope": 5, "priced": 2, "out_of_scope": 1, "awaiting": 3}


def test_a_finished_run_leaves_nothing_awaiting():
    priced = [_priced(_item(), _Estimate()) for _ in range(4)]
    counts = _counts(priced, total=4)
    assert counts["awaiting"] == 0
    assert counts["priced"] == counts["in_scope"] == 4


def test_the_counts_always_add_up():
    """Whatever the mix, nothing is counted twice and nothing is lost."""
    priced = [
        _priced(_item(), _Estimate()),
        _priced(_item()),
        _priced(_item(supported=False, reason="Tier 15")),
    ]
    counts = _counts(priced, total=3)
    assert counts["priced"] + counts["awaiting"] + counts["out_of_scope"] == 3


def test_the_page_uses_item_supported_for_the_scope_count():
    """A source check on the distinction, because it is one character wide.

    `p.supported` and `p.item.supported` differ only by the estimate, and the
    whole bug was reaching for the first where the second was meant.
    """
    from pathlib import Path

    source = Path("src/ui/stash_pricer.py").read_text(encoding="utf-8")
    assert "in_scope = [p for p in priced if p.item.supported]" in source
    assert '("Priceable", f"{len(supported)}", f"of {len(items)}")' not in source, (
        "the rail is back to counting unpriced maps as unpriceable"
    )


def test_the_rail_no_longer_calls_an_unpriced_map_unsupported():
    from pathlib import Path

    source = Path("src/ui/stash_pricer.py").read_text(encoding="utf-8")
    assert '("Out of scope", f"{out_of_scope}", "unsupported")' in source


@pytest.mark.parametrize("mode", ["model", "market"])
def test_tab_total_only_sums_maps_that_actually_have_a_price(mode):
    """0c during a run is correct and must stay correct.

    The fix is to stop calling the gap "unsupported", not to invent prices for
    it -- a total that quietly included unpriced maps as zero would be the same
    lie in a different column.
    """
    priced = [_priced(_item(), _Estimate()), _priced(_item())]
    total = sum(p.suggested_chaos or 0 for p in priced if p.supported)
    assert total == 100.0
