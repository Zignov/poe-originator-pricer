"""The Min value bar on the Stash Pricer.

Three separate claims, and the feature was broken on all three:

1. The bar has to reach the **grid**. It only ever filtered the table beside it,
   so typing a number re-drew the list and left the stash tab looking exactly as
   it had -- which is indistinguishable from a control that does nothing.
2. The bar has to be compared in **one unit**. `fmt.value` picks its unit per
   amount, so under "auto" it hands back chaos for an ordinary map and divine
   for one above the 2000c threshold; a bar of 40 then silently threw away the
   most valuable map in the tab.
3. A map under the bar with **open affixes** is not really under the bar. A 33c
   map with two empty slots is a 40c map to anyone holding an Exalted Orb, and
   hiding it defeats the half of this page that exists to find those.
"""
from __future__ import annotations

import pytest

from src.ml.predict import Estimate
from src.pricing.display import AUTO_DIVINE_THRESHOLD_CHAOS, PriceFormatter
from src.pricing.exalt import ExaltVerdict
from src.pricing.fair_value import PricedMap
from src.schema import NormalizedMap
from src.ui.stash_pricer import _apply_filters


def _map(chaos: float, *, x: int = 0, y: int = 0, confidence: str = "High") -> PricedMap:
    return PricedMap(
        item=NormalizedMap(
            stash_item_id=f"m{x}-{y}-{chaos}", x=x, y=y, is_originator=True, map_tier=16,
            supported=True,
        ),
        estimate=Estimate(
            fair_value_chaos=chaos,
            interval_low_chaos=chaos * 0.8,
            interval_high_chaos=chaos * 1.2,
            confidence=confidence,
            confidence_score=0.9,
        ),
        suggested_chaos=chaos,
    )


def _filter(priced, min_value, fmt=None, verdicts=None):
    return _apply_filters(
        priced,
        fmt or PriceFormatter(unit="chaos"),
        min_value,
        only_confident=False,
        reward_filter="Any",
        exaltable_only=False,
        verdicts=verdicts,
    )


# -- the bar itself ---------------------------------------------------------
def test_a_map_over_the_bar_is_kept_and_one_under_it_is_not():
    over, under = _map(80.0), _map(12.0)
    assert _filter([over, under], 40.0) == [over]


def test_a_map_exactly_on_the_bar_is_kept():
    """"40 and above" is what the control says, so 40 passes."""
    exact = _map(40.0)
    assert _filter([exact], 40.0) == [exact]


def test_a_bar_of_zero_filters_nothing():
    maps = [_map(1.0), _map(500.0)]
    assert _filter(maps, 0.0) == maps


# -- one unit, whatever the display currency is -----------------------------
def test_an_expensive_map_survives_a_chaos_bar_under_auto():
    """The regression. Under "auto", `fmt.value` returns *divine* above 2000c.

    So a 3000c map came back as ~13 against a bar of 40 and was dropped -- the
    filter removing precisely the map it exists to find.
    """
    expensive = _map(AUTO_DIVINE_THRESHOLD_CHAOS + 1000.0)
    cheap = _map(12.0)
    kept = _filter([expensive, cheap], 40.0, fmt=PriceFormatter(unit="auto"))
    assert kept == [expensive]


def test_a_divine_bar_is_read_in_divine():
    """Under "divine" the input is labelled (d), so 1.0 means one divine."""
    fmt = PriceFormatter(unit="divine", divine_to_chaos=200.0)
    over, under = _map(250.0), _map(150.0)
    assert _filter([over, under], 1.0, fmt=fmt) == [over]


def test_the_input_label_and_the_comparison_agree_on_the_unit():
    """`suffix(None)` is what the number is typed in; `to_chaos` must match it.

    Under "auto" the old label said "d" while the comparison was in chaos for
    anything ordinary -- so the box asked for divine and acted on chaos.
    """
    for unit in ("chaos", "divine", "auto"):
        fmt = PriceFormatter(unit=unit, divine_to_chaos=200.0)
        typed = 2.0
        expected = typed * (200.0 if fmt.suffix(None) == "d" else 1.0)
        assert fmt.to_chaos(typed, fmt.unit_for(None)) == pytest.approx(expected)


# -- the exalt exception ----------------------------------------------------
def _verdict(expected_value: float | None, *, available: bool = True, net: float = 5.0):
    return ExaltVerdict(
        available=available, expected_value_chaos=expected_value, net_gain_chaos=net
    )


def test_a_map_under_the_bar_is_kept_when_exalting_would_clear_it():
    """The headline case: 33c now, 45c once its two open slots are filled."""
    exaltable = _map(33.0)
    kept = _filter([exaltable], 40.0, verdicts={id(exaltable): _verdict(45.0)})
    assert kept == [exaltable]


def test_a_map_whose_exalt_still_leaves_it_under_the_bar_is_dropped():
    """The exception is not "any map with open affixes" -- the value has to clear."""
    weak = _map(33.0)
    assert _filter([weak], 40.0, verdicts={id(weak): _verdict(36.0)}) == []


def test_a_map_that_cannot_be_exalted_gets_no_exception():
    """Corrupted, full, or affixes unreadable: judged on what it is worth now."""
    full = _map(33.0)
    assert _filter([full], 40.0, verdicts={id(full): _verdict(None, available=False)}) == []


def test_a_map_with_no_verdict_at_all_gets_no_exception():
    assert _filter([_map(33.0)], 40.0, verdicts={}) == []


def test_the_exception_reads_expected_value_not_whether_the_orb_pays():
    """A losing exalt still tells you what the map would be worth.

    `Min value` asks what a map is worth; whether spending the orb is a good
    trade is the ring's question and the `Exaltable only` checkbox's. A map that
    would be worth 60c after an orb belongs in a 40c list even if the orb costs
    more than it gains.
    """
    losing = _map(33.0)
    verdict = _verdict(60.0, net=-8.0)
    assert not verdict.worth_it
    assert _filter([losing], 40.0, verdicts={id(losing): verdict}) == [losing]


def test_the_exalt_exception_is_measured_in_chaos_under_auto():
    """The unit bug and the exception must not undo each other."""
    exaltable = _map(AUTO_DIVINE_THRESHOLD_CHAOS - 100.0)
    fmt = PriceFormatter(unit="auto")
    verdicts = {id(exaltable): _verdict(AUTO_DIVINE_THRESHOLD_CHAOS + 500.0)}
    assert _filter([exaltable], 40.0, fmt=fmt, verdicts=verdicts) == [exaltable]


# -- the bar reaches the grid ----------------------------------------------
def _render(priced, fmt, **kwargs) -> str:
    """Draw the grid and hand back the HTML it emitted."""
    import src.ui.stash_grid as grid

    captured: list[str] = []
    real = grid.st.markdown
    grid.st.markdown = lambda html, **kw: captured.append(html)
    try:
        grid.render_grid(priced, fmt, **kwargs)
    finally:
        grid.st.markdown = real
    return "".join(captured)


def test_a_filtered_tile_is_faded_rather_than_removed():
    """A hole in the grid reads as an empty slot, not as "did not match".

    The grid is read positionally against the real stash tab -- it is what tells
    you which slot to click -- so a tile the filter excluded has to still be
    *there*, just quiet.
    """
    kept, excluded = _map(80.0, x=0, y=0), _map(12.0, x=1, y=0)
    html = _render(
        [kept, excluded], PriceFormatter(unit="chaos"), filtered_out={id(excluded)}
    )
    assert html.count("stash-cell filled") == 2, "a filtered tile must still be drawn"
    assert "filled filtered" in html


def test_an_unfiltered_grid_fades_nothing():
    html = _render([_map(80.0), _map(12.0, x=1)], PriceFormatter(unit="chaos"))
    assert "filtered" not in html


def test_filtering_does_not_re_shade_the_tiles_that_remain():
    """Bands are percentiles of the whole tab, so a bar must not move them.

    Computing them over the survivors instead would re-colour every remaining
    map each time the number changed, which makes the colours mean nothing.
    """
    maps = [_map(10.0, x=0), _map(50.0, x=1), _map(300.0, x=2)]
    fmt = PriceFormatter(unit="chaos")
    before = _render(maps, fmt)
    after = _render(maps, fmt, filtered_out={id(maps[0])})
    for colour in {line for line in before.split("background:")[1:]}:
        swatch = colour.split(";")[0]
        assert swatch in after, f"tile colour {swatch} changed when the filter was set"
