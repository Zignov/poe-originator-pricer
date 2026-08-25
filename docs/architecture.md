# Architecture

The design goal is that GGG can change an API without this project needing more
than a new adapter. Three subsystems meet only at one shared data type.

```
STASH DATA                    MARKET DATA                    MODEL
  official_api.py               official_trade_api.py          train.py
  json_import.py                manual_import.py               predict.py
  clipboard_import.py           browser_export.py              explain.py
        |                              |                          |
        v                              v                          |
   item_parser.py                item_parser.py                   |
        |                         + price_parser.py               |
        v                              v                          |
  NormalizedMap  ------------>  MarketListing  --------------->  |
        |                        (SQLite)                        |
        |                                                        v
        +----------------------------------------------> fair_value.py
                                                                 |
                                                                 v
                                                            PricingService
                                                                 |
                                                                 v
                                                              src/ui/
```

## The seam: `src/schema.py`

`NormalizedMap` is the only thing the three subsystems agree on. Every stash
adapter returns it. Every market collector wraps one in a `MarketListing`. The
model consumes nothing else.

`FEATURE_NAMES` is a fixed, ordered tuple of the seven pricing features. It is
written into the trained model's metadata and re-checked at load time — a model
whose feature schema differs from the current parser's is **refused**, not used
(`ModelSchemaMismatch`). This is the guard against a parser change silently
producing wrong prices.

## Layers

| Package | Responsibility | Knows about |
|---|---|---|
| `src/schema.py` | the shared item and listing types | nothing |
| `src/parsing/` | item JSON and item text -> `NormalizedMap`; price text -> amounts | schema |
| `src/stash/` | where stash items come from | schema, parsing |
| `src/market/` | where market listings come from, plus outliers and comparables | schema, parsing, database |
| `src/database/` | SQLite schema, migrations, all SQL | schema |
| `src/ml/` | features, backends, training, prediction, confidence, explanation | schema, database |
| `src/pricing/` | currency, selling strategy, and the `PricingService` façade | everything below |
| `src/overlay/` | the desktop overlay: its JSON contract, calibration, layout and tkinter window | schema, pricing, the palette |
| `src/ui/` | Streamlit pages | `PricingService` and repositories only |
| `src/cli.py` | the same operations without Streamlit | same as UI |

Nothing in `src/ml/` imports anything from `src/ui/`. Nothing in `src/parsing/`
imports from `src/market/` or `src/stash/`. Nothing in `src/overlay/` imports
Streamlit -- it runs in its own process, and `src/ui/palette.py` exists so the
two can share the value-band colours without one dragging the other in.

### The overlay is a second process, joined by a file

Streamlit re-runs its script on every widget touch; tkinter wants the main
thread and a blocking `mainloop`. They cannot share a process, so the Stash
Pricer page writes `data/overlay_state.json` and the overlay watches it. That
file is the whole interface between them, it is versioned, and it is defined in
one place: `src/overlay/state.py`.

Everything with a decision in it -- coordinate maths, colour bands,
serialisation, the resolution-mismatch check -- is a plain function or frozen
dataclass in `state.py` / `calibration.py` / `layout.py`, tested headlessly.
`window.py` is a renderer with no judgement in it, which is why nothing in the
test suite needs a display.

## Adapters

Both extension points are abstract base classes with a two-method contract:

```python
class StashSource:
    def list_tabs(self, league) -> list[StashTab]
    def fetch_tab(self, league, tab) -> list[NormalizedMap]
    def available(self) -> tuple[bool, str]     # and why not, if not

class MarketCollector:
    def collect(self, **kwargs) -> Iterator[MarketListing]
    def available(self) -> tuple[bool, str]
```

`available()` returning a *reason* is what lets the UI explain to the user why a
source is greyed out instead of just failing.

Adding a source is one file plus a registration line in the UI. Replacing a
broken GGG endpoint touches exactly one adapter.

## Data flow for the main workflow

```
Stash Pricer page
  -> StashSource.fetch_tabs()          # any adapter
  -> item_parser.classify()            # sets supported / unsupported_reason
  -> PricingService.price_many()
       -> Predictor.estimate()         # model + conformal interval
       -> confidence.score_confidence() # neighbours, spread, freshness
       -> comparables.find_comparables() # independent cross-check
       -> sell_strategy.suggested_price()
  -> stash_grid.render_grid()          # prices drawn on real x/y positions
  -> repository.save_snapshot()        # optional, re-openable later
```

## Market sampling: the thing that is easy to get wrong

A trade search returns **at most 100 result ids**, however many listings match.
That single constraint decides everything about how the dataset must be built,
and getting it wrong produces a model that fails silently.

**What was tried first, and why it was wrong.** The collector stepped through
chaos-equivalent price bands and took an equal share from each, so the sample
would span the whole price range. It does span it — and it also makes the
sampled price distribution roughly *uniform by construction*. That is sampling
on the dependent variable. The collected data came back with a flat ~300 chaos
median at every level of every reward feature, and the model scored R² ≈ 0. No
error was raised anywhere; the pipeline simply learned that price does not depend
on the features, because in that sample it genuinely did not.

**What it does now.** Two rules:

1. **Never stratify on price.** Stratify on *features* — the four reward pseudo
   stats and the pack-size filter — so rare reward combinations are represented,
   without touching the target's distribution.
2. **Sort by `indexed`, not by price.** Whatever the search sorts by decides
   which 100 of the matches come back. `{"sort": {"price": "asc"}}` returns only
   the cheapest; `{"sort": {"indexed": "desc"}}` returns the most recently
   listed, which is unbiased with respect to price.

3. **Collapse listings by account.** This one is easy to miss and does the most
   damage. Sellers dump whole tabs at a single flat price: before collapsing, one
   account contributed 142 maps all at exactly 40 chaos, another 140 at exactly
   100, and **76% of all collected rows sat inside such blocks**. Within a block
   the price is constant while the rewards vary wildly, so every row is evidence
   against the rewards mattering, and one whale outvotes a hundred independent
   sellers. `trade_filters.collapse = true` fixes it server-side: the same query
   went from 3 unique accounts in the first 10 results to 10, and from 205
   sellers across 2029 rows to 450 sellers across 820. A per-seller cap in the
   collector and a bulk-block sample weight in training
   (`features.bulk_listing_weights`) are the backstops, since manual imports get
   no server-side help.

Plus one unstratified slice, so the natural distribution of the listing flow is
in the data at all.

`tests/test_market_sampling.py` asserts both rules, because this is a failure
mode with no stack trace.

## Model pipeline

1. `repository.listing_rows()` — league, tier, corruption and outlier filters
   applied in SQL.
2. `features.rows_to_frame()` — seven core columns, plus optional context columns
   appended (never interleaved).
3. `validation.time_split()` — chronological 70/15/15. Never random: prices move
   during a league, and a random split lets the model see the future.
4. Each configured backend is trained on the training slice with recency weights
   and scored on validation. A Ridge baseline is always included, so a tree model
   that fails to beat linear regression is visible rather than assumed good.
5. Best by median absolute error is refit on train+validation and scored once on
   the newest held-out slice.
6. Split-conformal calibration on validation residuals, expressed as
   *multiplicative* ratios (`actual / predicted`), because an additive band would
   be meaningless across a price range spanning three orders of magnitude.
7. Rolling expanding-window folds as a second opinion.
8. Final model refit on everything, pickled together with its metadata and the
   training feature matrix (needed for nearest-neighbour confidence and SHAP
   background), and recorded in `model_runs`.

### Why the training matrix is stored in the artifact

Confidence scoring and the SHAP background both need the training distribution.
Storing it with the model keeps the artifact self-describing: a model file can be
copied elsewhere and still produce calibrated confidence.

## Database

Six tables, one forward-only migration list in `src/database/models.py`.
`schema_version` records what has been applied. Migrations are append-only —
never edit a released entry.

| Table | Purpose |
|---|---|
| `originator_listings` | market observations, with `dedupe_key`, `first_seen`, `last_seen`, `is_outlier`, `outlier_reason` |
| `currency_rates` | timestamped divine/chaos history |
| `stash_snapshots` / `stash_items` | a saved scan, re-openable to compare later |
| `model_runs` | every training run, its metrics and which one is active |
| `predictions` | what was predicted for which item when, for later calibration checks |

Prices are stored as both `price_amount` + `price_currency` **and** a derived
`price_chaos`. Changing the divine rate calls `recompute_price_chaos()` rather
than leaving stale derived values behind.

## Units

Everything is stored, computed and modelled in **chaos**. `src/pricing/display.py`
is the only place that decides what unit the user reads, so switching between
chaos, divine and auto never touches the model, the database, or the pricing
logic. `PricingService` carries one `PriceFormatter`, and every page and CLI
command formats through it.

## Deliberate non-goals

No neural networks, no OCR, no computer vision, no cloud infrastructure, no
JavaScript frontend, no distributed database. This is a local tool: Python +
SQLite + gradient boosting + Streamlit.

Gradient-boosting backends are all optional. If none of catboost / lightgbm /
xgboost import, `sklearn_hgb` is always available and training still works.
