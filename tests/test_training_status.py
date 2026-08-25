"""Telling the user whether the model has seen the data in the database.

Nothing retrains on its own -- `model.retrain_interval_hours` is a stored
preference no code path acts on -- so collecting listings does not change any
price until Retrain is pressed. Before this existed the dashboard showed only
*when* the model trained, which cannot answer the question that actually comes
up after a collection run: is the price I am reading built on what I collected?
"""
from __future__ import annotations

import pytest

from src.ui.dashboard import STALE_ROW_MARGIN, _training_status


class _Recorder:
    """Captures what the panel would render, without a Streamlit runtime."""

    def __init__(self):
        self.warnings: list[str] = []
        self.captions: list[str] = []

    def markdown(self, body, **kwargs):
        self.warnings.append(body)

    def caption(self, body, **kwargs):
        self.captions.append(body)


@pytest.fixture
def st(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr("src.ui.dashboard.st", recorder)
    return recorder


def _counts(total, outliers=0):
    return {"total": total, "outliers": outliers, "last_24h": 0}


def test_a_model_behind_the_data_is_called_out(st):
    _training_status(_counts(2622), {"dataset_size": 1696})
    assert st.warnings and not st.captions
    body = st.warnings[0]
    assert "1,696" in body and "2,622" in body
    assert "926" in body, "the gap is the number that makes it actionable"
    assert "Retrain" in body


def test_a_current_model_says_so_rather_than_staying_silent(st):
    """Silence reads as "something is wrong but I cannot tell what"."""
    _training_status(_counts(1700), {"dataset_size": 1696})
    assert st.captions and not st.warnings
    assert "Model is current" in st.captions[0]
    assert "only happens when you ask" in st.captions[0]


def test_no_model_at_all_is_a_different_message(st):
    _training_status(_counts(500), None)
    assert "No model trained yet" in st.warnings[0]


def test_outliers_are_not_counted_as_trainable_rows(st):
    """Training uses the usable rows, so the comparison has to as well --
    otherwise a flagged-outlier pass looks like new data to learn from."""
    _training_status(_counts(2000, outliers=400), {"dataset_size": 1600})
    assert st.captions, "1600 usable against 1600 trained is current"


@pytest.mark.parametrize(
    "behind,expect_warning",
    [(STALE_ROW_MARGIN - 1, False), (STALE_ROW_MARGIN, True)],
)
def test_the_margin_stops_a_handful_of_rows_from_nagging(st, behind, expect_warning):
    _training_status(_counts(1000 + behind), {"dataset_size": 1000})
    assert bool(st.warnings) is expect_warning


def test_a_model_ahead_of_the_data_does_not_warn(st):
    """Re-flagging outliers can shrink the usable set below what was trained on.
    That is not staleness and must not be reported as a negative backlog."""
    _training_status(_counts(1500), {"dataset_size": 1696})
    assert not st.warnings
    assert st.captions


def test_a_missing_dataset_size_does_not_crash_the_dashboard(st):
    _training_status(_counts(2000), {"dataset_size": None})
    assert st.warnings or st.captions


# -- the floor made the two numbers mean different things --------------------
class _Repo:
    def __init__(self, prices):
        self._prices = prices

    def usable_prices(self, league=None):
        return list(self._prices)


class _Cfg:
    def get(self, *_a, **_k):
        return "Allflame"


def test_a_freshly_trained_model_is_not_reported_as_behind(monkeypatch):
    """The regression this exists for.

    dataset_size counts rows *fitted*; the dashboard counted rows *stored*.
    Once the training floor held back the reroll pile the two stopped matching,
    so a run that had just finished successfully was reported as 1,368 listings
    behind -- every time, however often you retrained. It looked exactly like
    training not saving.
    """
    import src.ui.dashboard as page

    warnings, captions = [], []
    monkeypatch.setattr(page.st, "markdown", lambda msg, **k: warnings.append(msg))
    monkeypatch.setattr(page.st, "caption", lambda msg, **k: captions.append(msg))

    prices = [10.0] * 1368 + [100.0] * 2734       # exactly the shipped dataset
    counts = {"total": 4746, "outliers": 644}     # 4,102 usable
    page._training_status(counts, {"dataset_size": 2734}, _Repo(prices), _Cfg())

    assert not warnings, f"reported as behind after a successful train: {warnings}"
    assert captions and "current" in captions[0]


def test_the_held_back_rows_are_explained_not_just_missing():
    """Two unequal numbers with no reason given is what made this look broken."""
    import src.ui.dashboard as page

    note = page._floor_note({"total": 4746, "outliers": 644}, 2734)
    assert "1,368" in note and "30c" in note


def test_real_new_data_is_still_reported_as_behind(monkeypatch):
    """The fix must not paper over the case the status line was built for."""
    import src.ui.dashboard as page

    warnings = []
    monkeypatch.setattr(page.st, "markdown", lambda msg, **k: warnings.append(msg))
    monkeypatch.setattr(page.st, "caption", lambda msg, **k: None)

    prices = [100.0] * 3000                       # all trainable, model saw 2,734
    page._training_status({"total": 3000, "outliers": 0}, {"dataset_size": 2734},
                          _Repo(prices), _Cfg())
    assert warnings and "behind the data" in warnings[0]


def test_it_survives_a_repository_that_cannot_answer(monkeypatch):
    """A dashboard that raises is worse than one that falls back."""
    import src.ui.dashboard as page

    class Broken:
        def usable_prices(self, league=None):
            raise RuntimeError("no such column")

    assert page._trainable_count({"total": 100, "outliers": 10}, Broken(), _Cfg()) == 90
