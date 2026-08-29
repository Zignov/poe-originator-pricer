"""An unrecorded seller is an unknown identity, not a shared one."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.ml.validation import grouped_split


def _frame(sellers) -> pd.DataFrame:
    return pd.DataFrame({"seller": sellers, "price_chaos": np.arange(len(sellers), dtype=float)})


def test_blank_sellers_do_not_collapse_into_one_group():
    """The failure this guards: the bundled sample's blanked seller column.

    All of it used to become a single group, land wholesale on one side of the
    shuffle, and leave validation with a handful of rows.
    """
    train, valid, test = grouped_split(_frame([""] * 400))
    assert len(train) + len(valid) + len(test) == 400
    assert len(valid) > 40 and len(test) > 40


def test_missing_sellers_are_treated_the_same_as_blank_ones():
    train, valid, test = grouped_split(_frame([None] * 400))
    assert len(valid) > 40 and len(test) > 40


def test_a_blanked_sample_next_to_real_sellers_still_splits():
    """A seeded database that has since collected: 400 anonymous, 25 named."""
    sellers = [""] * 400 + [f"seller{i}" for i in range(25) for _ in range(4)]
    train, valid, test = grouped_split(_frame(sellers))
    assert len(train) + len(valid) + len(test) == len(sellers)
    assert len(valid) > 30 and len(test) > 30


def test_named_sellers_are_still_kept_whole():
    """The leakage guard the grouping exists for is untouched."""
    sellers = [f"seller{i}" for i in range(40) for _ in range(5)]
    frame = _frame(sellers)
    train, valid, test = grouped_split(frame)
    for a, b in ((train, valid), (train, test), (valid, test)):
        assert not set(frame.loc[a, "seller"]) & set(frame.loc[b, "seller"])
