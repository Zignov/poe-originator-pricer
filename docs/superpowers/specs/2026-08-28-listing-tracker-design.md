# Listing tracker: learning your price from what actually sells

Design, 2026-08-28.

Watch the maps you have listed for instant buyout, record what you asked and how
long it took to go, and turn that into two things the tool does not have today:
a page that shows every map still sitting in your store with a recommended lower
price, and a personal calibration that shifts suggested prices toward what your
listings actually clear at.

The premise is that on the maps you list, you know something the model does not.
The model learns from asks — what sellers *wanted*. A map of yours that vanished
in eleven minutes is evidence about what a buyer *paid*, and a map that has sat
for three days is evidence in the other direction. Neither is in the training
data.

---

## 1. Scope

**In:**

- Poll the user's own live instant-buyout listings from the official trade API.
- Record appearances, re-prices and disappearances as an append-only event log.
- A **My Listings** page: everything live, sorted by how long it has sat, with a
  staleness filter, a recommended adjusted price per map, a paste-able stash
  search regex for the stale set, and overlay export.
- A measured report of the gap between the model's number and what your maps
  clear at.
- A personal calibration multiplier applied to *suggested listing price*, with
  guards and an off switch.

**Out, deliberately:**

- Writing prices back into the game. No supported API exists; see
  `docs/data-access.md` §6. This produces numbers you type in yourself.
- A background daemon that polls with the app closed. The poller is written
  UI-independent so this can be added later without redesign (§9).
- Feeding your sold listings into model training data. A small self-selected
  sample would pull the model toward your own habits. The calibration
  multiplier keeps that signal in a separate, inspectable, switch-offable place.
- Non-Originator items, and anything outside the configured scope (T16, rare,
  uncorrupted). The model only prices those, so only those get a prediction.

---

## 2. Reading your listings

One pass, every poll:

```
POST /api/trade/search/{league}
     filters.trade_filters.filters.account.input = <POE_ACCOUNT_NAME>
     collapse OFF, status "any", sale_type omitted
  -> up to 100 listing ids, and a `total`
GET  /api/trade/fetch/{up to 10 ids}?query={id}
  -> price, listing.indexed, full item text
```

`build_query` in `src/market/collectors/official_trade_api.py` already takes an
`account` argument, verified against `/api/trade/data/filters`. Three parameters
matter more than the rest:

**`collapse` must be off.** It defaults to `True` in `build_query` and it means
"collapse listings by account". An account-filtered search with collapse on
would plausibly return a single row, and every other map would look sold.

In this codebase, off means **the key is omitted**, not sent as false:
`build_query` emits `collapse` only when it is on, `src/market/bulk.py` relies
on that for its own account-scoped scan, and
`tests/test_live_search_link.py::test_the_link_is_uncollapsed_so_a_seller_shows_every_map`
pins the convention by asserting `"collapse" not in trade`. The tracker follows
it rather than changing the shared query builder. Its own test asserts the same
absence. If a poll ever returns exactly one listing when the previous poll saw
many, the run is marked partial rather than trusted (§4).

**`status` is `"any"`, not `"online"`.** Otherwise logging out is
indistinguishable from selling the whole tab.

**`sale_type` is omitted**, which is exactly the buyout-or-fixed-price
population — the instant-buyout set. Confirmed in `docs/data-access.md` §4.

### More than 100 listings

A search returns at most 100 ids. Past that, the run issues additional searches
sliced by price band (`price_min`/`price_max`, already supported) to enumerate
the rest. If the set still cannot be completed, the poll is recorded with
`complete = 0` and **disappearance handling is skipped for that pass entirely**.

This is not a nicety. A listing missing because it fell outside the result
window is indistinguishable from one that sold, and counting it as a sale would
inject a fake fast sale into the very data the calibration is fitted on.

### Cost

Roughly one search plus one fetch per ten listings. Forty listings on a
fifteen-minute cadence is about twenty requests an hour, against a sustained
search policy of thirty per five minutes. Both limiters are the existing
`RateLimiter`, driven by GGG's own response headers, backing off rather than
retrying on 429.

`docs/data-access.md` §3 currently records "no continuous background polling" as
a deliberate choice. This feature reverses that for a single account-scoped
query, and **that document is updated in the same change** so it stays true.

---

## 3. Identity

A tracked listing is keyed on the trade listing id, with the reward-feature
fingerprint as a fallback, because id stability across a re-price has not been
verified. If the id changes but the fingerprint matches a listing that vanished
in the same poll, it is treated as the same map re-listed, not as a sale
followed by a new listing.

Fingerprint collisions — two of your maps with identical rewards and the same
name, listed at once — are marked `ambiguous` and excluded from learning and
from overlay placement rather than guessed at. They still appear on the page,
because a map you cannot tell apart from its twin is still a map you may want
to re-price.

**One row per map, not per price.** A re-price mutates `priced_at` on the
existing row and appends a `repriced` event; it does not open a second row. The
price history lives in the event log, which is where `censoring.py` reads it
from, so a map that wore three prices produces three observations without
producing three rows.

There is deliberately **no unique index** enforcing this. Identity is a
judgement — same id, or same fingerprint, or a twin that cannot be told apart —
and §6's whole premise is that judgements live in tested pure functions rather
than in schema constraints. A unique constraint would also simply fail to insert
the second of two genuinely identical live maps, which is a case that must be
recorded and flagged, not rejected. The index above exists for lookup speed.

---

## 4. The event log, and being honest about time

`gone` means **sold**. The API cannot distinguish a sale from a delist, and
pretending otherwise bought a workflow nobody wants. A single
`exclude_from_learning` checkbox on the row is the escape hatch for a map you
know you pulled; it keeps one bad row out of the fit without a resolution
workflow.

Events are append-only: `appeared`, `repriced`, `gone`, `reappeared`.

**A re-price restarts the clock.** Dropping a map from 180c to 120c and selling
it ten minutes later is a ten-minute sale at 120c, not a three-day sale. Elapsed
time is measured from the most recent `repriced` event. Each price the map wore
is its own observation: the interval it spent at 180c without selling is a
right-censored data point in its own right, so a map you have already marked
down twice carries more evidence than one you have not, automatically.

### Censoring

Polling stops when the app closes, so a disappearance is known only to have
happened between the last poll that saw it and the first that did not. Every
observation is therefore one of:

- **interval-censored** — sold somewhere in `[lo, hi]`;
- **right-censored** — still live, has survived at least `T`.

Nothing is stored as a point estimate of time-to-sell, because we do not have
one. Three consequences:

1. **The stale side is unaffected.** A map still listed after 62 hours is
   measured exactly, gaps or no gaps. The staleness page, its recommendations
   and the overpriced alerts all sit on this well-measured side.
2. **Alerts require a decisive bracket.** "Sold fast" fires only when the whole
   bracket lies below the fast threshold. A bracket that straddles it makes no
   claim on screen, but still constrains the fit.
3. **The fit uses censored data properly** rather than discarding it or
   midpointing it (§7).

Two cheap bracket-shrinkers, both in the poller: poll once immediately on app
open, which tightens the upper bound from "sometime overnight" to "before you
opened the app"; and poll young listings more often than old ones, since the
first hour holds the fast-sale signal and a three-day-old map does not need a
five-minute check.

`listing_poll_runs` records every pass — when, how many seen, `total` reported,
complete or partial, and any error. Without it, an app that was closed for eight
hours and a poll that silently failed look the same, and both look like a
suspiciously quiet market.

---

## 5. Schema

Migration `3`, appended to `MIGRATIONS` in `src/database/models.py` (the file's
own rule: never edit a released entry).

```sql
CREATE TABLE tracked_listings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    account               TEXT NOT NULL,
    league                TEXT NOT NULL,
    trade_listing_id      TEXT,
    fingerprint           TEXT NOT NULL,

    name                  TEXT,
    base_type             TEXT,
    map_tier              INTEGER,
    item_level            INTEGER,
    corrupted             INTEGER DEFAULT 0,
    quantity              INTEGER DEFAULT 0,
    rarity                INTEGER DEFAULT 0,
    pack_size             INTEGER DEFAULT 0,
    more_currency         INTEGER DEFAULT 0,
    more_maps             INTEGER DEFAULT 0,
    more_scarabs          INTEGER DEFAULT 0,
    more_divination_cards INTEGER DEFAULT 0,

    price_amount          REAL,
    price_currency        TEXT,
    price_chaos           REAL,
    price_type            TEXT,

    -- Where the map physically is, straight from listing.stash. This is what
    -- lets the overlay draw on it without a separate stash read.
    stash_name            TEXT,
    stash_x               INTEGER,
    stash_y               INTEGER,

    indexed_at            TEXT,      -- GGG's own listing.indexed
    priced_at             TEXT,      -- last repriced, or indexed_at
    first_seen            TEXT NOT NULL,
    last_seen             TEXT NOT NULL,
    gone_first_missing    TEXT,      -- upper bound of the sale interval
    status                TEXT NOT NULL DEFAULT 'live',   -- live | gone

    predicted_chaos       REAL,
    suggested_chaos       REAL,
    interval_low_chaos    REAL,
    interval_high_chaos   REAL,
    model_version         TEXT,

    ambiguous             INTEGER DEFAULT 0,
    exclude_from_learning INTEGER DEFAULT 0,
    raw_data              TEXT
);

CREATE INDEX idx_tracked_identity
    ON tracked_listings (account, league, fingerprint, status);
CREATE INDEX idx_tracked_status ON tracked_listings (league, status, last_seen);

CREATE TABLE tracked_listing_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL REFERENCES tracked_listings(id) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    event        TEXT NOT NULL,     -- appeared | repriced | gone | reappeared
    old_price_chaos REAL,
    new_price_chaos REAL
);

CREATE INDEX idx_tracked_events_listing ON tracked_listing_events (listing_id, at);

CREATE TABLE listing_poll_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    league         TEXT NOT NULL,
    account        TEXT NOT NULL,
    listings_seen  INTEGER DEFAULT 0,
    total_reported INTEGER DEFAULT 0,
    complete       INTEGER DEFAULT 0,
    error          TEXT DEFAULT ''
);
```

---

## 6. Modules

A new `src/tracking/` package. The split exists so that everything with a
decision in it is a pure function testable without a network, a database or a
browser; only two files do I/O.

| File | Does | Depends on |
|---|---|---|
| `src/market/my_listings.py` | account-filtered search + fetch + parse into `MarketListing` | trade collector, `parse_api_item`, `parse_api_price`, `RateLimiter` |
| `src/tracking/reconcile.py` | **pure.** previous rows + new snapshot → events | nothing |
| `src/tracking/censoring.py` | **pure.** rows + events → censored observations | nothing |
| `src/tracking/survival.py` | **pure.** censored observations → hazard by ratio band | numpy |
| `src/tracking/alerts.py` | **pure.** rows + thresholds → alerts with evidence | `survival` |
| `src/tracking/reprice.py` | **pure.** stale row + fit + fair value → recommended price + reason | `survival` |
| `src/tracking/calibration.py` | **pure.** fit → personal multiplier + source string | `survival` |
| `src/tracking/poller.py` | the thread, its cadence and its cancel flag | `my_listings`, `reconcile`, repository |
| `src/ui/my_listings.py` | the page | everything above |

`src/database/repository.py` gains the read/write methods; `src/ui/settings.py`
gains the tracker settings; `app.py` gains the page in the Price group.

The poller follows `src/market/run_job.py`: a worker mutating state the page
renders from, with a `threading.Event` off switch, and no Streamlit import
anywhere in it.

---

## 7. The estimator

Everything downstream — alerts, recommendations, calibration — reads one fitted
object, so the censoring is handled once and correctly instead of three times
approximately.

**Grid.** Time since `priced_at` is bucketed: 0–1h, 1–3h, 3–6h, 6–12h, 12–24h,
1–2d, 2–4d, 4d+. Price is bucketed by ratio `r = ask ÷ model estimate`, in bands
around 1.0.

**Fit.** A discrete-time hazard per (ratio band, time bucket), fitted by
Turnbull-style EM: a sale whose bracket spans several buckets contributes
fractional weight to each, in proportion to the current hazard estimate;
right-censored listings contribute survival through the buckets they completed.
A few iterations to convergence. Around forty lines of numpy — numpy and scipy
are already dependencies, so nothing new is installed.

**What it answers.** `P(sells within W | ratio band)`, for a target window `W`.

**Degenerate cases, which are the tests.** With all brackets exact it must
reduce to the plain empirical proportion. With no data it must report
insufficient data rather than a flat curve. With data in only one ratio band it
must refuse to extrapolate to the others.

---

## 8. What the user sees

### The My Listings page

Every live listing, default-sorted by time since `priced_at`, descending. A
staleness control — "sitting for at least [ 6h | 12h | 24h | 3d | custom ]" —
drives the whole page. Per row: the map, what you asked, what the model says,
how long it has sat, how many times you have marked it down, and the
recommended adjusted price with its reason.

Three actions on the filtered set:

- **Stash search regex.** `build_highlight_batches` from
  `src/stash/highlight_regex.py`, unchanged, with the stale set as the selection
  and every other map in the tab as the exclusion corpus. It already handles the
  250-character box and already splits into batches when one pattern cannot
  cover everything. Paste it into the stash search and the maps to re-price
  light up.
- **Overlay export.** Writes the overlay state file so the stale maps are ringed
  in the real stash tab with their recommended price on the tile. Stale maps are
  still physically in the tab, which is what makes this fit where a
  sold-map overlay would not have.
- **Copy-ready price notes**, in the format the app already produces.

The trade API reports where the map physically is. Each fetch result carries
`listing.stash` with `name`, `x` and `y` -- `src/market/bulk.py` already reads
exactly those three fields. So overlay placement needs no matching against a
separate stash read and no fingerprint guesswork: the tile is drawn where GGG
says the item is. A listing whose stash block is missing or lacks coordinates is
shown as unplaced rather than drawn in a guessed cell.

### The recommended adjusted price

A map that has not sold at price `P` over `T` hours is evidence that its
clearing price is below `P`. The recommendation is the highest price whose
`P(sells within W)` is still at or above the target probability, read off the
fit — a derived number, not a fixed haircut.

Guards:

- **A floor at the model's fair-value interval.** A stale map cannot ratchet
  itself down indefinitely; below the floor the page says the market may simply
  not want it at any sensible price, rather than recommending a giveaway.
- **Before the fit has data**, fall back to a configured ladder — step down
  through the strategy tiers (`patient` → `market` → `quick_sell`) and then in
  bounded steps per staleness tier — and label it as the fallback, not as a
  measurement.
- **The reason is always shown**: "listed 62 h at 1.4× estimate; your maps at
  ≥1.3× clear within 24 h 15% of the time; 118c sits in the band that clears."

### Alerts

In-app only: a sidebar badge and a section on the page.

- **Underpriced** — went `gone` with the whole bracket below the fast threshold,
  while asking at or below the model's number.
- **Overpriced** — still live well past your normal sell time, asking above it.

Thresholds follow the idiom already in `sell_strategy.estimate_multipliers`:
config defaults, replaced by your own observed distribution once there is enough
of it, so "fast" means fast for your maps in this league rather than a number
picked in advance. Config seeds 1 h and 24 h.

---

## 9. The learning

The fit gives the ratio at which your listings clear inside your target window.
The personal multiplier is that ratio, bracketed from below by fast sales (you
would have got at least that) and from above by stale listings.

It scales the **suggested listing price**, never the fair-value estimate. Fair
value is a claim about the market; this is a claim about your selling. It
composes as a new `personal` field on `StrategyMultipliers`, applied in
`suggested_price`, so there is one seam rather than a factor sprinkled through
the pricing code.

Guards mirror `SANE_MARKET_RATIO` in the same file, which already refuses a
correction it cannot believe and says why:

- at least ~20 sold listings, with evidence on both sides (fast sales alone
  bracket the ratio only from below, which fixes no upper bound);
- the factor clamped to `[0.8, 1.3]`;
- outside that range, **refuse and report the reason** rather than silently
  scaling every price you are shown;
- an off switch in Settings, and the source string surfaced wherever the price
  is, so a personally-calibrated number never looks like a market number.

**Interaction with model calibration.** The model's known underpricing is a
hedging artefact with a measured fix (quantile alpha 0.60). This factor is
measured on top of whatever the active model already does, so the report records
which `model_version` it was fitted against and refuses to carry a measurement
across a model change — otherwise the two corrections stack and the second one
is fitted on prices the first already moved.

---

## 10. Configuration

```yaml
listing_tracker:
  enabled: false           # off until an account name is set
  poll_interval_minutes: 15
  young_listing_minutes: 60      # denser polling below this age
  young_poll_interval_minutes: 5
  stale_after_hours: 24          # page filter: what counts as 'sitting'
  fast_sale_hours: 1             # seeds the threshold before observed data
  target_window_hours: 24        # W: how fast you want a map to clear.
                                 # Same number as stale_after_hours by default,
                                 # but a different question -- one filters the
                                 # page, the other defines the fit's target.
  target_sell_probability: 0.6
  calibration:
    enabled: true
    min_sold: 20
    clamp: [0.8, 1.3]
```

`POE_ACCOUNT_NAME` is already stored by the Settings page. The tracker stays off
until it is set, and says so rather than polling an empty account name.

---

## 11. Testing

TDD throughout, no live network in any test.

- **Reconciler** against scripted snapshot sequences: a re-price restarts the
  clock; a gap widens the bracket; a partial poll suppresses disappearance
  handling entirely; a listing that reappears after `gone` is not a second sale;
  an id change with a matching fingerprint is a re-list, not a sale.
- **Censoring** — interval and right-censored observations built from event
  logs, including a map marked down twice contributing three observations.
- **Survival fit** — synthetic data with a known clearing ratio recovered within
  tolerance; exact brackets reducing to the empirical proportion; empty and
  single-band inputs refusing rather than extrapolating.
- **Reprice** — the floor holds; the fallback ladder is labelled as a fallback;
  the reason string names the numbers it used.
- **Calibration** — every guard path, including the out-of-range refusal and the
  model-version change invalidating a stored measurement.
- **Collector** against captured-shape fixtures, asserting the query body
  carries the account filter, `collapse: false` and `status: any`.
- **Regex and overlay** reuse code with existing coverage; the new tests are on
  the selection — that the stale set is what gets passed as selected and
  everything else in the tab as excluded.
- **UI smoke** in the style of `tests/test_ui_smoke.py`.

---

## 12. Order of work

1. Migration 3 and the repository methods.
2. `my_listings.py` collector against fixtures.
3. `reconcile.py` — the heart of it, and the part most worth getting right
   before anything is built on top.
4. `censoring.py` and `survival.py`.
5. `alerts.py`, `reprice.py`.
6. The `poller.py` thread.
7. The My Listings page: table, staleness filter, regex, overlay, notes.
8. `calibration.py` and the `StrategyMultipliers.personal` seam.
9. Settings, config, and the `docs/data-access.md` update.

Steps 1–7 deliver the feature you can use. Step 8 is the part that changes
numbers elsewhere in the app, and it is deliberately last and independently
switchable.
