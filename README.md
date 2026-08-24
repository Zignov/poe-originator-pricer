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

### Running it on Streamlit Community Cloud

`app.py` is the entry point and a clean checkout boots without any local setup:
`config.yaml` is gitignored, and `src/config.py` falls back to
`config.example.yaml` when it is absent. Point Community Cloud at this repo with
`app.py` as the main file and it redeploys on every push to `main`.

Two things do not survive the trip, both by design:

* **The database and the trained model are not in the repo** (`data/*.db`,
  `models/*.pkl`). A cloud instance therefore starts with no listings and no
  model, and Community Cloud's disk does not persist across restarts. Collect
  from the Market Data page to fill it, or import a CSV export. Treat a cloud
  instance as a demo; the real dataset lives on the machine that collected it.
* **`POE_CONTACT_EMAIL` must be set as a secret.** Without it the User-Agent
  goes out as `you@example.com`, and GGG asks automated clients to identify
  themselves with a real address (see §12). `POE_LEAGUE` can be set the same way
  to override the league without editing config.

POESESSID is deliberately *not* something to put in cloud secrets — it is a
session cookie for your own account, and §5 covers why it stays local.

---

## 7. Loading a stash tab

**Stash Pricer** page → pick a source → pick tabs → **Analyze**.

You get:

- a **stash grid** with the suggested listing price for the selected selling
  strategy drawn on each map's real `x`/`y` position — hover a tile for the
  fair value and the exalt call behind it — outlined by whether an Exalted Orb
  pays for itself (solid cyan yes, dashed no, no outline on a map that cannot
  take one at all), colour-banded by value (percentiles of *this tab*, not hardcoded
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

## 8. Affix count and whether a map is worth an Exalted Orb

Every map loaded from a stash source shows how many affixes it carries — `5/6`,
or `8/8` on a corrupted one — and, where a slot is still open, what an Exalted
Orb is expected to be worth on it.

**Counting affixes is not counting modifier lines.** One affix can print several
lines — a real stash map showed eleven lines when a map holds at most eight
affixes — so counting lines over-counts badly.

Trade listings name every affix and its tier outright, and that is read directly.
**A stash does not.** GGG's stash endpoints return each modifier as description
text with no name, tier or `extended` block; verified against a live account,
321 modifier lines across 45 rare maps carried none. So stash maps are resolved
from their text instead, against a lookup harvested from the collected market,
and the reading is checked against the map's own Item Quantity — quantity is
exactly the sum of per-affix contributions, so "one affix or two?" implies
different totals and the reported number settles it.

Measured against ground truth on 1838 listings, with names and tiers stripped to
imitate a stash payload: **the affix count is correct on every one**, the
prefix/suffix split is wrong on 14, and 5 report unknown rather than guess.

**The verdict is arithmetic, not a heuristic.** A map's `Item Quantity: +84%` is
the *sum* of what each rolled affix contributes, so the whole contribution table
can be recovered from listings that show both their affixes and their totals. On
1696 listings and 108 affixes, an 80/20 split predicts held-out quantity at
R² 0.999 with a mean absolute error of 0.03 — this is inverting a genuinely
additive system, not fitting a curve to it.

So the estimate enumerates rather than samples. It takes every affix that could
still roll — excluding those already on the map, and respecting a full prefix or
suffix block — applies each one's exact contribution, prices the result with the
same model that priced the map, and weights by how often that affix actually
appears in the collected market:

```
5/6 affixes  ·  1 open suffix  ·  orb costs 4c
Worth exalting. Expected 189c after, a net gain of 85c once the orb is paid for.
Likely range: 68c to 129c gain     Best case:  of Defiance — +129c
Chance it beats the orb: 100%      Worst case: of Persistence — +68c
```

**Not every reward is affix-driven, and the catalogue refuses the ones that are
not.** Each feature has to predict held-out data before it is modelled.
`more_divination_cards` scores a held-out R² near zero because it comes from the
Originator memory rather than a rolled affix — so exalting is never credited with
adding any, where a less careful version would have invented value on every
estimate. Which rewards passed is printed when the model trains.

The orb price is fetched from the same source as the divine rate, on the Market
Data page, and stored alongside it, because it is the entire right-hand side of
the decision.

**Both prices come from [poe.ninja](https://poe.ninja/poe1/economy), and the
choice of source did more work than any statistic.** An earlier version read
GGG's live bulk-exchange order book and tried to reduce it to a price. That is
the wrong shape of data: a book is what a few dozen people are asking right now,
while a rate is what the currency actually changes hands at. Turning the first
into the second means guessing which offers are real. On the live exalted book —
`1, 2, 2.95, 3, 20, 25, 50, 50, 90, 150` chaos — that version discarded the 1-3
cluster as bait and returned **25**. poe.ninja puts the Exalted Orb at **1.25
chaos** against 47k volume an hour. The discarded cluster *was* the market.

poe.ninja aggregates the in-game Currency Exchange over a window, so the number
is smooth and carries a volume figure the app displays alongside it —
`primaryValue` and `volumePrimaryValue` from
`/poe1/api/economy/exchange/current/overview`, cross-checked against what
poe.ninja's own page renders. One request returns every currency.

Two things worth knowing:

* **The Exalted Orb is cheap.** Since 3.19 it is an ordinary crafting currency,
  and at ~1.25 chaos it is nearly free against a map worth 100. A stale value of
  10 or 25 does not shift the exalt verdict, it inverts it.
* **poe.ninja also publishes a `stash` overview** built from trade-site listings
  rather than the exchange. It disagrees — divine 189 against 211, exalted 3.02
  against 1.25 — because listings are what people ask and the exchange is what
  clears. `EXCHANGE` is the default; `STASH` stays reachable in
  `src/market/currency_source.py` for comparison.

Both prices are editable by hand in **Settings → League and pricing**, and the
league they are read for is the League setting there, now a list of what GGG is
actually serving rather than a free-text box. An unrecognised league is worth
calling out: poe.ninja answers one with HTTP 200 and an empty result, so the app
names that case explicitly rather than reporting it as a market with nothing to
say.

---

## 9. Finding bulk lots to buy

Originator Maps are bought in bulk, and the **Bulk Buy** page finds sellers with
several listed at once. It is the inverse of market collection on every axis, and
deliberately so: collection collapses listings by account, caps how many any one
seller contributes, and never sorts by price, all to keep flat-priced tab dumps
from teaching the model that rewards do not affect price. A buyer wants exactly
those dumps.

**Nothing it finds is stored**, so that protection stays intact.

**It sorts by newest, not cheapest.** Cheapest-first sorts you into the floor of
the market, which is gone within seconds — a live probe found the hundred
cheapest listings were all 1-2 chaos and already contested. Sorting by listing
time under a price ceiling returns what appeared in the last minute inside a
range worth buying:

```
FULL SCAN        2.7s   100 listings, 15 sellers, 6 lots of 5+
  37 maps @  9c    337c total   listed 1 minute ago
  18 maps @ 10c    180c total   flat-priced
  17 maps @  8c    146c total
```

**Re-checking one seller is a single query**, so it returns in a second or two
rather than sweeping the market — and it sees more, because a market-wide scan
only shows the listings that fit inside the newest hundred. The seller above had
37 maps in the scan and 60 on a targeted re-check.

A scan older than two minutes says so, because the cheaper lots will have gone.

**Buying happens on GGG's own page.** Each lot links straight to that seller's
maps on the trade site, where GGG's purchase controls and its **Travel to
Hideout** button live. The whole query travels inside the link, so building one
costs no request at all — verified live: the site parses it, re-runs the search
and rewrites the address to its own id.

Reproducing what that Travel to Hideout button does is not on the table. It would
mean reverse-engineering an endpoint outside the published documentation, which
GGG's developer terms name as grounds for immediate termination, and sending
input to the game client, which the Terms of Use prohibit outright. Linking to
the page that already has the button is the whole of what this app does.

For anyone who would rather not go through the site, each lot also shows GGG's
own message text for every listing, ready to copy and paste in game yourself. The
app never sends it.

### Live search, and why it is not built in

A scan is a snapshot, and these lots do not last: while this page was being
built one seller went from 51 maps to 26 to 0 in about five minutes. GGG's
**live search** solves that properly — it pushes each new listing the moment it
appears, so there is nothing to race.

It is not implemented here, and that is deliberate. Live search runs on a
WebSocket endpoint that appears nowhere in GGG's developer documentation, which
lists every endpoint they publish and contains no real-time interface at all. A
client for it would mean reverse-engineering an undocumented endpoint — named in
GGG's developer terms as grounds for immediate termination — while holding a
standing authenticated connection and monitoring continuously, which is the
automated behaviour the Terms of Use prohibit and the opposite of this project's
"collection is something you trigger" rule. The account carrying that risk would
be yours, not the author's.

So the Bulk Buy page builds the query and hands it over: one link opens the
search on GGG's page with **Activate Live Search** right there, and the purchase
and Travel to Hideout buttons on every row. Same benefit, on the sanctioned
client. A test asserts no WebSocket client has crept into `src/`.

---

## 10. Training the model

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

## 11. Current limitations

Read this part.

- **These are asking prices, not sales.** Nothing in the public data says what
  anything actually sold for. A listing sitting at 300 divine for a week is
  evidence of an *ask*, not a value. Treat every number as a starting point and
  look at the comparables next to it.

- **The seven features explain a good part of the price, but far from all of
  it.** On a clean sample, `more_currency` and `quantity` carry most of the
  signal (correlations with log price around +0.6), `pack_size` follows (+0.44),
  and `rarity`, `more_maps` and `more_scarabs` are close to flat on their own.
  On 1696 usable listings, held-out R² lands around 0.66 with a median error of
  ~13 chaos against a 45-chaos median price. Predictions are a good ballpark,
  not a quote — the comparable listings shown beside every estimate are doing
  genuine work, not decoration.

- **The model is constrained so that more reward can never mean a lower price.**
  All seven reward features are monotonically non-decreasing, enforced in every
  backend. This is not decoration: the unconstrained model priced a strictly
  better map *lower* on 11% of maps, by as much as 77 chaos, which silently
  inverts the exalt calculation — that being nothing but a comparison between a
  map and a better version of itself. Constraining it also improved every
  held-out metric, because the constraint is true and acts as a regulariser.

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

## 12. GGG and API compliance

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

## 13. Project layout

```
app.py                     Streamlit entry point
src/
  schema.py                the shared NormalizedMap / MarketListing types
  config.py                config.yaml + .env loading
  auth/poe_oauth.py        OAuth flow (unverified -- see docs/data-access.md)
  stash/                   official_api / json_import / clipboard_import
  parsing/                 item_parser / implicit_parser / price_parser / affixes
  market/                  collectors/, comparables, outliers, rate_limit
                           bulk -- the buying path, never stored, never trained on
  database/                schema, migrations, repository
  ml/                      features, models, train, predict, confidence, explain
                           affix_catalogue -- per-affix reward contributions
  pricing/                 currency, sell_strategy, fair_value, exalt
  ui/                      one module per page
  cli.py                   everything the UI does, without Streamlit
docs/
  originator-modifiers.md  Phase 1 research, confirmed / inferred / unknown
  data-access.md           Phase 2 research, what was verified live
  architecture.md          how the pieces fit and why
tests/                     275 tests, including live-render checks of every page
```

See **[docs/architecture.md](docs/architecture.md)**.

---

## 14. Tests

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
