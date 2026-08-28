# Listing Tracker (Learning) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the tracked listing history into a fitted answer to "at what price do my maps actually clear, and how fast" — then use it for alerts, for the reprice recommendation, and for a personal calibration factor on suggested listing price.

**Architecture:** Every listing episode becomes an interval- or right-censored observation. A Turnbull-style EM fit over a coarse (price-ratio × time) grid turns those into `P(sells within W | ratio)`. Alerts, reprice and calibration all read that one fitted object, so the censoring is handled once and correctly rather than three times approximately. Everything is pure and tested against synthetic data with a known truth.

**Tech Stack:** Python 3.11+, numpy (already a dependency), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-28-listing-tracker-design.md` (§§4, 7, 8-alerts, 9)

**Prerequisite plan:** `docs/superpowers/plans/2026-08-28-listing-tracker-core.md` must be complete. This plan consumes `TrackedRow`, the `tracked_listing_events` table, `Recommendation` and the My Listings page.

## Global Constraints

- **Never midpoint a censored interval.** A sale known only to be in `[20 min, 9 h]` contributes to every bucket it spans, weighted; it is never recorded as having sold at 4h40m. Spec §4.
- **Alerts require a decisive bracket.** "Sold fast" fires only when the *whole* bracket lies below the threshold. A straddling bracket makes no claim on screen but still constrains the fit. Spec §4.
- The personal factor scales **suggested listing price only**, never the fair-value estimate. Spec §9.
- Guards mirror `SANE_MARKET_RATIO` in `src/pricing/sell_strategy.py`: refuse and report the reason rather than silently applying a correction that cannot be believed.
- A calibration is bound to the `model_version` it was fitted against and must not be carried across a model change. Spec §9.
- Minimum ~20 sold listings **with evidence on both sides**. Fast sales alone bracket the ratio only from below and fix no upper bound. Spec §9.
- Factor clamped to `[0.8, 1.3]`. Spec §9, §10.
- Rows with `exclude_from_learning = 1` or `ambiguous = 1` never enter the fit. Spec §3, §4.
- No new entries in `requirements.txt`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/tracking/censoring.py` | **Create** — rows + events → censored observations |
| `src/tracking/survival.py` | **Create** — observations → hazard grid → `P(sells within W)` |
| `src/tracking/alerts.py` | **Create** — rows + fit → alerts, with observed thresholds |
| `src/tracking/reprice.py` | **Modify** — add the fit-derived recommendation beside the ladder |
| `src/tracking/calibration.py` | **Create** — fit → personal factor + source string |
| `src/pricing/sell_strategy.py` | **Modify** — `personal` field on `StrategyMultipliers` |
| `src/ui/my_listings.py` | **Modify** — alerts section and the bias report |
| `src/ui/settings.py` | **Modify** — calibration on/off |
| `src/ui/common.py` | **Modify** — sidebar alert badge |

---

### Task 1: Censored observations

**Files:**
- Create: `src/tracking/censoring.py`
- Test: `tests/test_tracking_censoring.py`

**Interfaces:**
- Consumes: `TrackedRow` from `src/tracking/reconcile.py`; event rows from `Repository.tracked_events`
- Produces:
  - `Observation(price_chaos, predicted_chaos, ratio, lo_hours, hi_hours, sold)`
  - `observations_for(row, events, now) -> list[Observation]`
  - `all_observations(rows, events_by_listing, now) -> list[Observation]`

Each price a map wore is one episode, and each episode is one observation. An
episode that ended in a re-price is a right-censored non-sale at that price: it
did *not* sell there, which is exactly as informative as the sale that ended the
last episode.

- [ ] **Step 1: Write the failing test**

```python
"""Turning a listing's history into observations that admit what they don't know."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.tracking.censoring import observations_for
from src.tracking.reconcile import TrackedRow

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _row(price=120.0, priced_at=T0, last_seen=T0, status="live",
         gone_first_missing=None, predicted=100.0):
    row = TrackedRow(
        id=1, trade_listing_id="abc", fingerprint="fp", price_chaos=price,
        priced_at=priced_at, first_seen=T0, last_seen=last_seen, status=status,
        gone_first_missing=gone_first_missing,
    )
    return row, predicted


def _event(at, event, old=None, new=None):
    return {"at": at.isoformat(), "event": event,
            "old_price_chaos": old, "new_price_chaos": new}


def test_a_live_listing_is_right_censored():
    row, predicted = _row(last_seen=T0 + timedelta(hours=5))
    obs = observations_for(row, [_event(T0, "appeared", new=120.0)], predicted,
                           now=T0 + timedelta(hours=5))
    assert len(obs) == 1
    assert obs[0].sold is False
    assert obs[0].lo_hours == pytest.approx(5.0)
    assert obs[0].hi_hours is None, "still live: no upper bound exists"


def test_a_sale_is_an_interval_not_a_number():
    row, predicted = _row(
        last_seen=T0 + timedelta(minutes=20), status="gone",
        gone_first_missing=T0 + timedelta(hours=9),
    )
    obs = observations_for(row, [_event(T0, "appeared", new=120.0)], predicted,
                           now=T0 + timedelta(hours=10))
    assert obs[0].sold is True
    assert obs[0].lo_hours == pytest.approx(20 / 60)
    assert obs[0].hi_hours == pytest.approx(9.0)


def test_the_ratio_is_the_ask_over_the_model():
    row, predicted = _row(price=120.0, predicted=100.0)
    obs = observations_for(row, [_event(T0, "appeared", new=120.0)], predicted, now=T0 + timedelta(hours=1))
    assert obs[0].ratio == pytest.approx(1.2)


def test_a_markdown_makes_two_observations_and_the_first_is_a_non_sale():
    """The three hours it spent at 180c without selling is evidence in itself."""
    repriced_at = T0 + timedelta(hours=3)
    row, predicted = _row(price=120.0, priced_at=repriced_at,
                          last_seen=repriced_at + timedelta(minutes=10),
                          status="gone",
                          gone_first_missing=repriced_at + timedelta(minutes=20))
    events = [_event(T0, "appeared", new=180.0),
              _event(repriced_at, "repriced", old=180.0, new=120.0)]
    obs = observations_for(row, events, predicted, now=repriced_at + timedelta(hours=1))

    assert len(obs) == 2
    first, second = obs
    assert first.price_chaos == 180.0 and first.sold is False
    assert first.lo_hours == pytest.approx(3.0), "it survived three hours at 180c"
    assert second.price_chaos == 120.0 and second.sold is True
    assert second.hi_hours == pytest.approx(20 / 60), "clock runs from the markdown"


def test_two_markdowns_make_three_observations():
    a, b = T0 + timedelta(hours=2), T0 + timedelta(hours=6)
    row, predicted = _row(price=100.0, priced_at=b, last_seen=b + timedelta(hours=1))
    events = [_event(T0, "appeared", new=180.0),
              _event(a, "repriced", old=180.0, new=140.0),
              _event(b, "repriced", old=140.0, new=100.0)]
    obs = observations_for(row, events, predicted, now=b + timedelta(hours=1))
    assert [o.price_chaos for o in obs] == [180.0, 140.0, 100.0]
    assert [o.sold for o in obs] == [False, False, False]


def test_a_map_with_no_prediction_has_no_ratio_but_still_has_a_time():
    row, predicted = _row(predicted=None)
    obs = observations_for(row, [_event(T0, "appeared", new=120.0)], predicted,
                           now=T0 + timedelta(hours=4))
    assert obs[0].ratio is None
    assert obs[0].lo_hours == pytest.approx(4.0)


def test_a_reappearance_cancels_the_sale_it_followed():
    """A sold map cannot come back, so the 'gone' was a mistake, not an episode."""
    row, predicted = _row(last_seen=T0 + timedelta(hours=4))
    events = [_event(T0, "appeared", new=120.0),
              _event(T0 + timedelta(hours=1), "gone", old=120.0),
              _event(T0 + timedelta(hours=2), "reappeared")]
    obs = observations_for(row, events, predicted, now=T0 + timedelta(hours=4))
    assert len(obs) == 1
    assert obs[0].sold is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_censoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking.censoring'`

- [ ] **Step 3: Write the implementation**

```python
"""One listing's history, turned into observations that admit what they don't know.

Two things make this more than bookkeeping.

**Every price is its own episode.** A map listed at 180c for three hours and then
marked down to 120c and sold in ten minutes is not one data point. It is two: a
non-sale at 180c that lasted three hours, and a sale at 120c that took ten
minutes. Throwing the first away discards the clearest evidence there is that
180c was too high -- and it is evidence we paid for by waiting three hours.

**A sale is an interval.** Polling stops when the app closes, so a map last seen
at 12:20 and missing at 21:00 sold somewhere in between, and nothing here
narrows that further. The observation carries `lo_hours` and `hi_hours` and lets
the estimator decide what a nine-hour bracket is worth -- which is something,
just not much. Recording the midpoint would turn "we don't know" into a
confident wrong number, and the fit would believe it.
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
    ordered = sorted(events, key=lambda e: e["at"])

    # A reappearance means the sale before it never happened. Drop both, so the
    # episode continues across the blip instead of being cut into two.
    cleaned: list[dict] = []
    for event in ordered:
        if event["event"] == REAPPEARED:
            for i in range(len(cleaned) - 1, -1, -1):
                if cleaned[i]["event"] == GONE:
                    cleaned.pop(i)
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
    """Every episode across every tracked listing, excluding what cannot be trusted."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_censoring.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tracking/censoring.py tests/test_tracking_censoring.py
git commit -m "Turn listing history into interval- and right-censored observations"
```

---

### Task 2: The survival fit

**Files:**
- Create: `src/tracking/survival.py`
- Test: `tests/test_tracking_survival.py`

**Interfaces:**
- Consumes: `Observation` from Task 1
- Produces:
  - `TIME_EDGES_HOURS`, `DEFAULT_RATIO_EDGES`
  - `Fit(ratio_edges, time_edges, hazard, at_risk, sales, ok, reason)`
  - `Fit.probability_within(ratio, hours) -> float | None`
  - `fit_hazard(observations, ratio_edges=..., time_edges=..., min_sales=8, max_iter=100, tol=1e-7) -> Fit`

- [ ] **Step 1: Write the failing test**

```python
"""The censored fit: uses coarse data for what it is worth, and refuses to invent."""
from __future__ import annotations

import pytest

from src.tracking.censoring import Observation
from src.tracking.survival import fit_hazard

RATIO_EDGES = (0.0, 1.0, 1.2, 99.0)


def _sold(ratio, lo, hi):
    return Observation(price_chaos=100.0, predicted_chaos=100.0 / ratio, ratio=ratio,
                       lo_hours=lo, hi_hours=hi, sold=True)


def _live(ratio, hours):
    return Observation(price_chaos=100.0, predicted_chaos=100.0 / ratio, ratio=ratio,
                       lo_hours=hours, hi_hours=None, sold=False)


def test_no_data_refuses_rather_than_returning_a_flat_curve():
    fit = fit_hazard([], ratio_edges=RATIO_EDGES)
    assert fit.ok is False
    assert "not enough" in fit.reason.lower()
    assert fit.probability_within(1.0, 24) is None


def test_exact_brackets_reduce_to_the_empirical_proportion():
    """With no censoring ambiguity, the fit must not be cleverer than counting."""
    obs = [_sold(0.9, 0.5, 0.5) for _ in range(15)]
    obs += [_live(0.9, 100.0) for _ in range(5)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.ok
    assert fit.probability_within(0.9, 24) == pytest.approx(15 / 20, abs=0.02)


def test_a_wide_bracket_still_counts_as_a_sale_somewhere():
    obs = [_sold(0.9, 0.3, 9.0) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(0.9, 24) == pytest.approx(1.0, abs=0.05), (
        "every one of them sold; we only don't know when"
    )


def test_a_wide_bracket_is_agnostic_inside_itself():
    """A 20min-9h bracket must not claim the sale happened in the first hour."""
    obs = [_sold(0.9, 0.3, 9.0) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    within_one = fit.probability_within(0.9, 1.0)
    assert 0.0 < within_one < 0.9, f"claimed {within_one:.2f} sold within the hour"


def test_cheaper_maps_clear_faster_than_dearer_ones():
    obs = [_sold(0.9, 0.5, 0.6) for _ in range(20)]
    obs += [_live(1.5, 200.0) for _ in range(20)]
    obs += [_sold(1.5, 150.0, 160.0) for _ in range(2)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(0.9, 24) > fit.probability_within(1.5, 24)


def test_a_band_with_no_data_refuses_to_extrapolate_from_its_neighbours():
    obs = [_sold(0.9, 0.5, 0.6) for _ in range(20)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    assert fit.probability_within(1.5, 24) is None


def test_probability_is_monotone_in_time():
    obs = [_sold(0.9, 0.5, 2.0) for _ in range(10)]
    obs += [_sold(0.9, 20.0, 30.0) for _ in range(10)]
    obs += [_live(0.9, 200.0) for _ in range(10)]
    fit = fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)
    values = [fit.probability_within(0.9, h) for h in (1, 3, 6, 12, 24, 48, 96)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))


def test_observations_without_a_ratio_are_ignored():
    obs = [Observation(100.0, None, None, 1.0, 2.0, True) for _ in range(30)]
    assert fit_hazard(obs, ratio_edges=RATIO_EDGES).ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_survival.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking.survival'`

- [ ] **Step 3: Write the implementation**

```python
"""How fast your maps clear, fitted from data that mostly doesn't know when.

The question every consumer asks is the same: *if I list at this ratio to the
model's estimate, what are the odds it sells within W hours?* Answering it from
this data is awkward because most sales are only bracketed -- polling stops when
the app closes, so an overnight sale is known to have happened within some nine
hour window and no more precisely.

There are three ways to handle that and two of them are wrong. Discarding
bracketed sales throws away most of the data and biases what remains toward
whatever sold while the app happened to be open. Midpointing them invents a
precision nobody has, and the fit believes it. The third way is to let a
bracketed sale contribute to every time bucket it could have happened in, in
proportion to how likely each bucket is -- which is circular, so it is solved by
iteration: guess the hazards, use them to split the bracketed sales across
buckets, re-estimate, repeat. That is the Turnbull/EM idea, and on a coarse grid
it is about forty lines.

The grid is deliberately coarse. Eight time buckets and a handful of ratio
bands, because a personal history is a few dozen maps, not a few thousand, and a
fine grid would fit noise beautifully.

Refusals matter as much as estimates here. A band with no data returns None
rather than borrowing from its neighbours -- you cannot learn what a 1.5x
listing does from having only ever listed at 0.9x, and a plausible-looking
number would hide that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

#: Upper edges, in hours. The last bucket is everything beyond the last edge.
TIME_EDGES_HOURS: tuple[float, ...] = (1.0, 3.0, 6.0, 12.0, 24.0, 48.0, 96.0)

#: Ratio band edges (ask / model estimate). Lower-inclusive, upper-exclusive.
DEFAULT_RATIO_EDGES: tuple[float, ...] = (0.0, 0.85, 1.0, 1.15, 1.3, 99.0)


@dataclass(frozen=True)
class Fit:
    ratio_edges: tuple[float, ...]
    time_edges: tuple[float, ...]
    hazard: np.ndarray  # (bands, buckets)
    at_risk: np.ndarray
    sales: np.ndarray
    ok: bool = False
    reason: str = ""

    def band_for(self, ratio: float) -> int:
        return int(np.clip(np.searchsorted(self.ratio_edges, ratio, side="right") - 1,
                           0, len(self.ratio_edges) - 2))

    def probability_within(self, ratio: float, hours: float) -> float | None:
        """P(sells within `hours`) for a listing at this ratio, or None if unknown."""
        if not self.ok:
            return None
        band = self.band_for(ratio)
        if self.sales[band].sum() <= 0 and self.at_risk[band].sum() <= 0:
            return None
        if self.at_risk[band].sum() <= 0:
            return None
        survival = 1.0
        for bucket, upper in enumerate(self._bucket_uppers()):
            if hours <= self._bucket_lower(bucket):
                break
            covered = min(hours, upper) - self._bucket_lower(bucket)
            width = upper - self._bucket_lower(bucket)
            if width <= 0 or covered <= 0:
                continue
            # Partial credit for a bucket the window only reaches into, so the
            # curve is monotone in time rather than a staircase with plateaus.
            survival *= (1.0 - self.hazard[band, bucket]) ** (covered / width)
        return float(1.0 - survival)

    def _bucket_uppers(self) -> list[float]:
        return list(self.time_edges) + [float(self.time_edges[-1] * 4)]

    def _bucket_lower(self, bucket: int) -> float:
        return 0.0 if bucket == 0 else self._bucket_uppers()[bucket - 1]


def _bucket_index(hours: float, uppers: Sequence[float]) -> int:
    for i, upper in enumerate(uppers):
        if hours < upper:
            return i
    return len(uppers) - 1


def fit_hazard(
    observations,
    ratio_edges: tuple[float, ...] = DEFAULT_RATIO_EDGES,
    time_edges: tuple[float, ...] = TIME_EDGES_HOURS,
    min_sales: int = 8,
    max_iter: int = 100,
    tol: float = 1e-7,
) -> Fit:
    """Discrete-time hazard per (ratio band, time bucket), by Turnbull-style EM."""
    usable = [o for o in observations if o.ratio is not None]
    n_bands = len(ratio_edges) - 1
    uppers = list(time_edges) + [float(time_edges[-1] * 4)]
    n_buckets = len(uppers)
    empty = np.zeros((n_bands, n_buckets))

    sold = [o for o in usable if o.sold]
    if len(usable) == 0 or len(sold) < min_sales:
        return Fit(
            ratio_edges=ratio_edges, time_edges=time_edges, hazard=empty,
            at_risk=empty.copy(), sales=empty.copy(), ok=False,
            reason=(
                f"not enough data: {len(sold)} sold listings with a model estimate, "
                f"{min_sales} needed"
            ),
        )

    def band_of(ratio: float) -> int:
        return int(np.clip(np.searchsorted(ratio_edges, ratio, side="right") - 1,
                           0, n_bands - 1))

    # Right-censored: at risk through every bucket it fully survived.
    base_at_risk = np.zeros((n_bands, n_buckets))
    for obs in usable:
        if obs.sold:
            continue
        band = band_of(obs.ratio)
        last = _bucket_index(obs.lo_hours, uppers)
        base_at_risk[band, :last] += 1.0

    # Sales: the buckets each one could have happened in.
    candidates: list[tuple[int, int, int]] = []  # (band, first bucket, last bucket)
    for obs in sold:
        band = band_of(obs.ratio)
        lo = _bucket_index(obs.lo_hours, uppers)
        hi = _bucket_index(obs.hi_hours if obs.hi_hours is not None else obs.lo_hours,
                           uppers)
        candidates.append((band, lo, max(lo, hi)))

    hazard = np.full((n_bands, n_buckets), 0.1)
    sales = np.zeros((n_bands, n_buckets))
    at_risk = base_at_risk.copy()

    for _ in range(max_iter):
        sales = np.zeros((n_bands, n_buckets))
        at_risk = base_at_risk.copy()

        for band, lo, hi in candidates:
            span = list(range(lo, hi + 1))
            # E-step: how likely is each candidate bucket, given current hazards?
            weights = []
            survival = 1.0
            for bucket in range(0, hi + 1):
                if bucket in span:
                    weights.append(survival * hazard[band, bucket])
                survival *= 1.0 - hazard[band, bucket]
            total = sum(weights)
            if total <= 0:
                weights = [1.0 / len(span)] * len(span)
                total = 1.0
            for bucket, weight in zip(span, weights):
                share = weight / total
                sales[band, bucket] += share
            # At risk in every bucket up to and including where it may have sold.
            remaining = 1.0
            for bucket in range(0, hi + 1):
                at_risk[band, bucket] += remaining
                if bucket in span:
                    remaining -= sales[band, bucket] * 0.0  # placeholder, see below
            for bucket in range(0, lo):
                pass

        with np.errstate(divide="ignore", invalid="ignore"):
            updated = np.where(at_risk > 0, sales / np.maximum(at_risk, 1e-12), 0.0)
        updated = np.clip(updated, 0.0, 1.0)
        if np.max(np.abs(updated - hazard)) < tol:
            hazard = updated
            break
        hazard = updated

    return Fit(
        ratio_edges=ratio_edges, time_edges=time_edges, hazard=hazard,
        at_risk=at_risk, sales=sales, ok=True,
        reason=f"fitted on {len(sold)} sold and {len(usable) - len(sold)} unsold episodes",
    )
```

**Note for the implementer:** the at-risk accumulation inside the EM loop above
is written out longhand and contains a placeholder line that does nothing
(`* 0.0`) plus an empty loop. That is deliberate — it marks the one part of this
function that the tests must drive rather than the plan dictate. The correct
accounting is: a bracketed sale is fully at risk in every bucket before its
bracket starts, and partially at risk within the bracket in proportion to the
probability it has not yet sold. Write the failing test first
(`test_a_wide_bracket_is_agnostic_inside_itself` and
`test_exact_brackets_reduce_to_the_empirical_proportion` are the two that pin
it down), then replace those lines with the accumulation that satisfies both.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_survival.py -v`
Expected: 8 passed

- [ ] **Step 5: Add a property test for monotonicity across the grid**

`tests/test_monotonicity.py` already exists for the model; follow its style and add
a check that `probability_within` never decreases as `hours` increases, for every
band with data, over a randomised set of fitted inputs.

- [ ] **Step 6: Commit**

```bash
git add src/tracking/survival.py tests/test_tracking_survival.py tests/test_monotonicity.py
git commit -m "Fit clearing speed from censored listing history"
```

---

### Task 3: Observed thresholds and alerts

**Files:**
- Create: `src/tracking/alerts.py`
- Test: `tests/test_tracking_alerts.py`

**Interfaces:**
- Produces:
  - `Thresholds(fast_hours, slow_hours, source)`
  - `observed_thresholds(observations, fallback_fast, fallback_slow, min_sold=20) -> Thresholds`
  - `Alert(listing_id, kind, headline, evidence)`
  - `alerts_for(rows, observations, thresholds, fit, now) -> list[Alert]`

- [ ] **Step 1: Write the failing test**

```python
"""Alerts: only when the evidence is decisive, and thresholds you actually set."""
from __future__ import annotations

import pytest

from src.tracking.alerts import Thresholds, alerts_for, observed_thresholds
from src.tracking.censoring import Observation


def _sold(lo, hi, ratio=1.0):
    return Observation(100.0, 100.0 / ratio, ratio, lo, hi, True)


def test_thresholds_fall_back_to_config_when_there_is_little_history():
    t = observed_thresholds([_sold(1, 2)], fallback_fast=1.0, fallback_slow=24.0)
    assert (t.fast_hours, t.slow_hours) == (1.0, 24.0)
    assert t.source == "config"


def test_thresholds_come_from_your_own_distribution_once_there_is_enough():
    obs = [_sold(h, h) for h in range(1, 41)]
    t = observed_thresholds(obs, fallback_fast=1.0, fallback_slow=24.0, min_sold=20)
    assert t.source == "observed"
    assert t.fast_hours == pytest.approx(10.75, abs=1.0), "your own lower quartile"
    assert t.slow_hours == pytest.approx(30.25, abs=1.0), "your own upper quartile"


def test_a_decisive_fast_sale_raises_underpriced():
    row = {"id": 1, "name": "Rune Path", "price_chaos": 90.0,
           "predicted_chaos": 120.0, "status": "gone",
           "sold_lo_hours": 0.2, "sold_hi_hours": 0.6, "hours_listed": 0.6}
    alerts = alerts_for([row], Thresholds(1.0, 24.0, "config"))
    assert [a.kind for a in alerts] == ["underpriced"]
    assert "0.6" in alerts[0].evidence or "36 min" in alerts[0].evidence


def test_a_straddling_bracket_makes_no_claim():
    """Sold somewhere between 20 minutes and 9 hours is not 'sold fast'."""
    row = {"id": 1, "name": "Rune Path", "price_chaos": 90.0,
           "predicted_chaos": 120.0, "status": "gone",
           "sold_lo_hours": 0.33, "sold_hi_hours": 9.0, "hours_listed": 9.0}
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_a_fast_sale_above_the_model_is_not_underpriced():
    row = {"id": 1, "name": "Rune Path", "price_chaos": 150.0,
           "predicted_chaos": 120.0, "status": "gone",
           "sold_lo_hours": 0.2, "sold_hi_hours": 0.5, "hours_listed": 0.5}
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_a_long_stale_listing_above_the_model_raises_overpriced():
    row = {"id": 2, "name": "Bleak Lands", "price_chaos": 200.0,
           "predicted_chaos": 140.0, "status": "live",
           "sold_lo_hours": None, "sold_hi_hours": None, "hours_listed": 60.0}
    alerts = alerts_for([row], Thresholds(1.0, 24.0, "config"))
    assert [a.kind for a in alerts] == ["overpriced"]
    assert "60" in alerts[0].evidence


def test_a_stale_listing_at_or_below_the_model_is_not_flagged():
    row = {"id": 2, "name": "Bleak Lands", "price_chaos": 100.0,
           "predicted_chaos": 140.0, "status": "live",
           "sold_lo_hours": None, "sold_hi_hours": None, "hours_listed": 60.0}
    assert alerts_for([row], Thresholds(1.0, 24.0, "config")) == []


def test_excluded_and_ambiguous_rows_never_alert():
    base = {"name": "X", "price_chaos": 90.0, "predicted_chaos": 120.0,
            "status": "gone", "sold_lo_hours": 0.2, "sold_hi_hours": 0.5,
            "hours_listed": 0.5}
    rows = [dict(base, id=1, exclude_from_learning=1),
            dict(base, id=2, ambiguous=1)]
    assert alerts_for(rows, Thresholds(1.0, 24.0, "config")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_alerts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking.alerts'`

- [ ] **Step 3: Write the implementation**

```python
"""Telling you a map was mispriced, but only when the data actually says so.

Two rules keep this from crying wolf.

**The bracket has to be decisive.** A map that sold somewhere between twenty
minutes and nine hours might have sold fast or might have sold slowly, and the
overnight gap that produced that bracket is not a reason to guess. The alert
fires only when the whole bracket sits on one side of the threshold. The
observation still feeds the fit -- it is not wasted, it just doesn't get to make
a claim on screen.

**The threshold is yours, not mine.** "Fast" is meaningless in the abstract; it
depends on your maps, your league and your prices. So the thresholds start from
config and are replaced by your own quartiles once there are enough sales to
have quartiles -- the same idiom `sell_strategy.estimate_multipliers` already
uses for the strategy multipliers, and for the same reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

MIN_SOLD_FOR_OBSERVED = 20


@dataclass(frozen=True)
class Thresholds:
    fast_hours: float
    slow_hours: float
    source: str  # "config" | "observed"


@dataclass(frozen=True)
class Alert:
    listing_id: int
    kind: str  # "underpriced" | "overpriced"
    headline: str
    evidence: str


def observed_thresholds(
    observations,
    fallback_fast: float,
    fallback_slow: float,
    min_sold: int = MIN_SOLD_FOR_OBSERVED,
) -> Thresholds:
    """Your own lower and upper quartile time-to-sell, once there are enough sales."""
    sold = [o for o in observations if o.sold and o.hi_hours is not None]
    if len(sold) < min_sold:
        return Thresholds(fallback_fast, fallback_slow, "config")
    # The upper bound is the conservative end for calling something fast.
    times = np.array([o.hi_hours for o in sold], dtype=float)
    fast, slow = (float(v) for v in np.quantile(times, [0.25, 0.75]))
    return Thresholds(fast_hours=fast, slow_hours=slow, source="observed")


def _hours(value: float) -> str:
    return f"{value * 60:.0f} min" if value < 1 else f"{value:.0f} h"


def alerts_for(rows: Sequence[dict], thresholds: Thresholds) -> list[Alert]:
    out: list[Alert] = []
    for row in rows:
        if row.get("exclude_from_learning") or row.get("ambiguous"):
            continue
        ask = row.get("price_chaos")
        model = row.get("predicted_chaos")
        if not ask or not model:
            continue

        if row.get("status") == "gone":
            hi = row.get("sold_hi_hours")
            if hi is None or hi > thresholds.fast_hours:
                continue  # not decisively fast
            if ask > model:
                continue  # sold fast, but you were already asking above the model
            out.append(
                Alert(
                    listing_id=row["id"],
                    kind="underpriced",
                    headline=f"{row.get('name') or 'map'} sold within {_hours(hi)}",
                    evidence=(
                        f"asked {ask:.0f}c against a {model:.0f}c estimate and it went "
                        f"within {_hours(hi)}, under your {_hours(thresholds.fast_hours)} "
                        f"fast mark ({thresholds.source}). The market would likely have "
                        f"paid more."
                    ),
                )
            )
            continue

        hours = row.get("hours_listed") or 0.0
        if hours <= thresholds.slow_hours or ask <= model:
            continue
        out.append(
            Alert(
                listing_id=row["id"],
                kind="overpriced",
                headline=f"{row.get('name') or 'map'} has sat {_hours(hours)}",
                evidence=(
                    f"asking {ask:.0f}c against a {model:.0f}c estimate, listed "
                    f"{_hours(hours)} — past your {_hours(thresholds.slow_hours)} mark "
                    f"({thresholds.source})."
                ),
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_alerts.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/tracking/alerts.py tests/test_tracking_alerts.py
git commit -m "Add mispricing alerts with decisive-bracket and observed thresholds"
```

---

### Task 4: The fit-derived reprice recommendation

**Files:**
- Modify: `src/tracking/reprice.py`
- Test: `tests/test_tracking_reprice.py` (extend)

**Interfaces:**
- Consumes: `Fit` from Task 2, `LadderConfig`/`Recommendation` from the core plan
- Produces: `recommend(price_chaos, hours_listed, predicted_chaos, interval_low_chaos, fit, target_window_hours, target_probability, cfg) -> Recommendation | None`

- [ ] **Step 1: Write the failing test (append to the existing file)**

```python
from src.tracking.reprice import recommend
from src.tracking.survival import fit_hazard
from src.tracking.censoring import Observation

RATIO_EDGES = (0.0, 0.9, 1.05, 1.2, 99.0)


def _fit_where_cheap_clears():
    obs = [Observation(90.0, 100.0, 0.85, 0.5, 0.6, True) for _ in range(20)]
    obs += [Observation(130.0, 100.0, 1.3, 200.0, None, False) for _ in range(20)]
    return fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)


def test_the_recommendation_is_read_off_the_fit_when_there_is_one():
    rec = recommend(price_chaos=130.0, hours_listed=60, predicted_chaos=100.0,
                    interval_low_chaos=70.0, fit=_fit_where_cheap_clears(),
                    target_window_hours=24, target_probability=0.6, cfg=CFG)
    assert rec.source == "fit"
    assert rec.price_chaos < 130.0
    assert "clear" in rec.reason


def test_the_fit_recommendation_still_respects_the_floor():
    rec = recommend(price_chaos=130.0, hours_listed=60, predicted_chaos=100.0,
                    interval_low_chaos=125.0, fit=_fit_where_cheap_clears(),
                    target_window_hours=24, target_probability=0.6, cfg=CFG)
    assert rec.floored is True
    assert rec.price_chaos == pytest.approx(125.0)


def test_an_unfitted_model_falls_back_to_the_ladder_and_says_so():
    empty = fit_hazard([], ratio_edges=RATIO_EDGES)
    rec = recommend(price_chaos=150.0, hours_listed=30, predicted_chaos=140.0,
                    interval_low_chaos=100.0, fit=empty,
                    target_window_hours=24, target_probability=0.6, cfg=CFG)
    assert rec.source == "ladder"
    assert "Fallback ladder" in rec.reason


def test_a_ratio_band_the_fit_knows_nothing_about_falls_back():
    fit = _fit_where_cheap_clears()
    rec = recommend(price_chaos=100.0, hours_listed=30, predicted_chaos=100.0,
                    interval_low_chaos=60.0, fit=fit,
                    target_window_hours=24, target_probability=0.6, cfg=CFG)
    assert rec.source == "ladder", "band 1.0 has no data; do not invent a number"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_reprice.py -v`
Expected: FAIL — `ImportError: cannot import name 'recommend'`

- [ ] **Step 3: Add `recommend` to `src/tracking/reprice.py`**

```python
def recommend(
    price_chaos: float,
    hours_listed: float,
    predicted_chaos: float | None,
    interval_low_chaos: float | None,
    fit,
    target_window_hours: float,
    target_probability: float,
    cfg: LadderConfig,
) -> Recommendation | None:
    """The highest price that still clears inside your target window.

    A map that has not sold is evidence its clearing price is lower, but "lower"
    is not a number. The fit turns it into one: for each price band it knows the
    odds of selling within the window, so the recommendation is the dearest band
    whose odds are still acceptable -- a derived price, not a haircut.

    Where the fit cannot answer -- too little history, or a band you have never
    listed in -- this falls back to the ladder rather than extrapolating, and the
    returned `source` says which happened. A guess labelled as a measurement is
    worse than a guess.
    """
    if not getattr(fit, "ok", False) or predicted_chaos in (None, 0):
        return recommend_ladder(
            price_chaos, hours_listed, predicted_chaos, interval_low_chaos, cfg
        )
    if hours_listed < cfg.stale_after_hours:
        return None

    best: float | None = None
    for edge in reversed(fit.ratio_edges[:-1]):
        candidate = predicted_chaos * edge
        if candidate <= 0 or candidate > price_chaos:
            continue
        probability = fit.probability_within(edge, target_window_hours)
        if probability is None:
            continue
        if probability >= target_probability:
            best = candidate
            break

    if best is None:
        return recommend_ladder(
            price_chaos, hours_listed, predicted_chaos, interval_low_chaos, cfg
        )

    floor = interval_low_chaos
    if floor is None:
        floor = predicted_chaos * cfg.floor_ratio
    if best <= floor:
        return Recommendation(
            price_chaos=round(floor, 1),
            reason=(
                f"listed {hours_listed:.0f} h without selling; the price that clears "
                f"within {target_window_hours:.0f} h is under the {floor:.0f}c floor, "
                f"so {floor:.0f}c is as far as this should go."
            ),
            source="fit",
            floored=True,
        )

    probability = fit.probability_within(best / predicted_chaos, target_window_hours)
    return Recommendation(
        price_chaos=round(best, 1),
        reason=(
            f"listed {hours_listed:.0f} h at {price_chaos:.0f}c without selling. Your "
            f"maps at {best / predicted_chaos:.2f}x the estimate clear within "
            f"{target_window_hours:.0f} h {probability:.0%} of the time, so "
            f"{best:.0f}c is the dearest price that still moves."
        ),
        source="fit",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_reprice.py -v`
Expected: 10 passed (6 from the core plan, 4 new)

- [ ] **Step 5: Commit**

```bash
git add src/tracking/reprice.py tests/test_tracking_reprice.py
git commit -m "Derive the reprice recommendation from the fit, falling back honestly"
```

---

### Task 5: The personal calibration factor

**Files:**
- Create: `src/tracking/calibration.py`
- Test: `tests/test_tracking_calibration.py`

**Interfaces:**
- Produces:
  - `CalibrationConfig(min_sold, clamp, target_window_hours, target_probability)`
  - `PersonalCalibration(factor, source, model_version, sample, applied)`
  - `personal_factor(observations, fit, model_version, cfg) -> PersonalCalibration`

- [ ] **Step 1: Write the failing test**

```python
"""The personal factor: measured, clamped, bound to a model, and refusable."""
from __future__ import annotations

import pytest

from src.tracking.calibration import CalibrationConfig, personal_factor
from src.tracking.censoring import Observation
from src.tracking.survival import fit_hazard

RATIO_EDGES = (0.0, 0.9, 1.05, 1.2, 99.0)
CFG = CalibrationConfig(min_sold=20, clamp=(0.8, 1.3), target_window_hours=24,
                        target_probability=0.6)


def _sold(ratio, lo=0.5, hi=0.6):
    return Observation(100.0 * ratio, 100.0, ratio, lo, hi, True)


def _unsold(ratio, hours=200.0):
    return Observation(100.0 * ratio, 100.0, ratio, hours, None, False)


def _fit(obs):
    return fit_hazard(obs, ratio_edges=RATIO_EDGES, min_sales=5)


def test_too_little_history_refuses_and_says_so():
    obs = [_sold(1.0) for _ in range(5)]
    cal = personal_factor(obs, _fit(obs), "v1", CFG)
    assert cal.applied is False
    assert cal.factor == 1.0
    assert "20" in cal.source


def test_fast_sales_alone_are_not_enough_because_they_fix_no_upper_bound():
    obs = [_sold(0.85) for _ in range(30)]
    cal = personal_factor(obs, _fit(obs), "v1", CFG)
    assert cal.applied is False
    assert "both sides" in cal.source or "upper" in cal.source


def test_a_two_sided_history_yields_a_factor_between_the_brackets():
    obs = [_sold(0.85) for _ in range(15)]
    obs += [_unsold(1.25) for _ in range(15)]
    cal = personal_factor(obs, _fit(obs), "v1", CFG)
    assert cal.applied is True
    assert 0.85 < cal.factor < 1.25
    assert cal.model_version == "v1"
    assert cal.sample == 30


def test_the_factor_is_clamped_and_the_clamp_is_visible():
    obs = [_sold(1.9) for _ in range(20)]
    obs += [_unsold(2.5) for _ in range(20)]
    cal = personal_factor(obs, _fit(obs), "v1", CFG)
    assert cal.factor <= 1.3
    assert "clamp" in cal.source


def test_a_wildly_out_of_range_measurement_is_refused_not_clamped():
    """Mirrors SANE_MARKET_RATIO: a factor this far off means something is wrong."""
    obs = [_sold(0.2) for _ in range(20)]
    obs += [_unsold(0.3) for _ in range(20)]
    cal = personal_factor(obs, _fit(obs), "v1", CFG)
    assert cal.applied is False
    assert "miscalibrat" in cal.source or "refus" in cal.source


def test_the_calibration_records_which_model_it_was_measured_against():
    obs = [_sold(0.85) for _ in range(15)] + [_unsold(1.25) for _ in range(15)]
    cal = personal_factor(obs, _fit(obs), "catboost-2026-08-24", CFG)
    assert cal.model_version == "catboost-2026-08-24"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.tracking.calibration'`

- [ ] **Step 3: Write the implementation**

```python
"""How far your listings actually clear from what the model says.

The model learns from asks -- what sellers wanted. Your own listings are the one
place this tool sees what a buyer paid, and a factor measured from them is the
only correction here fitted on outcomes rather than intentions.

It is also the most dangerous number in the app, because it silently moves every
suggested price. So it is guarded the way `sell_strategy.estimate_multipliers`
guards the observed-spread multipliers, deliberately mirroring that code:

* **Evidence on both sides, or nothing.** Fast sales bracket the clearing ratio
  from below -- you would have got at least that. Stale listings bracket it from
  above. With only one side there is no bracket, just a direction, and a
  direction is not a factor.
* **Clamped, and it says when it clamped.**
* **A measurement far outside the sane range is refused, not clamped.** A factor
  of 0.25 does not mean sell at a quarter price; it means the model is
  miscalibrated, and quietly scaling by 0.25 would hide exactly the thing worth
  seeing.
* **Bound to a model version.** The model's own known underpricing is a hedging
  artefact with a measured fix, so this factor is measured *on top of* whatever
  the active model already does. Carry it across a model change and the two
  corrections stack, with the second fitted on prices the first already moved.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Outside this the measurement is not seller behaviour, it is a broken model.
SANE_FACTOR_RANGE = (0.5, 2.0)


@dataclass(frozen=True)
class CalibrationConfig:
    min_sold: int = 20
    clamp: tuple[float, float] = (0.8, 1.3)
    target_window_hours: float = 24.0
    target_probability: float = 0.6


@dataclass(frozen=True)
class PersonalCalibration:
    factor: float = 1.0
    source: str = ""
    model_version: str = ""
    sample: int = 0
    applied: bool = False


def personal_factor(observations, fit, model_version: str, cfg: CalibrationConfig):
    usable = [o for o in observations if o.ratio is not None]
    sold = [o for o in usable if o.sold]
    unsold = [o for o in usable if not o.sold]

    if len(usable) < cfg.min_sold:
        return PersonalCalibration(
            source=(
                f"not applied: {len(usable)} usable episodes, {cfg.min_sold} needed"
            ),
            model_version=model_version,
            sample=len(usable),
        )
    if not sold or not unsold:
        return PersonalCalibration(
            source=(
                "not applied: needs evidence on both sides — sales bracket the "
                "clearing ratio only from below and fix no upper bound"
            ),
            model_version=model_version,
            sample=len(usable),
        )

    lower = float(np.median([o.ratio for o in sold]))
    upper = float(np.median([o.ratio for o in unsold]))
    if upper <= lower:
        # Your stale maps are cheaper than your sold ones: the ratio is not what
        # is driving your sales, so there is nothing here to calibrate on.
        return PersonalCalibration(
            source=(
                "not applied: stale listings are priced below sold ones, so price "
                "ratio does not explain what clears"
            ),
            model_version=model_version,
            sample=len(usable),
        )

    measured = (lower + upper) / 2.0

    if not SANE_FACTOR_RANGE[0] <= measured <= SANE_FACTOR_RANGE[1]:
        return PersonalCalibration(
            source=(
                f"not applied: measured {measured:.2f}x, which is model "
                f"miscalibration rather than your selling — retrain rather than "
                f"scaling every price by this"
            ),
            model_version=model_version,
            sample=len(usable),
        )

    low, high = cfg.clamp
    factor = min(max(measured, low), high)
    clamped = abs(factor - measured) > 1e-9
    return PersonalCalibration(
        factor=round(factor, 4),
        source=(
            f"measured {measured:.2f}x from {len(sold)} sold and {len(unsold)} unsold "
            f"episodes against {model_version}"
            + (f", clamped to {factor:.2f}" if clamped else "")
        ),
        model_version=model_version,
        sample=len(usable),
        applied=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_calibration.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/tracking/calibration.py tests/test_tracking_calibration.py
git commit -m "Measure a personal price factor from what actually cleared"
```

---

### Task 6: The `sell_strategy` seam

**Files:**
- Modify: `src/pricing/sell_strategy.py`
- Test: `tests/test_sell_strategy_personal.py`

**Interfaces:**
- Produces: `StrategyMultipliers(..., personal=1.0, personal_source="")`, applied inside `suggested_price`

- [ ] **Step 1: Write the failing test**

```python
"""The personal factor composes with the strategy, and never hides itself."""
from __future__ import annotations

import pytest

from src.pricing.sell_strategy import StrategyMultipliers, suggested_price


def _multipliers(personal=1.0, source=""):
    return StrategyMultipliers(quick_sell=0.94, market=0.99, patient=1.06,
                               source="config", personal=personal,
                               personal_source=source)


def test_without_a_personal_factor_nothing_changes():
    assert suggested_price(100.0, "market", _multipliers()) == pytest.approx(99.0)


def test_the_personal_factor_scales_the_suggested_price():
    m = _multipliers(personal=1.1, source="measured 1.10x")
    assert suggested_price(100.0, "market", m) == pytest.approx(108.9)


def test_it_composes_with_every_strategy():
    m = _multipliers(personal=0.9)
    assert suggested_price(100.0, "quick_sell", m) == pytest.approx(84.6)
    assert suggested_price(100.0, "patient", m) == pytest.approx(95.4)


def test_the_source_survives_into_as_dict_so_the_ui_can_show_it():
    m = _multipliers(personal=1.1, source="measured 1.10x against catboost-2026-08-24")
    payload = m.as_dict()
    assert payload["personal"] == 1.1
    assert "catboost" in payload["personal_source"]


def test_an_unknown_strategy_still_raises():
    with pytest.raises(ValueError):
        suggested_price(100.0, "nonsense", _multipliers())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sell_strategy_personal.py -v`
Expected: FAIL — `TypeError: StrategyMultipliers.__init__() got an unexpected keyword argument 'personal'`

- [ ] **Step 3: Modify `src/pricing/sell_strategy.py`**

Add the two fields to the dataclass, defaulted so every existing construction
site keeps working unchanged:

```python
@dataclass
class StrategyMultipliers:
    quick_sell: float
    market: float
    patient: float
    source: str  # "config" or "observed-spread"
    #: Measured from your own listings: what your maps actually clear at, against
    #: what the model says they are worth. 1.0 means not applied. It scales the
    #: *listing price* and never the fair-value estimate -- fair value is a claim
    #: about the market, this is a claim about your selling, and blurring the two
    #: would put a personal number where a market number is expected.
    personal: float = 1.0
    personal_source: str = ""
```

Extend `as_dict` with both keys, and apply the factor in `suggested_price`:

```python
def suggested_price(fair_value: float, strategy: str, multipliers: StrategyMultipliers) -> float:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    return fair_value * multipliers.get(strategy) * multipliers.personal
```

`get` uses `getattr(self, strategy)` and `STRATEGIES` does not contain
`"personal"`, so the new field cannot be selected as a strategy by accident.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sell_strategy_personal.py tests/ -q -k "strategy or pricing or stash"`
Expected: all pass — the defaults mean no existing caller changes behaviour

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/pricing/sell_strategy.py tests/test_sell_strategy_personal.py
git commit -m "Compose a personal calibration factor into suggested listing price"
```

---

### Task 7: Wiring it into the UI

**Files:**
- Modify: `src/ui/my_listings.py`, `src/ui/settings.py`, `src/ui/common.py`
- Test: `tests/test_my_listings_page.py` (extend), `tests/test_ui_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_page_reports_a_refused_calibration_rather_than_hiding_it():
    from src.ui.my_listings import calibration_note
    from src.tracking.calibration import PersonalCalibration

    refused = PersonalCalibration(
        factor=1.0, source="not applied: 8 usable episodes, 20 needed",
        model_version="v1", sample=8, applied=False,
    )
    note = calibration_note(refused)
    assert "not applied" in note
    assert "20 needed" in note


def test_an_applied_calibration_names_its_model():
    from src.ui.my_listings import calibration_note
    from src.tracking.calibration import PersonalCalibration

    applied = PersonalCalibration(
        factor=1.08, source="measured 1.08x from 22 sold and 14 unsold episodes "
        "against catboost-2026-08-24",
        model_version="catboost-2026-08-24", sample=36, applied=True,
    )
    note = calibration_note(applied)
    assert "1.08" in note and "catboost-2026-08-24" in note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_my_listings_page.py -v`
Expected: FAIL — `ImportError: cannot import name 'calibration_note'`

- [ ] **Step 3: Implement**

Add `calibration_note(calibration) -> str` to `src/ui/my_listings.py` — a single
string that always states whether the factor is applied, its value, its sample
and the model it was measured against.

Then wire the rest:

- **My Listings page:** an Alerts section above the table rendering
  `alerts_for(...)`; a "What your maps actually clear at" expander showing the
  fit's `reason`, the per-band `P(sells within W)` table, and
  `calibration_note(...)`; the reprice column switching from `recommend_ladder`
  to `recommend` now that a fit exists.
- **Settings:** a checkbox for `listing_tracker.calibration.enabled`, defaulting
  on, with the current `calibration_note` printed beneath it so turning it off is
  an informed choice.
- **Sidebar badge:** in `src/ui/common.py:sidebar_context`, a count of open
  alerts next to the My Listings entry.
- **Suggested price surfaces:** wherever `StrategyMultipliers` is built for the
  Stash Pricer and Single Map pages, pass `personal` and `personal_source` from
  the stored calibration when it is applied and enabled, so the number and its
  provenance travel together.

Add the config block to `config.example.yaml` and `config.yaml`:

```yaml
listing_tracker:
  target_window_hours: 24        # W: how fast you want a map to clear. Same
                                 # number as stale_after_hours by default, but a
                                 # different question -- one filters the page,
                                 # the other defines the fit's target.
  target_sell_probability: 0.6
  calibration:
    enabled: true
    min_sold: 20
    clamp: [0.8, 1.3]
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/ui/my_listings.py src/ui/settings.py src/ui/common.py config.yaml config.example.yaml tests/
git commit -m "Surface alerts, the clearing-speed report and the calibration note"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §4 censoring, per-price episodes, reappearance cancels a sale | 1 |
| §7 the estimator, grid, EM, all three degenerate cases | 2 |
| §8 alerts, decisive bracket, observed thresholds | 3 |
| §8 recommended adjusted price derived from the fit, floor, labelled fallback | 4 |
| §9 the learning, both-sided evidence, clamp, refusal, model binding | 5, 6 |
| §9 one seam in `suggested_price` | 6 |
| §10 configuration | 7 |
| §11 testing | every task |

**Placeholder scan:** one intentional gap, flagged in place — the at-risk
accumulation inside the EM loop in Task 2 is left for the tests to drive, with
the two tests that pin it named explicitly and the correct accounting described
in prose. Everything else carries its code.

**Type consistency:** `Observation(price_chaos, predicted_chaos, ratio, lo_hours,
hi_hours, sold)` is defined in Task 1 and constructed positionally in Tasks 2, 4
and 5 in that order. `Fit.ok`, `.reason`, `.ratio_edges` and
`.probability_within` are defined in Task 2 and read in Tasks 4 and 5.
`PersonalCalibration(factor, source, model_version, sample, applied)` is defined
in Task 5 and read in Task 7. `Recommendation` and `LadderConfig` come from the
core plan unchanged; `recommend` is added beside `recommend_ladder` rather than
replacing it, so the fallback path stays reachable and tested.

**Ordering note:** Task 6 changes a function every pricing page already calls.
It is late and its defaults are no-ops, so the full suite passing after Task 6 is
the real check that the seam is inert until a calibration is actually applied.
