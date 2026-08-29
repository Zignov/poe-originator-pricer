"""The Market Data page while a collection is in flight.

The page used to run the collection on the script thread inside `st.spinner`,
which meant there was no thread left to notice a click: any Cancel button drawn
beside it could not be read until the run it was meant to interrupt had already
finished. The run now goes on a `CollectJob` and the page becomes a view of it.

Three things about that view are worth pinning, and none of them is the layout:

* the run starts and the page reruns *immediately*, so the Cancel button is on
  screen before the first request goes out rather than after the last one;
* the headline is computed in one place, because the inline path and the
  polling fragment both draw it;
* a cancelled collection does not go on to retrain, however it was started.

Nothing here opens a socket: the collection is never actually started.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.ui.market_data as page

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class _Update:
    """The fields `collect_headline` reads off a ProgressUpdate."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# A cancel must not drag a retrain in behind it
# ---------------------------------------------------------------------------
def test_cancelling_a_collection_does_not_also_trigger_the_paired_retrain():
    """"Collect and retrain" is two actions, and cancel stops both.

    Pressing Cancel is a request to stop spending time, so answering it by
    starting a training run the user then has to sit through is the opposite of
    what was asked -- and worse, it trains on a sample that stopped part way
    through the stratification. The workflow is cancel, look, then press
    "Retrain on stored data" when you actually want it.
    """
    cancelled = {"inserted": 120, "updated": 4, "cancelled": True}
    assert page.should_retrain_after(cancelled, requested=True) is False
    assert page.should_retrain_after(cancelled, requested=False) is False


def test_a_collection_that_ran_to_the_end_still_retrains_when_asked():
    finished = {"inserted": 500, "updated": 20, "cancelled": False}
    assert page.should_retrain_after(finished, requested=True) is True
    assert page.should_retrain_after(finished, requested=False) is False


def test_a_collection_that_errored_does_not_retrain_on_nothing():
    failed = {"error": "leagues endpoint returned HTTP 503", "inserted": 0}
    assert page.should_retrain_after(failed, requested=True) is False


# ---------------------------------------------------------------------------
# The headline the panel and the page share
# ---------------------------------------------------------------------------
def test_the_progress_headline_counts_from_what_was_already_stored():
    fraction, headline = page.collect_headline(
        _Update(fraction=0.5, collected=250, target=500, slice_index=3, slice_count=16),
        already=4000,
    )
    assert fraction == 0.5
    assert "4250 maps indexed" in headline
    assert "250 of 500 this run" in headline
    assert "query 4/16" in headline


def test_the_headline_survives_a_collector_that_only_sends_a_message():
    """ProgressUpdate stringifies to the old plain message on purpose.

    A collector that reports text and nothing else has to render rather than
    raise, which is why every field is read with getattr.
    """
    fraction, headline = page.collect_headline("slice 3 of 16", already=10)
    assert fraction == 0.0
    assert "10 maps indexed" in headline


# ---------------------------------------------------------------------------
# The page as a view of the job
# ---------------------------------------------------------------------------
def test_the_page_reruns_as_soon_as_the_run_starts():
    """Without the rerun the Cancel button only paints once the run is over.

    This is the same trick the Stash Pricer's live-run panel uses, and the same
    reason: the branch that draws the panel is above the branch that starts the
    job, so the script has to be sent back through it.
    """
    source = Path(page.__file__).read_text(encoding="utf-8")
    start = source.index("CollectJob.start")
    tail = source[start : start + 700]
    assert "st.rerun()" in tail, (
        "starting the run must be followed by a rerun, or the panel that draws "
        "Cancel is never reached until the run has already ended"
    )


def test_the_collection_no_longer_runs_on_the_script_thread():
    """A regression guard on the one change that makes cancelling possible."""
    source = Path(page.__file__).read_text(encoding="utf-8")
    assert "collect_from_trade_api(" not in source, (
        "the page is calling ingest directly again; that holds the session's "
        "only thread for the length of a rate-limited walk"
    )


def test_the_panel_offers_cancel_and_a_look_at_what_landed():
    """Driven through Streamlit's own harness, against a job that never runs.

    The stub reports itself as running and reports progress, so the fragment
    draws exactly what it would during a real collection -- without a socket
    anywhere in it.
    """

    class StubJob:
        running = True
        finished = False
        cancelling = False
        cancelled = False
        waiting_for_lock = False
        error = ""
        stats = None
        progress = _Update(
            fraction=0.25, collected=125, target=500, slice_index=2, slice_count=16
        )

        def cancel(self):  # pragma: no cover - not reached in this test
            pass

    app = AppTest.from_file(APP, default_timeout=240)
    app.run()
    app.session_state["collect_job"] = StubJob()
    app.session_state["collect_baseline"] = 4000
    app.sidebar.radio[0].set_value("Market Data").run()

    assert not app.exception, [str(e) for e in app.exception]
    labels = [b.label for b in app.button]
    assert "Cancel collection" in labels
    assert "Show what has landed" in labels
    # And the buttons that would start a *second* concurrent walk are gone.
    assert "Collect from trade API" not in labels
    assert "Collect and retrain" not in labels


def test_the_panel_says_when_it_is_queued_behind_another_collection():
    """A run that is being polite must not look like a run that is stuck.

    Only one collection reads the market at a time -- two rate limiters, neither
    aware of the other's requests, is how the published policy gets broken by a
    tool that believes it is obeying it. A job waiting on that lock has no
    progress to show, so it says why.
    """

    class QueuedJob:
        running = True
        finished = False
        cancelling = False
        cancelled = False
        waiting_for_lock = True
        error = ""
        stats = None
        progress = None

        def cancel(self):  # pragma: no cover - not reached in this test
            pass

    app = AppTest.from_file(APP, default_timeout=240)
    app.run()
    app.session_state["collect_job"] = QueuedJob()
    app.sidebar.radio[0].set_value("Market Data").run()

    assert not app.exception, [str(e) for e in app.exception]
    said = " ".join(c.value for c in app.caption)
    assert "one collection" in said.lower() or "passive" in said.lower()
