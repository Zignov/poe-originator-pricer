"""Things that are wrong but do not look wrong.

Every check here is one that actually happened, in one session, on a league
turnover -- and every one of them produced plausible numbers rather than an
error. That is the common shape and the reason this module exists: the app's
real failure mode is not crashing, it is quietly pricing against the wrong
thing while every page continues to look exactly as it should.

What went wrong, in order of how long it took to notice:

- **The model was trained on a different league than the one being priced.**
  Allflame's model pricing Covens maps. Nothing says so anywhere; the sidebar
  shows a league and a model version and never that they disagree.
- **A reward property was renamed and silently read as zero.** `Item Quantity`
  became `Atlas Drops`, the parser knew neither the name nor the new type id,
  and every map in the league read `quantity: 0`. Near the training floor that
  barely moves a price, so the symptom was good maps pricing low.
- **The divine rate was 40% wrong.** Fetched during an hour whose cheapest
  executed trade was an outlier, stored, and then used to scale every
  divine-denominated price until someone who knew the real rate looked at it.
- **The data was three months old.** A model is only as current as its market,
  and nothing on the page said when the market was last read.

The checks are pure functions over already-loaded objects, so they cost
nothing per rerun and can be tested without a display. Each carries the command
that fixes it, because "your model is from another league" is only half of
something useful.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

#: Worst first. The page shows them in this order.
ERROR = "error"
WARN = "warn"
INFO = "info"
_RANK = {ERROR: 0, WARN: 1, INFO: 2}

#: A model fitted on fewer rows than this is reported as thin. Not a failure --
#: it still prices, and the confidence output already degrades honestly -- but
#: it is worth knowing before trusting a number to the chaos.
THIN_MODEL_ROWS = 800

#: A rate older than this is reported. Currency moves fast enough early in a
#: league that a day-old divine rate can be meaningfully wrong, and it
#: multiplies into every price shown in divine.
STALE_RATE_HOURS = 24.0

#: How many recent payloads to re-read when looking for reward names this build
#: does not know. Small on purpose: it runs on a page load, and a rename shows
#: up on every map in the league, so a handful is as conclusive as a thousand.
REWARD_SAMPLE = 40


@dataclass(frozen=True)
class Finding:
    """One thing worth saying, and what to do about it."""

    severity: str
    title: str
    detail: str = ""
    fix: str = ""

    @property
    def rank(self) -> int:
        return _RANK.get(self.severity, 9)


def _age_hours(stamp: Any, now: datetime) -> float | None:
    """Hours since `stamp`, which arrives as a string or a datetime or None."""
    if stamp is None:
        return None
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp)
        except ValueError:
            return None
    if not isinstance(stamp, datetime):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds() / 3600.0


def check_model_league(configured: str | None, metadata: dict[str, Any] | None) -> Finding | None:
    """The one that hid for a whole league.

    A model trained on another league's market is not approximately right, it
    is answering a different question: the same map was worth 40c there and 79c
    here. It is still *usable* -- there may be nothing better until a new league
    has enough data -- so this is a warning with the retrain command on it
    rather than a refusal to price.
    """
    trained = (metadata or {}).get("league")
    if not trained or not configured or trained == configured:
        return None
    return Finding(
        WARN,
        f"The model was trained on {trained}, not {configured}",
        f"Every price on this page comes from {trained}'s market. Reward values "
        f"mean different things in different leagues, and prices move a long way "
        f"between them.",
        "Collect listings for this league, then `python -m src.cli train`.",
    )


def check_listings(configured: str | None, total: int, fresh_hours: float | None,
                   max_age_days: float | None) -> Finding | None:
    """Is there any market data for the league being priced, and is it current?"""
    if total <= 0:
        return Finding(
            ERROR,
            f"No listings collected for {configured}",
            "Comparable prices and the market-comparison mode both read from "
            "this league's listings, and there are none.",
            "`python -m src.cli collect-market --limit 500`",
        )
    if fresh_hours is None:
        return None
    days = fresh_hours / 24.0
    limit = float(max_age_days or 7.0)
    if days > limit:
        return Finding(
            WARN,
            f"The newest listing is {days:.0f} days old",
            f"Comparables older than {limit:.0f} days are excluded from pricing, "
            "so the comparison panels will thin out or empty.",
            "`python -m src.cli collect-market --limit 500`",
        )
    return None


def check_rate(rate: float | None, fetched_hours: float | None,
               source: str | None) -> Finding | None:
    """The divine rate multiplies into every price shown in divine."""
    if not rate or rate <= 0:
        return Finding(
            ERROR,
            "No divine/chaos rate",
            "Prices cannot be shown in divine without it.",
            "`python -m src.cli update-rate`",
        )
    if source and "config" in str(source).lower():
        return Finding(
            WARN,
            f"The divine rate ({rate:.0f}c) is the one written in config.yaml",
            "It has never been fetched, so it is whatever the file was shipped "
            "or last edited with rather than what a divine currently trades at.",
            "`python -m src.cli update-rate`",
        )
    if fetched_hours is not None and fetched_hours > STALE_RATE_HOURS:
        return Finding(
            INFO,
            f"The divine rate was last fetched {fetched_hours / 24:.0f} days ago",
            f"It is {rate:.0f}c. Currency moves fastest early in a league.",
            "`python -m src.cli update-rate`",
        )
    return None


def check_model(predictor: Any, model_error: str = "") -> Finding | None:
    if predictor is None:
        return Finding(
            ERROR,
            "No trained model",
            model_error or "Model-mode pricing is unavailable until one is trained.",
            "`python -m src.cli train`",
        )
    rows = 0
    try:
        rows = int(predictor.training_features.shape[0])
    except (AttributeError, IndexError, TypeError):
        rows = 0
    if 0 < rows < THIN_MODEL_ROWS:
        return Finding(
            INFO,
            f"The model was fitted on {rows:,} listings",
            "It prices, and its confidence scores already reflect how little it "
            "has to go on, but more data is the single biggest improvement "
            "available to it.",
            "`python -m src.cli collect-market --limit 2500`, then train again.",
        )
    return None


def check_unknown_rewards(names: dict[str, int] | None) -> Finding | None:
    """Reward properties this build cannot read, found in recent listings.

    This is the early warning for the next league rename. A reward the parser
    does not recognise is read as zero -- not as missing -- so the model prices
    the map as though it genuinely had none of that reward, and nothing
    anywhere says otherwise.
    """
    if not names:
        return None
    listed = ", ".join(sorted(names))
    return Finding(
        ERROR,
        "Unrecognised reward properties in recent listings",
        f"{listed} — read as 0 on every map that has them, which makes those "
        "maps price low. This is what a league rename looks like.",
        "Add them to PROPERTY_TO_FEATURE in src/schema.py, then "
        "`python scripts/reparse_listings.py --dry-run`.",
    )


def unknown_rewards_in(payloads: Any, limit: int = REWARD_SAMPLE) -> dict[str, int]:
    """Count unrecognised reward names across a few stored trade payloads."""
    import json
    from collections import Counter

    from src.parsing.item_parser import parse_api_item

    found: Counter[str] = Counter()
    for index, blob in enumerate(payloads or ()):
        if index >= limit:
            break
        try:
            payload = json.loads(blob) if isinstance(blob, (str, bytes)) else blob
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        item = payload.get("item", payload)
        if not isinstance(item, dict) or not item.get("properties"):
            continue
        try:
            found.update(parse_api_item(item).unknown_rewards)
        except Exception:
            continue
    return dict(found)


def findings(
    *,
    configured_league: str | None,
    model_metadata: dict[str, Any] | None,
    predictor: Any,
    model_error: str = "",
    listing_total: int = 0,
    newest_listing_hours: float | None = None,
    max_age_days: float | None = None,
    divine_rate: float | None = None,
    rate_fetched_hours: float | None = None,
    rate_source: str | None = None,
    unknown_rewards: dict[str, int] | None = None,
) -> list[Finding]:
    """Every finding, worst first.

    Takes already-loaded values rather than fetching anything itself, so it
    costs nothing on a rerun and can be tested without a database.
    """
    out = [
        check_unknown_rewards(unknown_rewards),
        check_model(predictor, model_error),
        check_model_league(configured_league, model_metadata),
        check_listings(configured_league, listing_total, newest_listing_hours, max_age_days),
        check_rate(divine_rate, rate_fetched_hours, rate_source),
    ]
    return sorted((f for f in out if f is not None), key=lambda f: f.rank)


def age_hours(stamp: Any, now: datetime | None = None) -> float | None:
    """Public wrapper, so callers do not reimplement timestamp handling."""
    return _age_hours(stamp, now or datetime.now(timezone.utc))
