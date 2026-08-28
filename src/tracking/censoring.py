"""One listing's history, turned into observations that admit what they don't know.

Two things make this more than bookkeeping.

**Every price is its own episode.** A map listed at 180c for three hours and then
marked down to 120c and sold in ten minutes is not one data point. It is two: a
non-sale at 180c that lasted three hours, and a sale at 120c that took ten
minutes. Throwing the first away discards the clearest evidence there is that
180c was too high -- and it is evidence we paid three hours of shelf time for.

**A sale is an interval.** Polling stops when the app closes, so a map last seen
at 12:20 and missing at 21:00 sold somewhere in between, and nothing here narrows
it further. The observation carries `lo_hours` and `hi_hours` and lets the
estimator decide what a nine-hour bracket is worth -- which is something, just
not much. Recording the midpoint would turn "we don't know" into a confident
wrong number, and the fit would believe it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

APPEARED = "appeared"
REPRICED = "repriced"
GONE = "gone"
REAPPEARED = "reappeared"


@dataclass(frozen=True)
class Observation:
    """One price, for one stretch of time, that did or did not end in a sale."""

    price_chaos: float
    predicted_chaos: float | None
    ratio: float | None
    #: Sold no earlier than this many hours after the price was set.
    lo_hours: float
    #: Sold no later than this. None means right-censored: no sale yet.
    hi_hours: float | None
    sold: bool


def _hours(a: datetime, b: datetime) -> float:
    return max((a - b).total_seconds() / 3600.0, 0.0)


def observations_for(
    row,
    events: Sequence[dict],
    predicted_chaos: float | None,
    now: datetime,
) -> list[Observation]:
    """Split one tracked listing into one observation per price it wore."""
    ordered = sorted(events, key=lambda event: event["at"])

    # A reappearance means the sale before it never happened. Drop both, so the
    # episode continues across the blip instead of being cut into two.
    cleaned: list[dict] = []
    for event in ordered:
        if event["event"] == REAPPEARED:
            for index in range(len(cleaned) - 1, -1, -1):
                if cleaned[index]["event"] == GONE:
                    cleaned.pop(index)
                    break
            continue
        cleaned.append(event)

    episodes: list[tuple[datetime, float | None]] = []
    for event in cleaned:
        if event["event"] in (APPEARED, REPRICED):
            episodes.append(
                (datetime.fromisoformat(event["at"]), event["new_price_chaos"])
            )
    if not episodes:
        return []

    sold = row.status == "gone" and row.gone_first_missing is not None

    out: list[Observation] = []
    for index, (start, price) in enumerate(episodes):
        if price is None:
            continue
        ratio = (
            price / predicted_chaos
            if predicted_chaos not in (None, 0)
            else None
        )
        is_last = index == len(episodes) - 1

        if not is_last:
            # It ended because you marked it down, not because it sold.
            end = episodes[index + 1][0]
            out.append(
                Observation(
                    price_chaos=price,
                    predicted_chaos=predicted_chaos,
                    ratio=ratio,
                    lo_hours=_hours(end, start),
                    hi_hours=None,
                    sold=False,
                )
            )
            continue

        if sold:
            out.append(
                Observation(
                    price_chaos=price,
                    predicted_chaos=predicted_chaos,
                    ratio=ratio,
                    lo_hours=_hours(row.last_seen, start),
                    hi_hours=_hours(row.gone_first_missing, start),
                    sold=True,
                )
            )
        else:
            out.append(
                Observation(
                    price_chaos=price,
                    predicted_chaos=predicted_chaos,
                    ratio=ratio,
                    lo_hours=_hours(now, start),
                    hi_hours=None,
                    sold=False,
                )
            )
    return out


def all_observations(rows, events_by_listing, predictions, now) -> list[Observation]:
    """Every episode across every tracked listing, minus what cannot be trusted.

    Ambiguous rows -- two of your maps that cannot be told apart -- and rows you
    marked as "not a sale" are dropped here rather than filtered downstream, so
    there is one place where the question "what is the fit allowed to see" is
    answered.
    """
    out: list[Observation] = []
    for row in rows:
        if getattr(row, "ambiguous", False):
            continue
        out.extend(
            observations_for(
                row,
                events_by_listing.get(row.id, []),
                predictions.get(row.id),
                now,
            )
        )
    return out
