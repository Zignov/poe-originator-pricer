"""Reading one account's listing history out of the database, once.

Two callers need this and they must not disagree: the My Listings page, which
shows you the measurement, and the pricing seam, which acts on it. If those
assembled their own observations the page could report a factor of 1.05 while
the prices moved by something else, and there would be nothing on screen to
reveal it.

So this is the single path from rows and events to observations. It is thin --
queries and a call into `censoring` -- and the judgement all lives in the pure
modules it delegates to.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.tracking.calibration import (
    CalibrationConfig,
    PersonalCalibration,
    personal_factor,
)
from src.tracking.censoring import Observation, all_observations

#: Sales older than this stop describing the market you are selling into now.
DEFAULT_MAX_AGE_DAYS = 60


def observations_for_account(
    repo,
    account: str,
    league: str,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> list[Observation]:
    """Every price episode this account has on record, censored as it stands."""
    live = list(repo.live_tracked_rows(account, league))
    gone = list(
        repo.recent_gone_rows(account, league, since=now - timedelta(days=max_age_days))
    )
    rows = live + gone
    if not rows:
        return []

    predictions: dict[int, float | None] = {}
    for status in ("live", "gone"):
        for row in repo.tracked_rows_for_page(account, league, status=status):
            predictions[row["id"]] = row["predicted_chaos"]

    events: dict[int, list[dict]] = {}
    for event in repo.tracked_events([row.id for row in rows if row.id is not None]):
        events.setdefault(event["listing_id"], []).append(dict(event))

    return all_observations(rows, events, predictions, now)


def calibration_for_account(
    repo,
    account: str,
    league: str,
    model_version: str,
    cfg: CalibrationConfig,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> PersonalCalibration:
    """What this account's own sales say its prices should be scaled by."""
    if not account:
        return PersonalCalibration(
            source="not applied: no account name set", model_version=model_version
        )
    observations = observations_for_account(repo, account, league, now, max_age_days)
    return personal_factor(observations, model_version, cfg)
