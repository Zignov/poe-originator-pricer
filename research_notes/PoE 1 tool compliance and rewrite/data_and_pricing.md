# PoE 1 Pricing Tool: Data Acquisition Strategy and Item Valuation Methodology

Research date: 2026-11-22. Scope: Path of Exile **1** only. Target use case: pricing rare
high-tier maps ("Originator" maps) carrying Item Quantity / Item Rarity / Pack Size and
reward modifiers, in a single-user desktop tool.

---

## League context (affects every pipeline decision)

### Takeaway
The current PoE 1 league is **3.30.0 "Covens of Umbra", launched 13 November 2026** — nine
days old as of this research. GGG is still actively patching item drops and league-mechanic
rewards, so map prices are in their most volatile phase and any trained model will decay in
days, not weeks.

### Cited Findings
- "Path of Exile 1: Covens of Umbra" released as Content Update 3.30.0, launching November 13 (PST), confirmed live that day; no end date announced — [pathofexile.com news archive](https://www.pathofexile.com/news/archive)
- GGG post "Improvements to Core Item Drops" (18 Nov 2026) states the 3.30 item-generation changes "did not work out as planned for currency" and outlines planned changes — [pathofexile.com news archive](https://www.pathofexile.com/news/archive)
- GGG "What We're Working On: Endgame" (16 Nov 2026) notes "big gaps in item drops" between the highest- and lowest-performing league mechanics; a 17 Nov post says thread difficulty is being lowered and league mechanic rewards raised in a patch later that week — [pathofexile.com news archive](https://www.pathofexile.com/news/archive)
- The prior league was 3.29 "Curse of the Allflame", started 24 July 2026 — [playnews.gg guide](https://www.playnews.gg/en/guides/path-of-exile-in-2026-guide-to-the-curse-of-the-allflame-league-best-beginner-builds-and-why-poe-1-still-holds-up-against-poe-2); one source gives its end as 9 Nov 2026 — [seasontimer.live](https://seasontimer.live/poe/)
- Public stash API "doesn't contain any historical data and always returns the current state of stash tabs" — [poedb API:Public Stashes](https://poedb.tw/API%3APublic_Stashes)

### Inferences
- A ~3.5-month league cadence (Jul 24 → Nov 13) means a model's useful lifetime is bounded by the league, and the first ~2 weeks are the highest-drift period. A pipeline that cannot rebuild its training set from scratch in under an hour is unusable at league start.
- Because GGG is *actively rebalancing drop rates and mechanic rewards mid-league in 3.30*, the economic value of a given set of map modifiers is a moving target independent of player behaviour. This argues strongly for short-window training data (rolling 24–72 h) and against pooling a whole league, let alone across leagues.
- No source provides a *historical* archive for PoE 1 rare-map listings, so a rewrite cannot bootstrap from a backfill; it must collect its own data from league start.

### Gaps
- No announced 3.30 end date, so I cannot quantify remaining league life.
- I found no GGG announcement in the archive concerning the trade API or Currency Exchange in 3.30; "Bulk Item Exchange" appeared only as a navigation link, so I cannot confirm whether PoE 1's currency-exchange market scope changed this league.

---

## DATA SOURCES: comparison of the four options

### Takeaway
For **rare maps with explicit modifiers**, direct trade search is the *only* source that is
both queryable by modifier and immediately available; the public stash river is the only
source that gives full modifier data at volume plus the removal events needed to correct
ask-vs-sale bias, but it requires an approved OAuth `service:psapi` application. Community
aggregates (poe.ninja/poe.watch) do **not** price rare maps by modifier at all, and the
official Currency Exchange API carries no items — only currency markets — so its only role
is supplying a real *traded* chaos↔divine rate.

### Cited Findings

**(1) Trade search scraping (current approach)**
- The official developer reference does **not** document `POST /api/trade/search` or `GET /api/trade/fetch` at all — the reference covers `/public-stash-tabs`, currency exchange, and other endpoints, but these two are absent — [pathofexile.com/developer/docs/reference](https://www.pathofexile.com/developer/docs/reference)
- Rate limits are communicated at runtime via headers: `X-Rate-Limit-Policy` (endpoints can share a policy and are limited together), `X-Rate-Limit-Rules` (comma-delimited list of rules such as `ip`, `account`, `client`), `X-Rate-Limit-{rule}` and `X-Rate-Limit-{rule}-State`, plus `Retry-After`. Rule format is `hits:period:restriction` — max hits, period in seconds, restriction in seconds — [developer docs, Rate Limits](https://www.pathofexile.com/developer/docs/index#ratelimits)
- GGG states limits "are dynamic and can change at any time", applications "must parse and follow the rate limit headers", and "exceeding limits frequently can result in application access being revoked" — [developer docs, Rate Limits](https://www.pathofexile.com/developer/docs/index#ratelimits)
- All 4xx responses, **including 429, count as invalid requests**; too many in a short period can restrict the application *and* the user — [developer docs, Rate Limits](https://www.pathofexile.com/developer/docs/index#ratelimits)
- Required User-Agent format: `User-Agent: OAuth {$clientId}/{$version} (contact: {$contact}) ...` — [developer docs, Rate Limits](https://www.pathofexile.com/developer/docs/index#ratelimits)
- Trade searches and exchange queries share the same rate-limit policy — [PoE Trade API Questions thread](https://pathofexile.com/forum/view-thread/2079853/filter-account-type/staff)
- Community-reported: logging in rate-limits by account rather than IP with the same limits, and the IP-based limit allows double the requests per interval — [PoE Trade API Questions thread](https://pathofexile.com/forum/view-thread/2079853/filter-account-type/staff)
- `HEAD` requests can check an endpoint's rate-limit state without counting as a hit — [r/pathofexiledev, understanding X-RateLimit headers](https://lr.in.psf.lt/r/pathofexiledev/comments/1c3bff2/understanding_the_xratelimit_response_headers)
- A developer reports that rate-limit violations caused the endpoint to be blacklisted for all users in 2023, so reading headers beforehand is safer than waiting for 429s — [r/pathofexiledev](https://redlib.hbubli.cc/r/pathofexiledev/comments/12bzroe/whats_the_current_rate_limit_for_searching_for)
- Compliance flag (one line, researched separately): trade-API access has historically been gated by an explicit whitelisting process — [Whitelisting of the Trade API](https://pathofexile.com/forum/view-thread/2036957)

**(2) Public stash tab river `/public-stash-tabs`**
- `GET /public-stash-tabs[/<realm>]`; **required scope `service:psapi`**; realms `xbox` or `sony`, omit for PC; optional `id=` pagination code — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- Response: `next_change_id` (string) plus `stashes[]` of `PublicStashChange`, each with `id`, `public`, `accountName`, `stash`, `stashType`, **`league`**, and `items` — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- Items carry **`explicitMods` and `implicitMods`**, arrays of ItemMod objects with a `description` string and optional `flags`; items also carry `note` and `forum_note`, both documented only as "user-generated text". **There is no `price` field** — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- "There is currently a **5-minute delay** on results using this API" — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- An empty `stashes` array means you have reached the end of the stream; polling again with the same `next_change_id` returns new results as they appear — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- Access requires applying through the official OAuth form with `service:psapi`; browser AJAX is blocked by CORS so a proxy is needed; sending the `deflate` option in the request header "reduces traffic substantially"; a starting change id comes from `https://www.pathofexile.com/api/trade/data/change-ids` — [poedb API:Public Stashes](https://poedb.tw/API%3APublic_Stashes)
- The stream is "a stream containing all public stashes in all leagues for the given realm"; "if you want the most recent data you'll have to start from the end of the river" — [poewiki Stash tab API (via search snippet; page itself is behind an Anubis block)](https://www.poewiki.net/wiki/Stash_tab_API)
- When a stash changes, **the entire stash is re-sent** in the update — [r/pathofexiledev, 2016](https://pol1.lr.ggtyler.dev/r/pathofexiledev/comments/48i4s1/information_on_the_new_stash_tab_api)
- A 2021 community thread estimated trade data at "100+ million items" (total items indexed, not throughput) — [r/pathofexiledev, Oct 2021](https://lr.in.psf.lt/r/pathofexiledev/comments/q46jan/rough_guess_of_the_number_of_items_active_in_the)
- A current reference implementation, **PoE Ledger**, splits this into a `river-crawler` service that crawls the river into a NATS JetStream stream, a `stash-processor` that writes to **ClickHouse** (`ledger.listings`), a NATS KV bucket named `ratelimiter`, a Rust `price-history-api`, and a `web-next` frontend; it is designed to handle "massive influx of changes from league starts" — [mattsre/poeledger.com README](https://github.com/mattsre/poeledger.com/blob/main/README.md)

**(3) Official Currency Exchange API**
- `GET https://web.poecdn.com/api/currency-exchange[/<realm>][/<id>]`; labelled a **Public API with no scope required**; realms `xbox`, `sony`, `poe2`, omit for PoE 1 PC — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- Returns "aggregate trade history across all leagues, grouped into **hourly digests**"; each market seen in the requested hour is included — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- Fields: `next_change_id` (uint, unix timestamp truncated to the hour) and `markets[]` with `league`, `market_id`, `market_pair`, **`volume_traded`**, `lowest_stock`, `highest_stock`, **`lowest_ratio`**, **`highest_ratio`** — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- `id` is a unix timestamp from a previous response; omit it to get the first hour of history. When `next_change_id` equals the timestamp you passed, you are at the current end of the stream and should wait for the next hourly boundary. **Current-hour data is not available**, and old history entries may be removed later — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)

**(4) Community aggregates**
- poe.ninja supported surface is the economy overview endpoints, covering PoE 1 and PoE 2; builds/profiles and other non-economy endpoints are "internal... undocumented, unsupported, and not available for third-party use" and "may change or break at any time"; the docs state "This API exists to run the poe.ninja website, not as a product" — [poe.ninja/docs/api (via search; direct fetch returned HTTP 403)](https://poe.ninja/docs/api)
- Documented endpoint shapes: `https://poe.ninja/api/data/currencyoverview?league=<L>&type=Currency|Fragment` and `https://poe.ninja/api/data/itemoverview?league=<L>&type=...` with types `Oil, Incubator, Scarab, Fossil, Resonator, Essence, DivinationCard, SkillGem, BaseType, HelmetEnchant, UniqueMap, Map, UniqueJewel, UniqueFlask, UniqueWeapon, UniqueArmour, UniqueAccessory, Beast, Vial, DeliriumOrb, Omen, UniqueRelic, ClusterJewel, BlightedMap, BlightRavagedMap, Invitation, Memory, Coffin, AllflameEmber` — [ayberkgezer/poe.ninja-API-Document](https://github.com/ayberkgezer/poe.ninja-API-Document)
- itemoverview fields: `chaosValue`, `exaltedValue`, `divineValue`, `count`, `listingCount`, `sparkline` and `lowConfidenceSparkline` (each with `data[]` and `totalChange`), plus `id, name, icon, detailsId, stackSize, itemClass, baseType, implicitModifiers, explicitModifiers, mapTier, levelRequired, variant`. Currency responses use `pay`/`receive` objects and `chaosEquivalent` instead of `chaosValue` — [ayberkgezer/poe.ninja-API-Document](https://github.com/ayberkgezer/poe.ninja-API-Document)
- **No poe.ninja type covers rare maps filtered by modifier.** `Map` is the nearest type and its documented example has empty `explicitModifiers`; the modifier-bearing example is under `UniqueMap` — [ayberkgezer/poe.ninja-API-Document](https://github.com/ayberkgezer/poe.ninja-API-Document)
- FilterBlade "takes it's economy data straight from poe.ninja"; in that thread a user found poe.watch "shows it as 1.35c which looks to be much closer to what the trade listings are currently sitting at" than poe.ninja, i.e. the two aggregates materially disagree — [NeverSink-Filter issue #253](https://www.github.com/NeverSinkDev/NeverSink-Filter/issues/253)
- poe.watch's own README says it "gathers data over time for various items... from public trade listings" and "finds the average prices", with no statement of median vs trimmed mean vs outlier removal, no update interval and no volume figures — [nibbydev/poewatch](https://github.com/nibbydev/poewatch)
- A third-party npm client `poe-watch-api` exists but documents only request shapes, not pricing — [poe-watch-api README](https://app.unpkg.com/poe-watch-api@0.1.1/files/README.md)

### Inferences
- **Primary source must be trade search.** It is the only one of the four that lets you *query by modifier value* (IQ/IR/Pack Size ranges), which is the entire feature space for an Originator map. The river carries the mods but has no server-side query — you must ingest everything and filter locally. The aggregates do not carry rare-map mods at all.
- **The right supplements are: (a) Currency Exchange API for the numeraire, (b) the river if and only if you get `service:psapi`, (c) poe.ninja for nothing map-specific.** Currency Exchange is the only source in this set that reports *executed* trades (`volume_traded`, `lowest_ratio`/`highest_ratio`) rather than asks, so it is the correct source for the chaos↔divine conversion the tool's output depends on — strictly better than taking divine price from poe.ninja asks. It is also unauthenticated and costs 1 request/hour to stay current.
- **Currency Exchange cannot price maps.** Its unit is a currency `market_pair`; a rare map with rolled mods is not a fungible market. So it contributes exactly one number to the pipeline (the conversion rate) and no item features.
- **The river's unique value is the censoring indicator, not the volume.** Because the whole stash is re-sent on change and the stream is exhaustive, an item vanishing from a public tab, or its `note` changing, is an observable event. That is the only cheap way to get "this ask at this price was/was not taken, and after how long", which is precisely what the ask-vs-sale correction below needs. Trade search snapshots can approximate it only by re-polling the same search and diffing listing ids, which costs search budget.
- **For a single-user desktop tool the river is disproportionate.** It needs an approved OAuth application with `service:psapi`, continuous ingestion (a 5-minute delay plus a serial cursor means you cannot pause and resume without falling behind), all-leagues/all-items firehose decoding to find a handful of maps, and the reference open-source consumer reaches for NATS JetStream plus ClickHouse rather than SQLite. That is a hosted-service architecture. Conversely, if the rewrite is ever hosted, the river becomes the correct primary source and trade search becomes the supplement.
- **poe.ninja/poe.watch disagreement is itself a finding:** since neither publishes its aggregation rule and they produce materially different numbers for the same item, neither can be used as ground truth to validate a map model.

### Gaps
- **No measured throughput figure for the river.** I could not find records/hour, pages/hour, stashes per page, typical page size in bytes, or GB/day, from GGG docs, the wiki, poedb, PoE Ledger, or r/pathofexiledev. The only quantitative anchor is a 2021 community *stock* estimate of "100+ million items". I am deliberately not estimating this; it should be measured directly by crawling for 10 minutes with `deflate` enabled and recording bytes and stash counts.
- **No numeric rate limit is published for `/public-stash-tabs`** — every source says "there is a rate limit" and points at headers. The only concrete figures anywhere in GGG's docs are the *illustrative example* `X-Rate-Limit-Client: 10:5:10`.
- I could not confirm the user's stated "30 searches / 5 minutes" from any primary source. The closest cited figure is a community example of `30:60` on an *account* rule for a different (item) policy. The headers are the authority; treat 30/5min as the user's own measurement.
- **The `note` price-encoding format is undocumented.** GGG describes `note` only as "user-generated text". I could not retrieve a current authoritative description of the `~b/o`/`~price` grammar (the poewiki page is behind an Anubis challenge). Any river consumer has to reverse-engineer and defensively parse it.
- **poe.watch's current operational status in Nov 2026 is unverified.** Its README is undated in what I could read and I found no 2026 activity confirmation.
- poe.ninja's docs page returns HTTP 403 to automated fetches, so all poe.ninja facts above are second-hand (search snippets and a community mirror of the doc) rather than read directly.

---

## PRICING METHODOLOGY: what current tools actually do

### Takeaway
No currently-verifiable PoE pricing tool publishes a learned-model methodology. The surviving
mainstream price checker (Awakened PoE Trade) is a **pure comparables/live-lookup tool with
manually chosen filters and no stated confidence measure**, and the one well-known ML price
predictor, **poeprices.info, appears to be dead** — its domain now redirects to a GoDaddy
"for sale" parking page.

### Cited Findings
- **poeprices.info is offline/for sale:** fetching `https://www.poeprices.info/` returns HTTP 307 redirecting to `https://forsale.godaddy.com/forsale/www.poeprices.info?...&traffic_type=TDFS_DASLNC...` — observed 2026-11-22 via WebFetch of [poeprices.info](https://www.poeprices.info/)
- Before that, poeprices.info ran a "Rare item AI price prediction" feature distinct from its unique/gem/div-card pricing; players reported it failing independently of the rest of the site ("Rare item AI price prediction doesn't seem to be working at the moment... Rest of the site (unique items, gems, div cards, etc.) works fine") — [Poe Price Check forum thread](https://www.pathofexile.com/forum/view-thread/1216141/page/212)
- PoE Overlay used "an inbuilt integration of your price prediction AI", i.e. the prediction was consumed by third-party tools — [Poe Price Check forum thread](https://www.pathofexile.com/forum/view-thread/1216141/page/204)
- Players judged poeprices "a more sophisticated and easier to use method to pricecheck with than poe.trade offers", while others said "a lot struggle with the minimum maximum range and checkboxes" — [Poe Price Check forum thread](https://www.pathofexile.com/forum/view-thread/1216141/page/204)
- **Awakened PoE Trade is explicitly not a model:** "The app does not magically check the price of your item." The user ticks the stats and filters. Filter ranges are auto-filled "slightly below the item's actual roll so more listings match", and related mods can be merged into "pseudo" stats (e.g. combining several spell-damage mods into one total) — [Awakened PoE Trade quick-start](https://snosme.github.io/awakened-poe-trade/quick-start)
- The quick-start page states nothing about how results are summarised (no median/range statement), nothing about rate limits, and makes **no statement about accuracy or confidence** — [Awakened PoE Trade quick-start](https://snosme.github.io/awakened-poe-trade/quick-start)
- Awakened PoE Trade credits poe.ninja, poeprices.info and RePoE among its upstream projects — [Awakened PoE Trade quick-start](https://snosme.github.io/awakened-poe-trade/quick-start)
- A third-party guide describes the tool as querying "the official trade site using real listings and filters (like item mods, quality, links, etc.) to give you the most accurate median pricing based on live data" — [ssegold guide, 2026](https://www.ssegold.com/awakened-poe-trade-3-26-price-check-tool-guide) (secondary source; the official docs do not make this claim)
- Recommended manual workflow in community guides: remove filters for minor stats until enough results appear, then compare several listings — [ssegold guide](https://www.ssegold.com/awakened-poe-trade-3-26-price-check-tool-guide)
- **A k-NN approach has been tried and published:** `licoffe/POE-kNN` prices items with k-nearest neighbours; the author warns it "should not be regarded as a finished product of any kind" — [licoffe/POE-kNN](https://github.com/licoffe/POE-kNN)
- Other open-source PoE economy work exists but documents pipelines rather than valuation algorithms: `produdez/poe-economy` — [GitHub](https://github.com/produdez/poe-economy); `mattsre/poeledger.com` (ClickHouse price history, aggregation SQL in `stash-processor/sql`, not described in the README) — [GitHub](https://github.com/mattsre/poeledger.com/blob/main/README.md)
- A PoE trade macro reportedly used trimmed mean and median — [NeverSink-Filter issue #253 thread context](https://www.github.com/NeverSinkDev/NeverSink-Filter/issues/253)
- Comparable methodology from an adjacent game economy that *does* publish: SteamAnalyst documents its CS2/CS:GO skin price derivation — [SteamAnalyst methodology](https://www.steamanalyst.com/zh/methodology); WoW's TradeSkillMaster documents "market value" as a published formula over auction scans — [TSM AuctionDB market value](https://support.tradeskillmaster.com/auctiondb-market-value); EVE Online community threads discuss trimmed-mean/percentile approaches to a "sane" market price — [EVE forums](https://forums.eveonline.com/t/what-math-do-you-use-for-a-sane-market-price/311684)

### Inferences
- The state of the art in *shipped* PoE tooling is live comparables with human-in-the-loop filter relaxation. The learned-model niche was occupied by a single site that has now disappeared, which means (a) there is a genuine gap the rewrite could fill, and (b) there is no published benchmark to beat, so the rewrite must create its own holdout evaluation.
- poeprices.info's death is a practical dependency warning: Awakened PoE Trade credits it, and PoE Overlay embedded it. Any rewrite should not depend on a third-party prediction service.
- The two patterns Awakened PoE Trade uses are worth copying regardless of model choice: **pseudo-mod aggregation** (collapse correlated mods into a single total before they become features) and **asymmetric filter widening** (bound the search slightly *below* the item's roll, so comparables are weakly worse than the item and the resulting price is a conservative floor).
- TSM's publicly-documented scan-based "market value" is the closest healthy analogue to what this tool should expose: a transparent, reproducible formula over observed listings, with the formula published, rather than an opaque score.

### Gaps
- Neither poe.ninja nor poe.watch documents its aggregation formula; I found no primary statement of outlier filtering, trimming, median vs mean, or what `lowConfidence` thresholds mean. A PoE 2 guide's claim that poe.ninja "smooths artificial spikes" is a third-party characterisation, not documentation — [mobalytics](https://mobalytics.gg/poe-2/guides/poe2-price-check).
- poe.ninja does not define the difference between `count` and `listingCount` in the community mirror of its docs.
- I found **no published paper, thesis or blog post specifically on PoE price prediction** with reported error metrics. The only artefacts are unmaintained repos (POE-kNN) and dead services (poeprices.info). This is a real gap, not a search failure on one query — three differently-phrased searches surfaced only patents on unrelated retail pricing.

---

## Is a three-library GBM ensemble over-engineered for ~7 numeric features?

### Takeaway
Yes. CatBoost + LightGBM + XGBoost are three implementations of the *same* hypothesis class;
on 7 numeric features and ~2000 rows they produce highly correlated errors, so the ensemble
buys very little variance reduction while tripling training time, dependency weight and
failure surface. The defensible replacement is **one** gradient-boosted model trained with a
**quantile objective at three quantiles**, calibrated by conformalised quantile regression, on
top of a live-comparables path.

### Cited Findings
- Plain quantile boosting is not calibrated: a practitioner source claims "vanilla quantile XGBoost intervals undercover. Without CQR your '90% interval' is probably a 70-75% interval" — [skills.sh regression notes](https://skills.sh/brojonat/llmsrules/regression) (blog-grade source; treat the specific numbers as anecdotal)
- Conformalised Quantile Regression fits lower and upper quantile models and then calibrates them on held-out data, yielding **finite-sample, distribution-free marginal coverage** — [Romano, Patterson & Candès, "Conformalized Quantile Regression"](https://arxiv.org/pdf/1905.03222)
- Classical split conformal uses a single global residual quantile, producing intervals that are too wide in low-noise regions and insufficiently adaptive under heteroscedasticity — motivation for CQR and local variants — [Angelopoulos & Bates, "A Gentle Introduction to Conformal Prediction"](https://arxiv.org/pdf/2107.07511)
- In a comparison of conformal quantile methods, the Romano et al. (2019) CQR method "typically yields tighter prediction intervals in finite samples" — [Sesia & Candès, "A comparison of some conformal quantile regression methods"](https://arxiv.org/pdf/1909.05433)
- Boosted Conformal Prediction (NeurIPS 2024) uses gradient boosting to improve a conformity score such as CQR's, to reduce average interval length or improve conditional coverage while maintaining marginal coverage — [NeurIPS 2024 paper text](https://object.cloud.sdsc.edu/v1/AUTH_da4962d3368042ac8337e2dfdd3e7bf3/ml-papers-txt/NeurIPS/2024/boosted_conformal_prediction_intervals__379933ae.txt)
- LoBoost (arXiv, Feb 2026) proposes model-native *local* conformal intervals computed from the boosted trees themselves, arguing existing boosting uncertainty methods are "model-native but not distribution-free" — [arXiv 2602.22432](https://arxiv.org/html/2602.22432v1) (recent preprint, not independently validated)
- A method evaluated on 11 real tabular datasets reports "consistently produces narrower prediction intervals while maintaining the desired coverage probability", and notes GBTs can be calibrated with conformal quantile regression — [arXiv 2405.18176v4](https://web3.arxiv.org/pdf/2405.18176v4)
- k-NN has been attempted for PoE item pricing and abandoned as unfinished — [licoffe/POE-kNN](https://github.com/licoffe/POE-kNN)

### Inferences
- What the ensemble actually gives up by being collapsed to one model: almost nothing on point accuracy, since all three are axis-aligned boosted trees over the same 7 features. What it gives up *materially* is the illusion of robustness — three correlated learners averaged together do not fix the real error sources here, which are (i) asking-price bias, (ii) league-start drift, and (iii) small-sample coverage in sparse modifier regions.
- What the ensemble *costs* concretely: three native-dependency wheels in a desktop app, three sets of hyperparameters, triple retraining latency, and no interval estimate at all unless each model is additionally trained per quantile (9 fits instead of 3).
- Where a learned model genuinely beats live comparable lookup: (a) **sparse regions** — a map whose exact IQ/IR/Pack-Size combination has zero or one live listing, where a comparables query returns nothing actionable no matter how it is widened; (b) **request cost** — one cached model answers an entire stash tab for zero requests; (c) **monotone structure** — a learned model can be constrained so that more Item Quantity never lowers the predicted price, whereas a handful of noisy comparables frequently violates that and produces nonsense orderings.
- Where it loses: (a) **regime changes** — a mid-league nerf to a mechanic's rewards (which GGG is doing right now in 3.30) instantly invalidates the mapping from mods to price, and comparables track it within minutes while a model trained yesterday does not; (b) **level shifts in the numeraire** — divine-orb drift re-prices everything multiplicatively; (c) **thin-tail items** — the single top-1% roll is priced by one seller's whim, which no regression recovers; (d) **explainability** — a seller can verify "here are the five listings I used", not "the model said 240c".
- Therefore the correct architecture is **neither/both**: comparables when the live market is dense enough, model when it is not, with an explicit, logged switch rule. That is the single most important structural recommendation in these notes.
- Monotonic constraints (supported natively by LightGBM/XGBoost/CatBoost) are the cheapest accuracy-and-trust win available with 7 features and are unavailable to a k-NN or kernel approach.

### Gaps
- I found no benchmark comparing GBM ensembles against single GBMs specifically on small-n, low-dimensional tabular regression, so the "ensemble buys little" claim is an inference from the fact that the three libraries share a hypothesis class, not a cited measurement. The rewrite should settle it empirically: it costs one experiment on existing data to compare a 3-model ensemble against one LightGBM by cross-validated MAE on log-price.
- No head-to-head benchmark of GBM quantile regression vs conformal intervals on tabular data was found; the literature indicates CQR is the standard way to obtain coverage guarantees but which variant is tightest is dataset-dependent.

---

## Asking prices vs sale prices: the censoring problem

### Takeaway
Listings are **right-censored observations of the seller's reservation price**, and the
mainstream statistical treatment is survival/censored-quantile regression with time-on-market
as the response. The literature is unambiguous that dropping unsold listings biases the model,
and the most directly transferable result is that the **ratio of actual ask to model-predicted
price predicts time-to-sale better than the raw ask does** — which maps exactly onto the output
this tool should produce.

### Cited Findings
- Opendoor's data science team frames unsold homes as censored observations and states that removing them from training data "introduces significant bias"; they use survival analysis with days-on-market as the target — [Opendoor, liquidity modeling using survival analysis](https://www.opendoor.com/w/blog/liquidity-modeling-real-estate-survival-analysis)
- A used-car-marketplace study uses a three-step pipeline — ML price prediction, then survival analysis of time to sale — and finds **the ratio of actual to predicted offer price predicts listing duration better than the raw asking price** — [survival-analysis preprint](https://real.mtak.hu/247298/1/epj_survival.pdf)
- Censored quantile regression has been applied to time-on-market in German cities, positioned as an extension of survival regression — [Cajias 2020, Journal of Business Economics](https://epub.uni-regensburg.de/44755/1/Cajias2020_Article_ExploringTheDeterminantsOfReal.pdf)
- A time-on-market index must include censored listings; using only completed sales yields a "much lower and less accurate median" — [Carrillo 2017 working paper](https://belkcollegeofbusiness.charlotte.edu/ichiang1/wp-content/uploads/sites/863/2014/09/2017S_Carrillo.pdf)
- Asking prices systematically exceed sale prices, largely because listing and transaction datasets differ in composition; unsold listings tend to carry **high asks with low transaction probability** — [Empirical Economics 2020](https://link.springer.com/content/pdf/10.1007/s00181-020-01992-3.pdf)
- The public stash API returns only "the current state of stash tabs" and holds no history, so disappearance from the stream is the only signal of removal — [poedb API:Public Stashes](https://poedb.tw/API%3APublic_Stashes)
- Items are re-sent whole when the stash changes, so a `note` edit is observable as a changed price on an unchanged item id — [r/pathofexiledev](https://pol1.lr.ggtyler.dev/r/pathofexiledev/comments/48i4s1/information_on_the_new_stash_tab_api)
- The Currency Exchange API is the one official source reporting executed volume (`volume_traded`) and realised ratio bounds (`lowest_ratio`, `highest_ratio`) rather than asks — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)

### Inferences
- Concretely for this tool: a single trade-search snapshot of asks is a sample of **the upper envelope** of the price distribution, skewed upward by exactly the listings that are not selling. Fitting a mean regression to it estimates "what sellers hope for", not "what the map is worth". The *low quantiles* of the ask distribution are much closer to a transactable price than the mean or median, because the cheapest asks are the ones that clear.
- The cheapest usable censoring correction without the river: **re-poll the same saved search on a schedule and diff listing ids.** A listing that disappears within minutes of a price cut is a near-sale; one that persists for days at a high price is censored at that price. This converts the existing 30-search/5-min budget into survival data at the cost of a few searches per hour, and needs no `service:psapi`.
- If the tool records listing age (trade fetch results expose an indexed/listed timestamp per listing in practice), **the age distribution at each price level is free censoring information inside a single search** — old-and-expensive is censored, young-and-cheap is about to transact. I could not verify the field name from GGG documentation because the trade endpoints are undocumented, so this must be confirmed against a live response.
- The transferable output design from the used-car result: emit **two numbers plus a ratio** — a "market value" (median of the fitted distribution) and a "sells within N hours" price (a low quantile) — and show the user ask/predicted as the liquidity dial. This is substantively more useful for a stash-pricing workflow than a single "fair value" and is directly supported by the cited finding.
- Practical modelling route in order of effort: (1) fit on **log price**, predict the **0.25 quantile** rather than the mean, and call it the transactable price — captures most of the bias correction with a one-line change; (2) add Tobit/censored-quantile regression once removal events are being recorded; (3) full Cox/AFT time-to-sale model only if the river is available.

### Gaps
- I found no PoE-specific or game-economy-specific published treatment of ask-vs-sale bias with numbers. All quantitative evidence here is from real-estate and used-car markets, where listing durations are weeks/months rather than hours; the direction of the bias transfers, the magnitude almost certainly does not.
- No source quantifies what fraction of PoE trade listings ever sell, or the median time-to-sale for rare maps. Without this the censoring rate is unknown, which matters because censored-regression corrections are only worth their complexity above moderate censoring rates.

---

## Uncertainty estimation and communicating confidence

### Takeaway
Report an interval, not a point, and obtain it from quantile regression calibrated by
conformalised quantile regression so the stated coverage is actually achieved; drive the
displayed confidence off the **number and recency of comparables**, not off model variance.

### Cited Findings
- CQR gives finite-sample, distribution-free marginal coverage by calibrating fitted quantiles on a held-out set — [Romano et al.](https://arxiv.org/pdf/1905.03222)
- Uncalibrated quantile-GBM intervals undercover substantially — [practitioner source](https://skills.sh/brojonat/llmsrules/regression) (anecdotal)
- Split conformal's single global residual quantile is inadequate under heteroscedasticity — [Angelopoulos & Bates](https://arxiv.org/pdf/2107.07511)
- poe.ninja itself publishes a confidence signal alongside its prices: a `lowConfidenceSparkline` in parallel with `sparkline`, plus `count` and `listingCount` — [poe.ninja API doc mirror](https://github.com/ayberkgezer/poe.ninja-API-Document)
- Awakened PoE Trade makes no accuracy or confidence statement at all — [quick-start](https://snosme.github.io/awakened-poe-trade/quick-start)

### Inferences
- Map-price noise is strongly heteroscedastic — a common mid-roll map has many comparables and a tight spread, a top-roll Originator has one or none — so a single global error bar is the wrong design and split conformal is specifically contraindicated by the Angelopoulos & Bates point. Use CQR or a locally-adaptive variant.
- Calibration must be **out-of-time, not random-split**: with a 9-day-old league and active balance patches, a random K-fold split leaks future prices into training and will report coverage that the tool does not achieve in use. Hold out the most recent 12–24 h as the conformal calibration set.
- Practical display: three numbers and a reason. "Sells fast ≈ X (q0.25) · Market ≈ Y (q0.50) · Patient ≈ Z (q0.90), from N comparables, newest 14 min old." The N and the age are what the user can actually audit, and poe.ninja's own choice to ship a low-confidence flag keyed to observation counts supports making comparable-count the confidence driver.
- Refuse to answer rather than extrapolate: below some comparable count (the rewrite should pick it from its own holdout, e.g. N < 5) the honest output is a wide interval explicitly labelled as a model extrapolation.

### Gaps
- poe.ninja does not document the threshold at which it marks a price low-confidence, so I cannot cite a precedent value for N.

---

## Efficiency: caching, incremental collection and query design under the search budget

### Takeaway
Treat search requests as the scarce resource and spend them on **population-level searches that
amortise across many items**, not on per-item lookups; answer from cache by default and spend a
live query only when the cached comparable set is thin or stale. Under the user's measured
30 searches / 5 minutes, per-item live comparables cap the tool at roughly 50–90 items/hour,
whereas a cached model prices an unlimited stash instantly.

### Cited Findings
- Rule format `hits:period:restriction` and per-rule state are returned in headers on every response; applications must parse and follow them; limits are dynamic — [developer docs, Rate Limits](https://www.pathofexile.com/developer/docs/index#ratelimits)
- 429s and all other 4xx count as invalid requests and repeated offences can restrict both the application and the user — [developer docs, Rate Limits](https://www.pathofexile.com/developer/docs/index#ratelimits)
- `HEAD` requests can read an endpoint's rate-limit state **without consuming a hit** — [r/pathofexiledev](https://lr.in.psf.lt/r/pathofexiledev/comments/1c3bff2/understanding_the_xratelimit_response_headers)
- Trade searches and exchange queries share one rate-limit policy, so they compete for the same budget — [PoE Trade API Questions](https://pathofexile.com/forum/view-thread/2079853/filter-account-type/staff)
- Community report: authenticated requests are limited per account at the same limits, while IP-based limiting "allows double the requests per interval" — [PoE Trade API Questions](https://pathofexile.com/forum/view-thread/2079853/filter-account-type/staff)
- Violations have previously caused endpoint blacklisting affecting all users — [r/pathofexiledev](https://redlib.hbubli.cc/r/pathofexiledev/comments/12bzroe/whats_the_current_rate_limit_for_searching_for)
- Awakened PoE Trade's own tactic is to auto-fill each filter range **slightly below the item's roll** so more listings match on the first try — [quick-start](https://snosme.github.io/awakened-poe-trade/quick-start)
- The public stash stream carries a **5-minute delay**, and the Currency Exchange stream publishes **hourly** with no current-hour data — [developer docs reference](https://www.pathofexile.com/developer/docs/reference)
- `deflate` in the request header "reduces traffic substantially" on the stash API — [poedb API:Public Stashes](https://poedb.tw/API%3APublic_Stashes)

### Inferences (arithmetic from the user's measured 30/5min budget)
- **Per-item live comparables: 4–7 requests/item → 4.3–7.5 items per 5-minute window → ~52–90 items/hour.** A 144-slot quad tab of maps therefore takes ~1.6–2.8 hours to price item-by-item. This is the fundamental reason per-item lookup cannot be the default path for a stash-pricing tool.
- **Population search amortises far better.** One broad search ("Originator maps, this league, any mods, sorted by price") plus its fetch pages yields on the order of 100 listings for ~1 search hit. Rebuilding a 2000-listing training set is then ~20 searches ≈ 3.5 minutes of search budget (plus fetch calls under their own policy), i.e. the entire training set is refreshable several times per hour. **Spending the budget on refreshing a shared comparable pool rather than on individual items is a 10–20× efficiency gain for a full-tab workflow.**
- Query-design rules that cut requests directly: (a) **stratify, don't widen** — issue a small fixed set of searches partitioned by map tier and coarse IQ band, cached per partition, rather than a bespoke widening ladder per item; the 4–7-request widening cost disappears because the partition is already populated; (b) **sort by price ascending and read only the first fetch page** — the low quantiles are what you need for a transactable price, and the expensive tail adds requests while adding bias; (c) **de-duplicate searches by filter hash** so two similar maps in the same tab share one query.
- Cache policy keyed to the two published delays: there is no point refreshing a comparable partition faster than the market updates it, and nothing in this stack is fresher than 5 minutes anyway. A **5–10 minute TTL per partition** matches the river's documented delay; the chaos↔divine rate from Currency Exchange needs **one request per hour** because that endpoint publishes hourly and withholds the current hour.
- The decision rule between cache and live query should be mechanical and logged: query live **only if** (comparables in partition < N_min) **or** (newest comparable older than TTL) **or** (the item's feature vector sits outside the convex range of the cached partition, i.e. a genuine top-roll). Otherwise answer from cache at zero cost. Everything else — including every ordinary map in a tab — is a cache hit.
- Use `HEAD` as a free pre-flight before any burst, since it reads limit state without consuming a hit; combined with reading `X-Rate-Limit-{rule}-State` from every response this makes a 429 avoidable rather than expected. Given that 429s count as invalid requests and have historically triggered blacklisting, a **token-bucket scheduler seeded from the live headers (not from a hardcoded 30/5min) is mandatory**, and the tool should never treat a 429 as a normal retry signal.
- Because search and exchange share a policy, a bulk-exchange feature would eat the same budget as price checking — budget them as one pool.
- Storage is a non-issue on the trade-search path (2000 listings of 7 features plus raw JSON is single-digit MB) and is the dominant cost on the river path, where the reference implementation reaches for ClickHouse. This asymmetry, more than request cost, is what makes the river a hosted-service choice.

### Gaps
- I could not verify the 30/5min figure or obtain any official per-endpoint number, so all arithmetic above is conditional on the user's own measurement. The limit must be read from headers at runtime.
- No published figure for `/api/trade/fetch` batch size or its separate rate-limit policy; the commonly used batch of 10 ids per fetch call is not documented by GGG in the reference I could read, so "~100 listings per search" should be re-measured rather than assumed.
- No measured bandwidth or storage-per-day figure for the river (see earlier gap), so I cannot quantify the storage asymmetry beyond "single-digit MB vs a ClickHouse deployment".
