"""The checks that say what is wrong when nothing looks wrong.

Every one of these is a real failure from a single league turnover, and every
one produced plausible numbers rather than an error. That is what makes them
worth a banner: the app's actual failure mode is not crashing, it is pricing
confidently against the wrong thing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.ui.health import (
    ERROR,
    INFO,
    THIN_MODEL_ROWS,
    WARN,
    age_hours,
    check_listings,
    check_model,
    check_model_league,
    check_rate,
    check_unknown_rewards,
    findings,
    unknown_rewards_in,
)


class _Predictor:
    def __init__(self, rows=5000, league="Covens"):
        self.metadata = {"league": league}
        self.training_features = type("A", (), {"shape": (rows, 7)})()


# -- the one that hid for a whole league ------------------------------------
def test_a_model_from_another_league_is_reported():
    """Allflame's model pricing Covens maps. Nothing said so anywhere."""
    found = check_model_league("Covens", {"league": "Allflame"})
    assert found is not None
    assert "Allflame" in found.title and "Covens" in found.title
    assert "train" in found.fix


def test_a_matching_league_says_nothing():
    assert check_model_league("Covens", {"league": "Covens"}) is None


@pytest.mark.parametrize("metadata", [None, {}, {"league": None}, {"league": ""}])
def test_a_model_that_does_not_record_its_league_is_not_accused(metadata):
    """Silence is not evidence of a mismatch, and a false alarm here would
    teach the user to ignore the banner."""
    assert check_model_league("Covens", metadata) is None


def test_an_unset_league_is_not_accused_either():
    assert check_model_league(None, {"league": "Allflame"}) is None


def test_the_league_mismatch_is_a_warning_not_an_error():
    """It still prices, and there may be nothing better early in a league."""
    assert check_model_league("Covens", {"league": "Allflame"}).severity == WARN


# -- the next rename --------------------------------------------------------
def test_unrecognised_rewards_are_an_error():
    """A reward read as 0 is not a degraded price, it is a wrong one."""
    found = check_unknown_rewards({"Atlas Drops": 197, "More Threads": 69})
    assert found.severity == ERROR
    assert "Atlas Drops" in found.detail and "More Threads" in found.detail
    assert "PROPERTY_TO_FEATURE" in found.fix


@pytest.mark.parametrize("names", [None, {}])
def test_no_unrecognised_rewards_says_nothing(names):
    assert check_unknown_rewards(names) is None


def test_unknown_rewards_are_found_in_real_payload_shapes():
    payloads = [
        '{"item": {"properties": [{"name": "[New|Atlas Shards]", "type": 999, '
        '"values": [["+60%", 1]]}]}}',
        '{"properties": [{"name": "Item Rarity", "type": 3, "values": [["+92%", 1]]}]}',
    ]
    assert unknown_rewards_in(payloads) == {"Atlas Shards": 1}


def test_the_current_names_are_not_flagged():
    """The whole point of having fixed the parser."""
    payload = (
        '{"properties": ['
        '{"name": "[AtlasDropScaling|Atlas Drops]", "type": 112, "values": [["+84%", 1]]},'
        '{"name": "More [Thread|Threads]", "type": 89, "values": [["+35%", 1]]}]}'
    )
    assert unknown_rewards_in([payload]) == {}


@pytest.mark.parametrize(
    "payload",
    ["{not json", "", None, "[]", '{"item": null}', '{"properties": []}', "null"],
)
def test_a_payload_that_cannot_be_read_is_skipped_not_raised(payload):
    """This runs on a page load; it may not take the page down."""
    assert unknown_rewards_in([payload]) == {}


def test_the_sample_is_bounded():
    """It runs on every page load, so it must not walk the whole table."""
    payload = '{"properties": [{"name": "[X|Atlas Shards]", "type": 9, "values": [["+1%", 1]]}]}'
    assert unknown_rewards_in([payload] * 500, limit=5) == {"Atlas Shards": 5}


# -- data and rate ----------------------------------------------------------
def test_no_listings_at_all_is_an_error():
    found = check_listings("Covens", 0, None, 7.0)
    assert found.severity == ERROR
    assert "collect-market" in found.fix


def test_data_older_than_the_pricing_window_is_reported():
    """Comparables past max_age_days are excluded, so the panels empty out."""
    found = check_listings("Covens", 5000, 24 * 90, 7.0)
    assert found is not None
    assert "90 days" in found.title


def test_fresh_data_says_nothing():
    assert check_listings("Covens", 5000, 3.0, 7.0) is None


def test_a_rate_that_was_never_fetched_is_reported():
    """config.yaml shipped 210 while the market was at 125, then at 209."""
    found = check_rate(210.0, None, "config")
    assert found.severity == WARN
    assert "config.yaml" in found.title
    assert "update-rate" in found.fix


def test_a_missing_rate_is_an_error():
    assert check_rate(None, None, None).severity == ERROR
    assert check_rate(0.0, None, None).severity == ERROR


def test_a_stale_fetched_rate_is_only_informational():
    """It was measured once; it is just old."""
    assert check_rate(209.0, 24 * 5, "GGG Currency Exchange").severity == INFO


def test_a_fresh_fetched_rate_says_nothing():
    assert check_rate(209.0, 0.5, "GGG Currency Exchange") is None


# -- the model --------------------------------------------------------------
def test_no_model_is_an_error_carrying_its_reason():
    found = check_model(None, "no model has been trained yet")
    assert found.severity == ERROR
    assert "no model has been trained yet" in found.detail


def test_a_thin_model_is_informational_not_a_warning():
    """It prices, and its confidence already degrades honestly."""
    found = check_model(_Predictor(rows=THIN_MODEL_ROWS - 1))
    assert found.severity == INFO
    assert "collect-market" in found.fix


def test_a_well_fed_model_says_nothing():
    assert check_model(_Predictor(rows=THIN_MODEL_ROWS + 1)) is None


def test_a_predictor_with_no_readable_shape_does_not_raise():
    assert check_model(type("P", (), {"metadata": {}})()) is None


# -- the whole banner -------------------------------------------------------
def test_todays_broken_state_produces_every_finding():
    """The exact state the app was in this morning, replayed."""
    found = findings(
        configured_league="Covens",
        model_metadata={"league": "Allflame"},
        predictor=_Predictor(rows=9085, league="Allflame"),
        listing_total=0,
        newest_listing_hours=None,
        max_age_days=7.0,
        divine_rate=210.0,
        rate_fetched_hours=None,
        rate_source="config",
        unknown_rewards={"Atlas Drops": 197},
    )
    text = " | ".join(f"{f.title} {f.detail}" for f in found)
    assert "Atlas Drops" in text
    assert "Allflame" in text
    assert "No listings" in text
    assert "config.yaml" in text
    # Four distinct problems, four distinct findings -- not one merged blob.
    assert len(found) == 4


def test_a_healthy_install_shows_nothing_at_all():
    """A banner that is always on is a banner nobody reads."""
    assert findings(
        configured_league="Covens",
        model_metadata={"league": "Covens"},
        predictor=_Predictor(rows=5000),
        listing_total=5000,
        newest_listing_hours=2.0,
        max_age_days=7.0,
        divine_rate=209.1,
        rate_fetched_hours=0.5,
        rate_source="GGG Currency Exchange",
        unknown_rewards={},
    ) == []


def test_findings_are_ordered_worst_first():
    found = findings(
        configured_league="Covens",
        model_metadata={"league": "Allflame"},
        predictor=_Predictor(rows=5000),
        listing_total=0,
        divine_rate=209.0,
        rate_fetched_hours=0.1,
        rate_source="GGG Currency Exchange",
        unknown_rewards={"Atlas Shards": 3},
    )
    severities = [f.severity for f in found]
    assert severities == sorted(severities, key=lambda s: {ERROR: 0, WARN: 1, INFO: 2}[s])
    assert severities[0] == ERROR


# -- timestamps -------------------------------------------------------------
def test_age_handles_the_three_shapes_a_timestamp_arrives_in():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    assert age_hours(now - timedelta(hours=5), now) == pytest.approx(5.0)
    assert age_hours("2026-11-22T07:00:00+00:00", now) == pytest.approx(5.0)
    # Naive strings are treated as UTC rather than rejected; the database
    # writes both shapes depending on how old the row is.
    assert age_hours("2026-11-22T07:00:00", now) == pytest.approx(5.0)


@pytest.mark.parametrize("value", [None, "", "not a date", 42, []])
def test_an_unreadable_timestamp_is_unknown_rather_than_zero(value):
    """Zero would read as "just now", which is the opposite of the truth."""
    assert age_hours(value) is None
