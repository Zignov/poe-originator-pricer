Build a local application for Path of Exile 3.29 that estimates the market value of **T16 Originator Maps** based primarily on their special implicit values, and that can analyze an entire stash tab so the user can quickly price every Originator Map they own.

The end result should be something the user can actually use during gameplay:

1. gather market data from publicly available / permitted sources
2. build and maintain a dataset of Originator Map listings
3. train a price-prediction model
4. fetch or import the contents of one or more stash tabs from the user's PoE account using an officially supported or otherwise permitted mechanism
5. identify all relevant Originator Maps in those tabs
6. extract their implicit values automatically
7. estimate a price for every map
8. compare each prediction with recent comparable listings
9. present everything in a convenient UI so the user can quickly decide how to list each map for sale

The application should work without requiring special privileged API permissions wherever possible.

# Core principle

For version 1, assume that most of the value of an Originator Map is explained by its special reward-related implicit modifiers.

Do not initially model every ordinary map prefix/suffix.

The main pricing features should be:

```text
quantity
rarity
pack_size
more_currency
more_maps
more_scarabs
more_divination_cards
```

Other implicit reward types discovered during research should be added if they can legitimately occur on uncorrupted T16 Originator Maps.

Each modifier must remain an independent numeric feature.

Do not combine them into one manually weighted score.

For example:

```text
quantity = 84
rarity = 134
pack_size = 32
more_currency = 47
more_maps = 35
more_scarabs = 0
more_divination_cards = 0
```

must remain seven separate features.

The model should learn their relative value and interactions.

# Phase 1 — Research the actual Originator Map modifier pool

Before building the pricing model, research the current Path of Exile 3.29 implementation of uncorrupted T16 Originator Maps.

Determine as accurately as possible:

- every special implicit modifier that can appear
- which modifiers are mutually exclusive
- how many reward implicits can appear on one map
- possible minimum values
- possible maximum values
- possible roll increments
- whether map tier affects the ranges
- whether item level affects the ranges
- whether quality affects them
- whether corruption changes the system
- whether Originator tier/rank changes the system
- whether any implicit-looking values are actually generated from normal map properties

Focus especially on things such as:

```text
Item Quantity
Item Rarity
Monster Pack Size
More Currency
More Maps
More Scarabs
More Divination Cards
```

and any additional reward categories discovered.

Write findings to:

```text
docs/originator-modifiers.md
```

Clearly distinguish:

- confirmed information
- inferred information
- unknown information

Do not invent modifier ranges.

# Phase 2 — Investigate legal/permitted data access

Investigate the current PoE 3.29 trade site and Instant Buyout functionality.

Write:

```text
docs/data-access.md
```

Document:

- what official Path of Exile APIs currently exist
- what requires OAuth
- what requires application registration
- what requires special permissions
- what account/stash endpoints exist
- how Instant Buyout listings are delivered to the browser
- whether listing search has a documented public API
- whether current trade-search endpoints can be used without special privileges
- applicable rate limits
- whether bulk collection is permitted
- what restrictions GGG places on automated data collection
- whether user-triggered browser capture is safer than automated scraping

Do not build anything designed to:

- bypass Cloudflare
- bypass authentication
- evade rate limits
- spoof multiple clients
- circumvent access controls
- reverse-engineer restricted endpoints in violation of GGG rules

If an official API requires ordinary OAuth login but not special developer approval, using it is acceptable.

If stash access requires a user OAuth flow, implement that properly.

If an automated market collector is not appropriate without permission, provide a semi-manual collector instead.

# Phase 3 — Stash tab integration

This is a major requirement.

The user should be able to connect their Path of Exile account and analyze entire stash tabs.

Prefer an official GGG stash/account API if available and permitted.

Implement proper authentication rather than asking the user to paste session cookies where possible.

Desired workflow:

```text
Open app
    ↓
Connect Path of Exile account
    ↓
Select league
    ↓
Load stash tab list
    ↓
Choose one or more tabs
    ↓
Fetch tab contents
    ↓
Detect Originator Maps
    ↓
Parse implicit stats
    ↓
Estimate value of each map
    ↓
Show sorted pricing table
```

The app should be able to process an entire tab at once.

If official API access to private stash tabs is not available without additional approval, implement alternative import modes such as:

- user-triggered JSON export
- clipboard import
- item-text import
- browser extension/helper
- local capture of item text generated by the game
- CSV import

Do not silently fall back to prohibited scraping.

Keep stash acquisition behind an abstraction such as:

```text
src/stash/
    base.py
    official_api.py
    json_import.py
    clipboard_import.py
```

All methods should produce the same internal item schema.

# Stash item representation

For every map found in a stash tab, store enough information to identify it and re-display it.

Suggested normalized representation:

```text
stash_item_id
stash_tab_id
stash_tab_name
x
y
width
height

name
base_type
map_tier
item_level
quality
corrupted

quantity
rarity
pack_size
more_currency
more_maps
more_scarabs
more_divination_cards

raw_modifiers
raw_item_data
```

The `x` and `y` stash coordinates are important.

They let the UI show the user where each priced map physically sits in the stash.

# Originator Map identification

The parser should reliably distinguish:

- normal maps
- Originator Maps
- corrupted Originator Maps
- maps of other tiers
- maps that do not have the required implicit structure

Initially price only:

```text
uncorrupted T16 Originator Maps
```

unless research establishes a different precise eligibility rule.

Other maps should be visible but marked:

```text
Unsupported
```

rather than silently producing questionable predictions.

# Map parser

Build a robust parser that can consume both:

- structured API item JSON
- Path of Exile copied item text

Normalize modifiers into numeric fields.

For example, an item containing:

```text
Item Quantity: +84%
Item Rarity: +134%
Monster Pack Size: +32%
More Scarabs: +35%
More Currency: +47%
```

should become:

```json
{
  "quantity": 84,
  "rarity": 134,
  "pack_size": 32,
  "more_currency": 47,
  "more_maps": 0,
  "more_scarabs": 35,
  "more_divination_cards": 0
}
```

If a modifier does not exist on the map and absence genuinely represents no bonus, normalize it to `0`.

Preserve the raw data as well.

Add tests for every known implicit format.

# Phase 4 — Market dataset

Create a local database containing observed Originator Map listings.

Use SQLite for the first version.

Suggested table:

```sql
originator_listings
```

Suggested fields:

```text
id
listing_id
captured_at
league

map_tier
item_level
quality
corrupted

quantity
rarity
pack_size
more_currency
more_maps
more_scarabs
more_divination_cards

price_amount
price_currency
price_chaos

seller
source
raw_data
```

Add database migrations if practical.

# Market-data ingestion architecture

Implement market sources as interchangeable collectors.

For example:

```text
src/market/collectors/
    base.py
    manual_csv.py
    clipboard.py
    browser_export.py
    official_trade_api.py
```

The collector must normalize everything to one common schema.

The application should work even if only manual imports are available.

# Instant Buyout

Investigate the Instant Buyout section specifically.

Determine whether there is a documented and permitted way to retrieve these listings programmatically.

If yes, implement it while respecting rate limits.

If no, build a user-triggered collection workflow.

A possible safe workflow could be:

```text
User performs trade search
    ↓
User triggers browser helper
    ↓
Visible/result data is exported
    ↓
Local application parses results
    ↓
Listings enter SQLite
```

The program should never attempt to bypass site restrictions merely for convenience.

# Duplicate handling

Avoid collecting the same listing repeatedly.

Prefer a stable listing ID.

If unavailable, construct a fingerprint from fields such as:

```text
seller
price
implicit values
item identifiers
listing timestamp
```

Keep first_seen and last_seen timestamps if useful.

That can later help estimate market liquidity and stale listings.

# Currency normalization

Store:

```text
price_amount
price_currency
```

and preferably:

```text
price_chaos
```

For Divine Orb listings, convert to chaos using a configurable current exchange rate.

Possible design:

```text
currency_rates
    timestamp
    divine_to_chaos
```

Do not hardcode a permanent divine/chaos exchange rate.

The UI should allow the user to override the rate manually.

# Phase 5 — Machine learning

Start with standard tabular regression.

Do not use a neural network initially.

Test:

- CatBoost
- LightGBM
- XGBoost
- Random Forest / ExtraTrees if useful
- linear regression baseline

CatBoost is a reasonable first default.

Core features:

```text
quantity
rarity
pack_size
more_currency
more_maps
more_scarabs
more_divination_cards
```

Optionally include:

```text
hour/day
days_since_league_start
current_divine_chaos_rate
```

if the data demonstrates they improve predictions.

Do not include ordinary map affixes in the first production model.

# Target

Primary target:

```text
price_chaos
```

Also experiment with:

```python
log1p(price_chaos)
```

because listing prices are likely to have a long-tailed distribution.

Transform predictions back afterward.

# Time-aware model validation

Market prices change over time.

Do not rely only on random train/test splits.

Use something such as:

```text
oldest 70% → train
next 15% → validation
newest 15% → test
```

or rolling time-series validation.

Report:

```text
MAE
Median Absolute Error
R²
SMAPE or another sensible relative metric
```

MAPE can be included only if it behaves sensibly.

# Recent market weighting

Experiment with weighting recent listings more heavily.

For example:

```text
0–12 hours old      weight 1.0
12–24 hours         weight 0.9
1–3 days            weight 0.7
3–7 days            weight 0.4
older               weight 0.2
```

These exact values are only an example.

Tune them using validation rather than assuming they are correct.

# Bad and troll listings

Asking price is noisy.

Implement detection for:

- zero-price listings
- extreme outliers
- obviously unrealistic prices
- duplicate listings
- very old listings
- listings massively separated from comparable items

Prefer flagging suspicious observations rather than permanently deleting them.

Create fields such as:

```text
is_outlier
outlier_reason
```

# Comparable listings

Machine-learning output should never be the only evidence shown to the user.

For every stash map, find recent comparable listings.

Use normalized feature distance or nearest neighbors.

Example:

```text
Current map:

Quantity       84
Rarity         134
Pack Size      32
Currency       47
Maps           35
Scarabs         0
Div Cards       0

Closest recent listings:

Currency Maps Pack Qty Price   Age
46       36   31   82  9.3d    18m
49       33   34   87  10.0d   42m
44       37   32   80  9.0d    1h
```

This provides an excellent sanity check.

# Prediction interval

Do not return just one number.

Return approximately:

```text
Estimated fair value: 9.6 div

Suggested listing:
9.8 div

Likely range:
8.9–10.5 div

Confidence:
High
```

Possible approaches:

- quantile regression
- conformal prediction
- ensembles
- residual-based intervals

Pick something defensible.

# Suggested listing price

Separate:

```text
estimated fair market value
```

from:

```text
suggested listing price
```

The user may want to sell quickly or maximize value.

Add configurable selling strategies:

```text
Quick sell
Market
Patient
```

Example behavior:

```text
Fair value: 10.0 div

Quick sell:  9.4 div
Market:      9.9 div
Patient:    10.5 div
```

Do not use fixed percentages forever.

Once sufficient market data exists, estimate these adjustments from actual spread/liquidity behavior.

# Stash-tab pricing screen

This should become the main user-facing workflow.

After loading a stash tab, display all Originator Maps in a sortable table.

Example:

| Pos | Currency | Maps | Scarabs | Div Cards | Pack | Qty | Rarity | Fair Value | Suggested | Confidence |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A1 | 47 | 35 | 0 | 0 | 32 | 84 | 134 | 9.6d | 9.8d | High |
| B1 | 28 | 48 | 0 | 0 | 35 | 91 | 120 | 8.2d | 8.0d | Medium |
| C1 | 0 | 32 | 42 | 0 | 30 | 77 | 145 | 6.9d | 6.8d | High |

Allow sorting by:

```text
highest value
lowest value
more currency
more maps
more scarabs
pack size
confidence
stash position
```

# Stash visualizer

Also create a visual stash-tab representation.

If the tab layout is grid-based, render the stash grid and mark the relevant map positions.

For each map tile, show a compact estimated price such as:

```text
9.8d
```

Hovering or clicking should show:

```text
Quantity: 84
Rarity: 134
Pack: 32
Currency: 47
Maps: 35

Fair value: 9.6d
Suggested: 9.8d
Range: 8.9–10.5d
```

This feature is important because it makes the tool practical during selling.

The user should be able to look at the app and immediately see which physical map in the tab corresponds to each price.

# Color coding

Use simple visual categories such as:

```text
low value
medium value
high value
very high value
```

Do not hardcode league-specific thresholds permanently.

Prefer percentile-based thresholds calculated from the current stash or market.

# Optional clipboard workflow

Add a convenience button:

```text
Copy price
```

For example:

```text
~price 9.8 divine
```

or the appropriate Path of Exile note format.

Also provide:

```text
Copy all recommendations
```

which might produce:

```text
A1 → 9.8d
B1 → 8.0d
C1 → 6.8d
D2 → 12d
```

Do not automatically modify the game client or perform gameplay actions.

The application is for analysis and pricing assistance.

# Optional bulk-price workflow

If there is a legitimate API or supported mechanism for modifying stash item notes, investigate it.

Do not implement automated write operations unless they are officially supported and appropriate.

Otherwise simply produce recommended prices for the user to enter manually.

# Search by map

Allow the user to manually enter an Originator Map without loading a stash.

Input:

```text
Quantity
Rarity
Pack Size
More Currency
More Maps
More Scarabs
More Divination Cards
```

Output:

```text
Estimated fair price
Suggested sell price
Confidence interval
Comparable listings
Feature explanation
```

Also allow:

```text
Paste item text
```

so a Ctrl+C item from the game can be priced instantly.

# Feature explanation

Use SHAP or another suitable model-explanation tool.

Example UI:

```text
Estimated baseline                3.2 div

+47% More Currency               +3.1 div
+35% More Maps                   +1.8 div
32% Pack Size                    +0.7 div
84% Quantity                     +0.4 div
134% Rarity                      +0.1 div
-----------------------------------------
Estimated value                   9.3 div
```

These numbers must be generated by the model.

Do not create hand-written weights.

# Feature interactions

Tree-based models should naturally learn interactions.

Analyze important combinations such as:

```text
currency × maps
currency × pack size
maps × pack size
scarabs × pack size
div cards × pack size
quantity × pack size
```

Use SHAP interaction values or partial-dependence plots where practical.

# Model confidence

Create a useful confidence score.

Factors can include:

- number of comparable recent listings
- distance to nearest training examples
- disagreement between ensemble models
- width of prediction interval
- age of available market observations

Example:

```text
High:
many close recent comparables

Medium:
reasonable training support

Low:
unusual implicit combination or sparse market data
```

Do not present low-confidence estimates as precise.

# UI

Use a local web application.

Streamlit is acceptable for the first implementation.

A future migration to a richer frontend can happen later.

Suggested pages:

```text
Dashboard
Stash Pricer
Single Map
Market Data
Model Analysis
Settings
```

# Dashboard

Show:

```text
Listings in dataset
Latest market update
Current divine/chaos rate
Model MAE
Number of stash tabs available
Last model training time
```

# Stash Pricer

Workflow:

```text
Choose league
Choose stash tab
Click Analyze
```

Then show:

- visual stash layout
- table of Originator Maps
- fair price
- suggested listing price
- prediction interval
- confidence
- closest listings

Allow filters such as:

```text
Only maps above 5 div
Only high-confidence predictions
Only More Currency maps
Only More Maps maps
```

# Single Map

Support both:

```text
manual numeric input
```

and:

```text
paste copied item text
```

# Market Data

Show:

- number of listings
- listings collected today
- price distributions
- common implicit combinations
- latest captures
- flagged outliers
- CSV export

# Model Analysis

Show:

```text
validation metrics
feature importance
SHAP summary
prediction vs actual
residuals
historical accuracy
```

# Settings

Include:

```text
league
currency conversion rate
market-data age limit
quick-sell strategy
default stash tab
model choice
retraining interval
```

# Suggested project structure

Use Python.

Something like:

```text
poe-originator-pricer/
│
├── README.md
├── requirements.txt
├── pyproject.toml
├── config.example.yaml
├── .env.example
│
├── app.py
│
├── docs/
│   ├── data-access.md
│   ├── originator-modifiers.md
│   └── architecture.md
│
├── src/
│   ├── auth/
│   │   └── poe_oauth.py
│   │
│   ├── stash/
│   │   ├── base.py
│   │   ├── official_api.py
│   │   ├── json_import.py
│   │   └── clipboard_import.py
│   │
│   ├── parsing/
│   │   ├── item_parser.py
│   │   ├── implicit_parser.py
│   │   └── price_parser.py
│   │
│   ├── market/
│   │   ├── collectors/
│   │   │   ├── base.py
│   │   │   ├── official_trade_api.py
│   │   │   ├── browser_export.py
│   │   │   └── manual_import.py
│   │   └── comparables.py
│   │
│   ├── database/
│   │   ├── models.py
│   │   ├── repository.py
│   │   └── migrations/
│   │
│   ├── ml/
│   │   ├── features.py
│   │   ├── train.py
│   │   ├── predict.py
│   │   ├── validation.py
│   │   ├── confidence.py
│   │   └── explain.py
│   │
│   ├── pricing/
│   │   ├── fair_value.py
│   │   ├── sell_strategy.py
│   │   └── currency.py
│   │
│   └── ui/
│       ├── dashboard.py
│       ├── stash_pricer.py
│       ├── single_map.py
│       ├── market_data.py
│       └── model_analysis.py
│
├── data/
│   └── originator.db
│
├── models/
│   └── .gitkeep
│
└── tests/
    ├── test_item_parser.py
    ├── test_implicit_parser.py
    ├── test_stash_parser.py
    ├── test_price_parser.py
    ├── test_duplicates.py
    ├── test_features.py
    └── test_prediction.py
```

# Tests

At minimum, create tests for:

```text
Originator Map identification
T16 detection
corruption detection
implicit parsing
missing implicits
unknown implicit handling
structured API item parsing
copied item-text parsing
stash-coordinate preservation
price parsing
divine-to-chaos conversion
duplicate listing detection
market outlier flagging
feature construction
model serialization
prediction output
comparable-search ranking
```

# Database tables

Consider approximately:

```text
market_listings
stash_snapshots
stash_items
currency_rates
model_runs
predictions
```

A stash snapshot should let the user re-open an old scan and compare how recommended prices changed later.

# Prediction history

Each time a stash is analyzed, optionally save:

```text
timestamp
item fingerprint
implicit values
predicted fair price
suggested price
prediction interval
model version
```

This will eventually let us measure whether the estimator was calibrated well.

# Re-training

Add a command such as:

```bash
python -m src.ml.train
```

and a UI button:

```text
Retrain model
```

The model should save metadata:

```text
training timestamp
dataset size
feature list
validation metrics
market date range
model version
```

Never load a model if its feature schema does not match the current parser schema.

# Command-line tools

Also provide useful CLI commands so the project is not dependent entirely on Streamlit.

Examples:

```bash
python -m src.cli inspect-stash
python -m src.cli import-market listings.json
python -m src.cli train
python -m src.cli price-item item.txt
python -m src.cli price-tab --tab "Originator Maps"
```

# README

Write a strong README explaining:

1. what the program does
2. what an Originator Map is
3. which values are modeled
4. how market data is gathered
5. how account authentication works
6. how to load a stash tab
7. how to train the model
8. how to price a stash tab
9. current limitations
10. GGG/API compliance considerations

Include a quick-start section.

Example desired user experience:

```bash
git clone ...
cd poe-originator-pricer

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

streamlit run app.py
```

Then in browser:

```text
1. Connect PoE account
2. Select league
3. Select stash tab
4. Click Analyze
5. View suggested prices
```

# Implementation priorities

Do this in stages.

## Milestone 1 — Core parser and model

Build:

- project skeleton
- SQLite
- Originator parser
- copied-item parser
- manual market-data import
- sample dataset support
- model training
- single-map prediction
- comparable listing search
- tests

At the end of Milestone 1, I should be able to paste one map and get a price estimate.

## Milestone 2 — Stash tab reader

Build:

- account authentication if officially available
- stash-tab enumeration
- tab fetching
- Originator detection
- bulk parsing
- stash-coordinate support
- table of estimated prices

At the end of Milestone 2, I should be able to select a stash tab and get estimates for every relevant map.

## Milestone 3 — Stash visualizer

Build:

- visual stash grid
- estimated-price overlays
- click-to-inspect
- sorting/filtering
- copy-price functions

At the end of Milestone 3, the tool should be convenient enough to use while selling maps.

## Milestone 4 — Market-data automation

Only after determining what data access is allowed, add the best legitimate market-data collection method.

Implement proper throttling and caching.

Never compromise the project by building around a fragile or prohibited scraping method.

## Milestone 5 — Better ML

Add:

- model comparison
- time-aware validation
- prediction intervals
- SHAP
- confidence scoring
- market-regime features
- optional recency weighting

# Important technical philosophy

Keep the system modular.

The three major components must remain independent:

```text
STASH DATA
    ↓
item parser
    ↓
normalized map

MARKET DATA
    ↓
listing parser
    ↓
normalized map + price

MODEL
    ↓
normalized map
    ↓
estimated value
```

This means that if GGG changes its APIs later, we only need to replace the relevant data-access module.

# Avoid premature complexity

Do not initially build:

- deep neural networks
- reinforcement learning
- computer vision
- OCR
- a huge cloud infrastructure
- distributed databases
- a complicated JavaScript frontend

This is a local pricing tool.

A clean Python + SQLite + CatBoost + Streamlit implementation is preferable.

# Success criteria

The first genuinely useful version should allow this workflow:

```text
User opens application.

User connects/imports their Path of Exile stash.

User selects:
"Originator Maps"

Application finds 27 eligible T16 Originator Maps.

Application extracts the seven relevant implicit values from each.

Application compares them to recent market observations.

Application predicts:

Map at A1 → fair value 4.8d → suggest 4.9d
Map at C1 → fair value 9.4d → suggest 9.5d
Map at D2 → fair value 14.1d → suggest 14d
Map at E3 → fair value 6.2d → suggest 6.3d

The user can sort from most valuable to least valuable.

Clicking a map shows the closest current trade listings and explains the estimate.

A visual stash grid shows the predicted price directly over each map's stash position.
```

That is the primary product.

The ML model is a means to achieve that experience, not the product by itself.

# Final requirement for Claude Code

Before coding, inspect the repository and produce a short implementation plan.

Then work milestone by milestone.

After every milestone:

1. run the relevant test suite
2. run any lint/type checks that exist
3. manually verify the main workflow
4. fix failures before continuing
5. summarize what is complete and what remains

Do not claim that API access, stash access, Instant Buyout access, modifier ranges, or market-data collection methods work until they have actually been verified against current Path of Exile 3.29 documentation or live behavior.

Where current information is uncertain, build an interface/abstraction and document the uncertainty rather than hardcoding assumptions.