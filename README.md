# PoE Originator Map Stash Pricer

A local tool for Path of Exile 3.29 that prices **T16 Originator Maps** from their
reward values, and prices a whole stash tab at once so you can decide what to
list and for how much without checking each map by hand.

It is an analysis tool. It never touches the game client, never modifies your
stash, and never performs a gameplay action.

---

## 1. What it does

```
Connect or import your stash
        v
Find every Originator Map in the tab
        v
Read its seven reward values
        v
Estimate a fair value, a suggested listing price and a likely range
        v
Show the closest real listings as a cross-check
        v
Draw the prices onto a picture of your stash grid
```

You get a sortable table, a visual stash grid with the price written on each
map's actual position, and per-map explanations of where the number came from.

---

## 2. What an Originator Map is

An Originator Map is a map that dropped carrying the implicit

```
Area is Influenced by the Originator's Memories
```

Those maps roll extra reward properties on top of the usual ones — extra
currency, extra maps, extra scarabs, extra divination cards — and those rewards
are what the market pays for.

One thing worth knowing up front, because it contradicts the common assumption:
**the reward values are item properties, not implicits.** They sit in the same
block as Item Quantity, above the first divider:

```
Item Class: Maps
Rarity: Rare
Terror Solace
Map (Tier 16)
--------
Item Quantity: +107% (augmented)
Item Rarity: +159% (augmented)
Monster Pack Size: +41% (augmented)
More Maps: +40% (augmented)
More Currency: +47% (augmented)
--------
Item Level: 84
--------
Area is Influenced by the Originator's Memories
```

The only real implicit is the Originator line itself, and it has no number — it
is a yes/no marker. Full findings, with what was confirmed against live 3.29 data
and what remains unknown, are in **[docs/originator-modifiers.md](docs/originator-modifiers.md)**.

---

## 3. Which values are modelled

Seven independent numeric features. They are never combined into one
hand-weighted score — the model learns their relative value and their
interactions itself.

```
quantity
rarity
pack_size
more_currency
more_maps
more_scarabs
more_divination_cards
```

A map with

```
Item Quantity: +84%   Item Rarity: +134%   Monster Pack Size: +32%
More Scarabs: +35%    More Currency: +47%
```

becomes

```json
{"quantity": 84, "rarity": 134, "pack_size": 32,
 "more_currency": 47, "more_maps": 0, "more_scarabs": 35,
 "more_divination_cards": 0}
```

Absent rewards normalize to `0`, because absence genuinely means no bonus. The
raw item data and full modifier list are preserved alongside.

### Prices are shown in chaos

Uncorrupted T16 Originator Maps mostly trade between about 50 and 200 chaos, so
"64c" is a more useful thing to read than "0.28d". Everything is stored and
modelled in chaos regardless; the display unit is one setting:

```yaml
display:
  currency: chaos    # chaos | divine | auto
```

`auto` shows chaos below ~2000c and divine above. Change it in **Settings ->
League and pricing** without restarting.

### Scope

By default the model prices **uncorrupted Tier 16** Originator Maps. Anything
else — corrupted, wrong tier, not an Originator, no rewards rolled — is still
shown in the table and grid, but marked `Unsupported` with the reason, rather
than given a questionable price.

> **Worth knowing:** on the live trade site the most expensive T16 Originator
> Maps are *corrupted* 8-mod maps, at 195–300 divine. The uncorrupted market is
> much cheaper. If you want to price the corrupted market instead, flip one flag:
>
> ```yaml
> scope:
>   require_uncorrupted: false
> ```
>
> Collector, parser, model and UI all already handle it.

---

## 4. How market data is gathered

From the **official pathofexile.com trade API**, which was verified live to
respond without authentication:

```
POST /api/trade/search/{league}
GET  /api/trade/fetch/{ids}?query={id}
POST /api/trade/exchange/{league}     # for the divine/chaos rate
```

The client:

- identifies itself with a real contact address in the `User-Agent`, in the
  format GGG's docs require;
- reads GGG's rate-limit policy out of its own response headers and waits
  accordingly, rather than guessing an interval;
- backs off on 429 instead of retrying;
- **never** bypasses Cloudflare, authentication, or a rate limit, and never
  spoofs multiple clients.

Collection collapses listings by account, is stratified by **reward feature**
(never by price), and each slice is taken sorted by listing time rather than by
price. A trade search returns at most
100 ids however many match, so whatever it sorts by decides which 100 you see —
sorting by price hands back only one end of the range, and stratifying by price
makes the sampled price distribution uniform by construction and destroys the
very relationship the model is meant to learn.

Collapsing by account matters just as much. Sellers routinely dump a whole tab at
one flat price: an uncollapsed sample had a single account contributing 142 maps
all listed at exactly 40 chaos, and 76% of all rows sat in such blocks. Inside a
block the price never moves while the rewards vary wildly, which is a direct
lesson that the rewards do not matter. See
[docs/architecture.md](docs/architecture.md#market-sampling-the-thing-that-is-easy-to-get-wrong).

If that route is ever unavailable or inappropriate, the app is fully usable on
manual imports alone — CSV, a saved trade JSON response, or a copied item plus a
price. See **[docs/data-access.md](docs/data-access.md)** for exactly what was
verified, what was not, and why.

**Instant Buyout** is not a separate feed. It is the `sale_type` filter on the
same search, and omitting the filter selects "Buyout or Fixed Price" — which is
what the collector does. Each listing's `~b/o` vs `~price` type is stored anyway,
because it is useful signal for spotting price-fixing listings.

---

## 5. How account authentication works

Honestly: **the officially supported route is currently closed.**

GGG's developer documentation states *"We are currently unable to process new
applications"*, so an OAuth client with the `account:stashes` scope cannot be
obtained right now. The full OAuth flow is implemented
(`src/auth/poe_oauth.py`) behind the same interface as everything else, and
supplying a `client_id` is the only change needed if registration reopens — but
it has never been run against a real client, and the code says so.

What works today:

| Method | Credentials | Notes |
|---|---|---|
| **POESESSID** | your own session cookie | your own credential, your own account, sent to GGG's own site |
| **Stash JSON export** | none | drop a saved stash response |
| **CSV** | none | one row per map |
| **Clipboard** | none | Ctrl+C on maps in game, paste them in |

To use POESESSID: log in to pathofexile.com, open developer tools → Application →
Cookies, copy `POESESSID` into `.env` or the Settings page. Treat it like a
password. `.env` is gitignored and the Settings page does not write it to disk.

---

## 6. Quick start

```bash
git clone <this repo>
cd poe-originator-pricer

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp config.example.yaml config.yaml   # set your league and contact address
cp .env.example .env                 # optional: POESESSID for stash access
```

Then get some data and a model:

```bash
python -m src.cli update-rate                 # live divine/chaos rate
python -m src.cli collect-market --limit 2000 # ~15-20 min, rate-limit obedient
python -m src.cli train --tune-weights
python -m src.cli status
```

And run the app:

```bash
streamlit run app.py
```

In the browser:

```
1. Connect PoE account (or pick an import mode)
2. Select league
3. Select stash tab
4. Click Analyze
5. View suggested prices
```

---

## 7. Loading a stash tab

**Stash Pricer** page → pick a source → pick tabs → **Analyze**.

You get:

- a **stash grid** with the estimated price drawn on each map's real `x`/`y`
  position, colour-banded by value (percentiles of *this tab*, not hardcoded
  league thresholds), with full details on hover;
- a **sortable table** — by value, by any reward, by pack size, by confidence, or
  by stash position;
- **filters** — minimum value, high confidence only, or only maps carrying a
  particular reward;
- per-map **fair value, suggested listing price, likely range and confidence**;
- the **closest real listings**, as an independent cross-check on the model;
- **copy-ready notes** (`~price 9.8 divine`) and a copy-all block, for you to
  type in yourself.

From the CLI:

```bash
python -m src.cli inspect-stash
python -m src.cli price-tab --tab "Originator Maps" --comparables --copy-all
python -m src.cli price-tab --json my-stash-export.json
python -m src.cli price-item map.txt
```

---

## 8. Training the model

```bash
python -m src.ml.train                 # or: python -m src.cli train
python -m src.cli train --tune-weights # also search recency-weight schedules
```

or the **Retrain model** button in Settings.

What happens:

1. The split is chosen to match how the data was gathered, and training says
   which it used. Across several collection sessions it is **chronological** —
   oldest 70% train, newest 15% held-out test — because prices move during a
   league and a random split lets the model see the future. For a **single
   snapshot** of the current listing book, ordering by listing time would
   instead measure how long a listing has sat unsold, so the split becomes
   **seller-grouped random**: whole sellers stay on one side, so the model
   cannot score well by recognising one seller's flat price.
2. Every configured backend (CatBoost, LightGBM, XGBoost, scikit-learn HGB) is
   trained and scored, **plus a Ridge baseline**. If the trees cannot beat linear
   regression, you will see that rather than have it hidden.
3. Best by median absolute error wins, is refit, and is scored once on the
   held-out slice.
4. Prediction intervals come from split-conformal calibration on validation
   residuals, as *multiplicative* ratios — an additive band is meaningless across
   a price range spanning three orders of magnitude.
5. The model is saved with its full feature schema. **A model whose features do
   not match the current parser is refused at load time**, never used.

Metrics reported: MAE, median absolute error, R², SMAPE, and the share of
predictions within 25% / 50% of the actual. MAPE only when it behaves.

All of it is visible on the **Model Analysis** page, along with feature
importance, prediction-vs-actual, residuals, two-way partial dependence for the
interesting reward interactions, and the history of every training run.

---

## 9. Current limitations

Read this part.

- **These are asking prices, not sales.** Nothing in the public data says what
  anything actually sold for. A listing sitting at 300 divine for a week is
  evidence of an *ask*, not a value. Treat every number as a starting point and
  look at the comparables next to it.

- **The seven features explain a good part of the price, but far from all of
  it.** On a clean sample, `more_currency` and `quantity` carry most of the
  signal (correlations with log price around +0.6), `pack_size` follows (+0.44),
  and `rarity`, `more_maps` and `more_scarabs` are close to flat on their own.
  On 1696 usable listings, held-out R² lands around 0.61 with a median error of
  ~15 chaos against a 45-chaos median price. Predictions are a good ballpark,
  not a quote — the comparable listings shown beside every estimate are doing
  genuine work, not decoration.

- **Every backend's score is reported, including a linear baseline.** So you can
  see whether the gradient-boosted model actually earned its place rather than
  being told one is in use.

- **The seven features do not capture map danger.** Two maps with identical
  rewards can be worth different amounts because one is far deadlier to run.
  That variance shows up as model error and there is currently no feature for it.

- **The price ceiling is deliberately low, and that is a modelling choice.**
  `market.plausible_price_chaos` is `[5, 1000]`. Roughly 95% of uncorrupted T16
  Originator Maps trade between 30 chaos and 3 divine; on a 1955-listing sample
  only 19 sat above 1000 chaos, and those are parked inventory rather than
  prices. Left in, they owned the top of every histogram and stretched the
  model's upper range. Raise the ceiling in config if you are pricing a market
  where those asks are real — corrupted 8-mod maps, for instance.

- **The uncorrupted T16 market is the cheap end.** Median ask is around 45-60
  chaos. The money is in corrupted 8-mod Originator Maps, which is a scope flag
  away (`require_uncorrupted: false`) and has far more price spread — likely a
  better-fitting model if that is what you want to price.

- **Confidence is not a probability.** It answers "how much does the market
  actually tell us about a map like this", from comparable count, neighbour
  distance, interval width and data freshness. Low confidence means treat the
  number as a rough guide.

- **The dataset is a snapshot of the current listing book, not history.** It
  samples what is listed now; it cannot see what has already sold and
  disappeared. Re-collect before trusting prices after a market move. Collecting
  on several days also unlocks the chronological split and the rolling-window
  folds, which are the stronger validation.

- **Bulk dumps are the biggest data hazard, and are handled in three places.**
  Sellers routinely list a whole tab at one flat price; an early uncollapsed
  sample had one account contributing 142 maps all at exactly 40 chaos, with 76%
  of all rows inside such blocks. Collection now collapses by account, caps
  listings per seller, and training down-weights any residual block. If you
  import market data by hand, the weighting is what protects you.

- **Stash access via POESESSID is not officially supported.** It works, but GGG
  offers no stability guarantee and the cookie expires.

- **The OAuth path is unverified.** See section 5.

- **The `More Divination Cards` property `type` id is unknown**, because it never
  appeared in the sampled listings. Parsing matches on name first, so this does
  not break anything, but it is documented rather than guessed.

- **Single league, single tier at a time.** Both are config values, not
  hardcoded, but a model is trained for one scope.

---

## 10. GGG and API compliance

- All market data comes from GGG's own endpoints, unauthenticated, at a
  self-limiting rate driven by the rate-limit policy GGG returns in its own
  response headers.
- The `User-Agent` follows GGG's documented format and carries a real contact
  address. It is configurable and you should set it to your own.
- No Cloudflare bypass. No authentication bypass. No rate-limit evasion. No
  client spoofing. No IP rotation. No reverse-engineering of restricted
  endpoints.
- No continuous background polling. Collection is something you trigger.
- No writes to the game, the stash, or item notes — there is no supported API for
  it, and doing it another way would mean automating the client. You copy prices
  in yourself.
- Every data source sits behind an abstraction, so if GGG's position or endpoints
  change, one adapter is replaced and the app keeps working on manual imports.

Details, including exactly which claims were verified live and which were not,
are in **[docs/data-access.md](docs/data-access.md)**.

---

## 11. Project layout

```
app.py                     Streamlit entry point
src/
  schema.py                the shared NormalizedMap / MarketListing types
  config.py                config.yaml + .env loading
  auth/poe_oauth.py        OAuth flow (unverified -- see docs/data-access.md)
  stash/                   official_api / json_import / clipboard_import
  parsing/                 item_parser / implicit_parser / price_parser
  market/                  collectors/, comparables, outliers, rate_limit
  database/                schema, migrations, repository
  ml/                      features, models, train, predict, confidence, explain
  pricing/                 currency, sell_strategy, fair_value
  ui/                      one module per page
  cli.py                   everything the UI does, without Streamlit
docs/
  originator-modifiers.md  Phase 1 research, confirmed / inferred / unknown
  data-access.md           Phase 2 research, what was verified live
  architecture.md          how the pieces fit and why
tests/                     145 tests, including live-render checks of every page
```

See **[docs/architecture.md](docs/architecture.md)**.

---

## 12. Tests

```bash
python -m pytest
```

Covers Originator identification, T16 detection, corruption detection, implicit
and property parsing in both formats, missing and unknown values, stash
coordinate preservation, price parsing, divine-to-chaos conversion, duplicate
detection, outlier flagging, feature construction, time-aware validation, model
serialization and schema refusal, prediction output, comparable ranking, selling
strategies — and a smoke test that renders every Streamlit page through
Streamlit's own test harness, so a broken page fails the suite instead of showing
up as a red box in your browser.
