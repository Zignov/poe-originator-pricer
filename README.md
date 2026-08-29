# PoE Originator Map Stash Pricer

A local tool for Path of Exile 3.29 that prices **T16 Originator Maps** from their
reward values, and prices a whole stash tab at once so you can decide what to
list and for how much without checking each map by hand.

It is an analysis tool. It never touches the game client, never modifies your
stash, and never performs a gameplay action.

**[Download the latest release](https://github.com/Zignov/poe-originator-pricer/releases/latest)**,
extract it, and double-click `Start Originator Pricer.bat`. It ships with a
trained model, so the first stash tab you paste already gets prices. Needs
Python 3.11+ installed; §6 covers what the launcher does and why there is no
frozen `.exe`.

---

## 1. What it does

```
Connect or import your stash, or read a character's inventory
        v
Find every Originator Map in it
        v
Read its seven reward values
        v
Estimate a fair value, a suggested listing price and a likely range
        v
Show the closest real listings as a cross-check
        v
Draw the prices onto a picture of your stash grid
        v
...or onto the real stash tab (or inventory), with a transparent desktop overlay
        v
...and re-priced from the overlay itself, with one chord, between maps
```

You get a sortable table, a visual stash grid with the price written on each
map's actual position, per-map explanations of where the number came from, and
an optional click-through overlay that puts the same tiles on top of the game
window itself (section 7).

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

By default the model prices **rare, uncorrupted Tier 16** Originator Maps.
Anything else — magic or normal rarity, corrupted, wrong tier, not an
Originator, no rewards rolled — is still shown in the table and grid, but marked
`Unsupported` with the reason, rather than given a questionable price.

**Rare** is not a preference, it is the scope. A magic map holds one prefix and
one suffix where a rare holds three of each, so it carries a fraction of the
reward values and sells for a fraction of the price: in a 4,746-row sample the
magic listings averaged 18c against a rare market that starts around 30c.
Trained together they teach the model that low reward totals are normal, and the
exalt verdict — which assumes the rare 3+3 ceiling — is simply wrong about them.
The rule is applied in three places, because each leaks differently: the trade
search asks GGG for `rarity: rare` so nothing new arrives, `classify()` refuses
to price one already in hand, and the outlier pass flags rows collected before
the rule existed so they leave training without being deleted.

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

### Two ways to run it

**Active** is the manual button on the Market Data page: pick a size, press
Collect, watch the progress bar. It runs on a worker thread, so **Cancel is a
real button** rather than a decoration — the flag is checked inside the
rate-limit wait, not after it, which matters because GGG's search policy can
impose a half-hour penalty and a cancel that has to wait one out is not a
cancel. Everything collected before the stop is already committed; ingest
flushes every fifty listings. A separate **Retrain on stored data** button then
trains on whatever landed, including a cancelled run's partial haul, without
calling the API at all.

**Passive** is a toggle that collects on a loop for as long as the app is open:
one scan of ~400 maps (about 56 requests), then a quiet period long enough for
the rate limit to reset, then another. Scan size, cooldown, an optional
switch-off timer and automatic retraining are all configurable.

Two things worth being explicit about:

- **The cooldown is politeness, not safety.** The rate limiter obeys GGG's own
  response headers unconditionally and would allow this far more often. The
  cooldown is there so the traffic looks like what it is.
- **It stops when you close the app, and never restarts itself.** The switch is
  deliberately not remembered between launches, even though the settings are. A
  background collector running because a config file said so — while you believe
  it is stopped — is exactly what gets a User-Agent blocked.

The two modes share one lock, so they never read the market at the same time.
Two concurrent collections would be two rate limiters neither of which can see
the other's requests, each individually convinced it is obeying a policy they
are together doubling.

### Not paying twice for the same listing

A search hands back 100 ids for one request; a fetch costs one request per ten.
The fetch is therefore **ten times dearer per listing**, and it used to be spent
re-reading rows already in the database — measured over a six-cycle run, 61% of
every fetch request came back with something already stored.

Two filters now run before anything is fetched: ids already taken this run
(every slice sorts newest-first, so a map matching two slices sat near the top
of both), and ids stored and seen inside `market.refetch_after_hours`. That
second one is a window rather than "already stored" on purpose — a seller who
marks a map down is publishing real information, and an id never re-read would
freeze at its first asking price.

Measured on a 150-listing scan afterwards: 508 repeats skipped before they cost
anything, and **97% of what was fetched was new, against 26% before.**

That also changes what paces passive mode. The useful gap between scans is not
set by the rate limit — the limiter already obeys GGG's headers — but by how
fast the market turns over, which measured at roughly 190 new listings every 18
minutes. So the gap is a controller rather than a constant: it starts at
`cooldown_minutes`, stretches when a scan comes back mostly repeats, and pulls
back in when it does not, up to `max_cooldown_minutes`. The signal is the share
of *search results* that were new — never the share of fetched rows, which now
that repeats are filtered would read 100% for ever and never back off.

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
Cookies, and copy `POESESSID` into **Settings → Account**, along with your
account name including its `#1234` discriminator.

**Save to .env** keeps it for next time; without it the value lasts only as long
as the app is open. **Forget** clears it again. Saving edits `.env` in place, so
your contact address and anything else in that file survives untouched, and the
value is picked up immediately rather than at the next restart.

Treat it like a password. `.env` is gitignored, never goes into a release zip,
and never leaves your machine — but it is plain text, so saving makes the cookie
*persist*, not become secret. It expires on its own; when GGG starts answering
403, paste a fresh one.

---

## 6. Quick start

### Download and double-click (Windows)

Grab the zip from the [latest release](https://github.com/Zignov/poe-originator-pricer/releases/latest),
extract it anywhere you like — Desktop is fine, `C:\Program Files` is not —
and double-click:

```
Start Originator Pricer.bat
```

That is the whole install. The first run creates a private Python environment
inside the folder and downloads the dependencies, which takes a few minutes and
a few hundred MB; every run after that starts in seconds. When the server is up
it opens your browser at `http://localhost:8501` by itself.

Starting it from a terminal rather than by double-clicking needs the `.\`
prefix — `call ".\Start Originator Pricer.bat"`. Without it `cmd` reads the
quoted name as its own `start` builtin and reports a file it cannot find.

It needs **Python 3.11 or newer** on the machine — from
[python.org](https://www.python.org/downloads/windows/), with *Add python.exe to
PATH* ticked in the installer. The launcher says so and stops if it cannot find
one. There is no frozen `.exe`: this app is a Streamlit server plus three
gradient-boosting backends, which freezes to roughly 800 MB and breaks on every
dependency bump, so a venv that `pip` can repair is the more honest package.

The download ships with a **trained model already in it**, so prices work on the
first stash tab you paste — no collection run needed to see something. The 19 MB
listings database is not in the zip, but the rows that model was fitted on are:
the first run imports `data/originator_listings_allflame.csv` into a fresh
database, prints a line saying so, and does not do it again. That is what makes
your first **Collect and retrain** add to **15,691 listings** of history rather
than start over from the few hundred it just pulled — which used to mean trading
the bundled model for a much thinner one without being told.

Three consequences worth knowing:

- **An upgrade brings the newer market with it.** The import is keyed to the
  contents of the bundled file, not to the database being empty, so installing a
  release that ships more listings loads them. Only rows the previous bundle put
  there are replaced; anything you collected or imported yourself is untouched,
  which is why those rows carry a source of their own (`bundled_sample`).
- **It is one league's market** (`Allflame`). Set a different league in
  `config.yaml` before the first run and the import is skipped rather than filed
  under the wrong league's name — you start empty and collect.
- **Retraining on much less data says so.** If a retrain would replace a model
  fitted on more than twice the listings it is about to use, the training log
  warns before overwriting it.

Everything the launcher creates — `.venv`, `config.yaml`, `.env`, the database —
stays inside that one folder. Deleting the folder uninstalls it completely.

On macOS or Linux the equivalent is `./start.sh`. The web app is portable; the
desktop overlay in §7 is Windows-only.

### From a clone

```bash
git clone <this repo>
cd poe-originator-pricer

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp config.example.yaml config.yaml   # set your league and contact address
cp .env.example .env                 # optional: POESESSID for stash access
```

A trained model is already in the checkout — `models/originator_model.pkl` is
tracked (§11) — so `streamlit run app.py` prices maps right now. What is *not*
in the checkout is the listings database, so the Market Data and Model Analysis
pages stay empty until you give them one:

```bash
python -m src.cli update-rate                 # live divine/chaos rate
python -m src.cli collect-market --limit 2000 # ~15-20 min, rate-limit obedient
python -m src.cli train --tune-weights        # only once you have your own data
python -m src.cli status
```

Retraining is worth doing when the shipped model is from a league or a week
that no longer matches the market you are selling into — §11 on why a snapshot
ages. Until then the shipped one is the better of the two.

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

* **The database is not in the repo** (`data/*.db`), and Community Cloud's disk
  does not persist across restarts. What travels instead is the pair that
  rebuilds a usable one: `models/originator_model.pkl` is tracked (§11), so a
  cloud instance prices maps from the first request, and
  `data/originator_listings_allflame.csv` is seeded into the empty database on
  each cold start, so the Market Data and Model Analysis pages have the market
  that model was fitted on to show. Anything *you* collect there still dies with
  the container: treat a cloud instance as a demo, and keep the real dataset on
  the machine that collected it.
* **`POE_CONTACT_EMAIL` must be set as a secret.** Without it the User-Agent
  goes out as `you@example.com`, and GGG asks automated clients to identify
  themselves with a real address (see §14). `POE_LEAGUE` can be set the same way
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
  take one at all), colour-banded by value across six bands (percentiles of *this tab*, not
  hardcoded league thresholds) with anything under 30c greyed out on a fixed
  threshold so junk recedes instead of competing for attention, with full details on hover, and a **confidence meter**
  along the bottom edge of each tile — a short bar means the number above it is
  a rough estimate rather than a price, which is otherwise buried in a tooltip
  on the one tile you did not think to hover — and **one white dot per free
  affix slot** in the top-right corner, because how many slots are left decides
  whether an orb can touch the map at all and used to be reachable only by
  opening it. No dots means no slots left; a map whose affixes could not be read
  shows nothing rather than a misleading zero;
- a **sortable table** — by value, by any reward, by pack size, by confidence, or
  by stash position;
- **filters** — minimum value, high confidence only, or only maps carrying a
  particular reward;
- per-map **fair value, suggested listing price, likely range and confidence**;
- the **closest real listings**, as an independent cross-check on the model;
- **copy-ready notes** (`~price 9.8 divine`) and a copy-all block, for you to
  type in yourself;
- a **stash-search pattern** that highlights the maps above a price you pick —
  see below;
- a **Pricing mode** selector — the trained model, or a live market comparison
  against the maps actually listed right now. See §8.

### Pricing what is in your inventory

The same page reads a character's backpack as well as a stash tab: pick
**Character inventory** in the source row at the top of the page, load the
character list, choose a character, and press **Read inventory**.

That source row is always on screen, including once a tab is already loaded.
It used to fold away with the source-specific settings, which meant the only
source you could see was the one you were already using -- and the inventory
looked like a feature with no button, because nothing on the page said it
existed. Only the settings for the chosen source fold now.

It exists because of when you actually want the number. Maps come out of the map
device into your inventory, and that — before they are dumped into a tab and
forgotten — is the moment you want to know which of them is worth listing. Going
"put them in a tab, re-read the tab, come back" is enough friction that in
practice you stop checking.

It uses the same POESESSID session as the stash reader, one endpoint over
(`/character-window/get-items`), and everything downstream — the grid, the
table, the filters, the exalt verdicts, the highlight pattern, the overlay — is
identical, because an inventory read produces exactly the same normalized maps a
tab read does.

Two things are specific to it:

- **Only the 12x5 backpack is read.** A character's items response also contains
  everything equipped, the flasks, the jewels in the tree and whatever is in the
  map device, and those report `x`/`y` *inside their own slot* — an equipped
  helmet is at `0, 0`. Drawing those on the inventory grid would put a price on
  a cell holding something else, so they are dropped.
- **The character list is filtered to the league you are pricing.** GGG returns
  every character on the account, standard and hardcore and three years of dead
  leagues, and a thirty-entry picker in which the wrong pick silently prices
  nothing is worse than a two-entry one.

If the character list comes back empty, check the league in Settings and that
your characters are not hidden by your privacy settings on pathofexile.com —
GGG returns nothing for a hidden profile.

### Highlighting the good maps in game

Pick a price, copy the pattern, paste it into the stash search box (**Ctrl+F**),
and every map at or above that price lights up. The workflow is: price the tab,
set the threshold, paste, pull the highlighted maps.

The game cannot see this app's prices, so the threshold has to be re-expressed
as something the stash search *can* match — the text printed on the maps
themselves. Rare maps carry a random two-word name, so the pattern is built from
whatever fragments of those names pick out the maps above the price and nothing
else in the tab:

```
ort|glo|gri|est|tim|fat|^voi|ago.*gam
```

That is a real pattern for eight maps out of a 26-map tab, in 37 characters.
Three things about it are deliberate, and all three come from how the game's
search actually behaves:

- **It is fragments, not names.** The search box holds **250 characters** in
  PoE 1, which does not go far across a quad tab, so each map is reduced to the
  shortest piece of its name that no *other* map in the tab carries. A fragment
  that happens to cover several selected maps at once is preferred.
- **`^voi` is anchored.** The search matches **each line** of the item text, so
  `^` means "the name starts with voi" — which separates names that share a word
  when no plain substring can.
- **`ago.*gam` spans two words without a space.** A space in the search box
  means *and*, not a literal space, so a fragment containing one would quietly
  become a much wider search. `.` never matches a newline, so `.*` stays inside
  the one line.

When a map cannot be singled out — two maps in a tab can roll the same name, one
above the threshold and one below — the app says so and gives you the stash
position instead of emitting a pattern that silently highlights both. The same
goes for maps that will not fit in the character budget.

It is text for you to paste. Nothing is sent to the game client.

From the CLI:

```bash
python -m src.cli inspect-stash
python -m src.cli price-tab --tab "Originator Maps" --comparables --copy-all
python -m src.cli price-tab --json my-stash-export.json
python -m src.cli price-item map.txt
```

### Drawing the prices over the game itself

The stash grid in the browser still asks you to look away from the game and
match a picture of your tab against the real one. The **desktop overlay** removes
that step: it is a transparent, always-on-top, click-through window that draws
the same tiles — same colour bands, same suggested listing price, same
exalt rings, same confidence meter — directly on top of the real stash tab,
aligned cell for cell.

It is a passive draw-on-top window. It reads a JSON file this app wrote and
paints pixels above whatever is underneath. It does not read the game's memory,
hook it, or send it a single keystroke — it is click-through precisely so that
it *cannot* interact with anything.

**One-time calibration.** The overlay cannot find your stash tab by itself, and
finding it would mean screen-reading the client. So you tell it, once:

```bash
python -m src.cli overlay --calibrate
```

A cyan grid appears. Line it up with the stash tab's own cell borders:

| key | does |
| --- | --- |
| arrow keys | move the grid by one pixel |
| shift + arrows | resize the grid by one pixel |
| `+` / `-` | more or less opaque |
| `Enter` | save to `config.yaml` and start drawing |
| `Esc` | cancel, keeping the previous calibration |

Line up the *inner* lines, not just the outer border: cell size is stored as a
float (53.4px, not 53px) because 12 columns rarely land on a whole number, and
half a pixel per column is a price on the wrong map by the twelfth one.

If the keys do nothing, click the control strip once and try again. The keys go
to whichever window has focus, and a full-screen game underneath takes the focus
back at every opportunity; the strip is the one part of the overlay you can
click, so clicking it is how you hand the keyboard back.

The calibration is saved under `overlay:` in `config.yaml` together with the
screen resolution it was made at. Change resolution and the overlay says so
rather than drawing a tidy grid of prices over the wrong maps.

**Using it.** Price a tab as normal, then press **Send this tab to the overlay**
on the Stash Pricer page (or run `python -m src.cli price-tab --tab "Originator
Maps" --overlay`). That writes `data/overlay_state.json`. Then:

```bash
python -m src.cli overlay
```

A running overlay notices a new export within half a second — there is no need
to restart it when you re-price, change strategy, or switch tabs.

**Controls.** The overlay window itself cannot be clicked or focused, by design,
so the controls live in two places:

- **Ctrl+Alt+O** (configurable as `overlay.hotkey_toggle`) shows and hides it,
  system-wide, while the game has focus. This is a Windows `RegisterHotKey`
  reservation of that one chord — not a keyboard hook, and nothing else about
  your typing is visible to the process. If another application already owns the
  chord, the overlay says so in its status line and carries on.
- **Ctrl+Alt+A** (`overlay.hotkey_analyze`) re-reads the tab and re-prices it
  where you stand — see below.
- A small **control strip** sits above the grid with *show/hide*, *analyze*,
  *reload*, *calibrate* and *quit* buttons (and the same as `o`, `a`, `r`, `c`,
  `q` when it has focus). It is draggable, and it names the surface it draws
  over, because with both overlays up there are otherwise two identical strips
  on screen. It exists because a click-through window can never receive a
  keypress, so a stolen global chord per action would be the only alternative.

**Re-analysing without leaving the game.** *Reload* re-reads the file the app
last wrote. **Analyze** re-reads the *tab*: press **Ctrl+Alt+A** and the overlay
fetches the same tab (or the same character's inventory) from your account
again, re-prices it in the same mode and at the same selling strategy, and
redraws in place. That closes the loop the overlay was built for — run a map,
glance at the drop, run another — without an alt-tab in the middle of it.

It works because an export remembers where it came from. The state file carries
a small `source` block — kind, league, tab ids and names, pricing mode — written
by whichever thing produced it. That is also why some exports cannot be
re-analysed: a clipboard paste, a CSV and a re-opened snapshot describe maps,
not a place to go and read them again, so those are stamped `manual` and Analyze
says so rather than fetching something else and drawing it over your tab.

Details worth knowing:

- **It runs on a background thread.** A fetch plus a model pass is seconds, and
  a window sitting on top of a game is the worst possible place to freeze. The
  model and the database are loaded on the first analysis and kept, so the
  tenth press is fast.
- **A failed analysis leaves the last good prices on screen.** The strip says
  what went wrong. Stale prices whose age you can see beat no prices at all when
  you are mid-map.
- **Market mode stays market mode.** Analyze does not quietly substitute model
  prices for live ones; it re-runs the same comparison, bounded by the same
  `market_comparison.request_budget` the page uses, and reports what it spent.
- **It needs `POESESSID` in your environment or `.env`**, since it is a separate
  process from the app and does its own fetching.
- **A renamed tab is still found**, by id. A tab that is genuinely gone is
  reported rather than swapped for another one.

**The second overlay: your inventory.** The inventory panel is on the other
side of the screen from the stash and is 12x5 rather than 12x12, so it cannot
share a calibration. It gets its own — the `overlay_inventory:` block in
`config.yaml` — and its own everything else:

```bash
python -m src.cli overlay --profile inventory --calibrate   # once
python -m src.cli overlay --profile inventory
```

Both are separate processes and can be running at the same time, which is the
point: you look at your inventory while the stash tab is shut. Each watches its
own state file (`data/overlay_state.json` and
`data/overlay_inventory_state.json`), so re-pricing a tab does not disturb the
inventory overlay and vice versa, and each has its own toggle chord —
**Ctrl+Alt+O** for the stash, **Ctrl+Alt+I** for the inventory. Their control
strips are otherwise identical, so each one names the surface it is drawing
over.

On the Stash Pricer page the **Draw over** selector above the send button
chooses which of the two receives the current set of maps. It defaults to
whichever the maps came from, because sending an inventory read to the stash
overlay draws it in the wrong half of the screen and looks, from inside the
game, exactly like a bad calibration.

**Honest limitations:**

- **Fullscreen-exclusive mode will cover it.** That mode hands the display to
  the game and takes the desktop compositor out of the path; no overlay of this
  kind can draw over it. Use **windowed fullscreen**, which is what the overlay
  is built for and costs nothing in practice.
- **Windows only.** The transparency, the click-through flag and the hotkey are
  all Win32. Elsewhere the overlay refuses to start rather than opening an
  opaque window that swallows clicks.
- **Calibration is per resolution and per tab layout.** A 12x12 tab and a 24x24
  quad tab need different `grid` values; the overlay grows the grid to fit what
  it is given but the *cell size* is yours to set.
- **It draws only maps that have real stash coordinates.** Clipboard-pasted maps
  are numbered in paste order, which is not a layout, so they are left out
  rather than drawn on top of maps they do not belong to.
- **It does not follow the game window.** Move or resize the client and the
  calibration is stale until you redo it.
- The *Launch overlay window* button on the Stash Pricer page starts the process
  for you, but running `python -m src.cli overlay` in a terminal is better while
  you are setting it up — that is where its errors are printed.

---

## 8. Two pricing modes: the model, and the live market

The Stash Pricer has a **Pricing mode** selector. The default asks the trained
model. The other one, **Market comparison**, asks the trade site instead: for
each map it searches for the maps *currently listed* whose rewards sit inside a
window around that map's, and derives a price from those actual listings.

```
Model              7 numbers -> trained model -> a price      instant, free
Market comparison  7 numbers -> a trade search -> real asks   ~10s/map, API-heavy
```

### When to prefer it

- **When the model is stale.** The model prices the market it was trained on. A
  week-old model priced a market that no longer exists; this mode has no such
  problem because it only ever looks at what is listed now.
- **When the map is unusual.** A reward combination the model has barely seen
  comes back with low confidence and a wide band. The live search either finds
  maps like it or tells you it could not.
- **When the number is worth real money.** On a 3-divine map it is worth ten
  seconds to see the six listings the number came from.
- **Not for a whole quad tab on a whim.** Twenty maps is a couple of minutes;
  two hundred is most of an hour of rate-limited requests. Price with the model,
  then re-price the top of the table this way.

### Explainability is the point

Every map priced this way carries the exact listings behind it, in an expander:
each comparable's rewards, its price, its age, its seller, how far it sits from
your map in scaled feature space, and what share of the answer it therefore
carried. The discarded ones are listed too, with why. The model's price is a
number you trust or you do not; this one you can check.

The fair value is the **distance-weighted median** of those comparables —
weighted `1/(1+d²)`, so a map twice as far away counts far less than half — and
the interval is their weighted interquartile band. That band is a statement
about the market's own spread, not a multiplier chosen to look reassuring: six
listings all at 70c produce no interval at all, and the same six spread from 30c
to 150c produce a wide one.

### What it costs, and the four things that keep that bounded

**GGG's rate-limit policy is the whole cost model.** Measured live on 2026-08-25,
and note that the two endpoints are limited *separately*:

```
Search:  5:10:60,15:60:300,30:300:1800,600:21600:3600
Fetch:   12:4:10,16:12:300,50:300:300,1000:21600:1800
```

Thirty searches per five minutes is the binding search rule, and **fifty fetches
per five minutes** the binding fetch one — which matters more than it looks,
because a search that reads thirty listings is followed by three fetches. On the
per-map path the fetches are the larger half of the bill.

**So the market is read once for the whole tab, not once per map.** That is the
single decision that makes a full tab practical. Maps quantise into buckets by
their reward values, and it turns out that barely helps on a real tab: seventy
maps sampled from the collected 3.29 data land in a median of **sixty-five
distinct buckets**, so almost every map was paying its own search plus up to
three fetches — about 65-195 searches for one tab, and an hour and a half of
waiting.

A pooled read costs **sixteen searches and eighty fetches whatever the tab
holds**, because it walks the same stratified slices the training collector uses
(§4) — a trade search returns at most a hundred ids however many match, so one
query cannot see the market and sixteen spread across the reward range can. Each
map's comparables are then selected out of that shared set locally, by the same
scaled distance and the same widening tolerance schedule a per-map search would
have used, so a price means exactly what it meant before. **A map the pool has
nothing like still gets a search of its own** — an unusual map is precisely the
one worth spending a request on.

The run costs both plans against the published policy and takes the cheaper, so
on a three-map tab pooling switches itself off rather than spending sixteen
searches to save none.

Five things keep that from being unbounded:

1. **Adaptive tolerance.** The search starts tight and widens only if too few
   maps come back — at most three steps. This is not an optimisation, it is
   necessary: a live probe of a tight window around a real map matched exactly
   **one** listing in the whole league. **Which step produced the answer is
   recorded and shown**, because a price found at 4× tolerance is a weaker claim
   than one found at 1×, and confidence reflects it.
2. **The shared pool**, above: flat in the number of maps, so the cost of a tab
   stops growing with its size.
3. **A bucket cache**, for the maps that do fall back to their own search. Maps
   are quantised into buckets by their reward values, so near-identical maps
   share a single query. Entries expire after `cache_ttl_minutes` — live prices
   go stale, and an hour-old answer presented as the current market is worse
   than no answer.
4. **A request budget.** A hard ceiling per run, configurable and overridable in
   the UI. Hitting it stops cleanly rather than half-way through a fetch.
5. **Resumability.** Stopping — the budget, an interruption, a 429 — leaves every
   already-priced map priced. Press the button again and it continues; the bucket
   cache is on disk, so it continues across restarts too.

Rate limiting is never bypassed. This mode goes through the same client, the
same `RateLimiter` and the same 429 back-off as everything else in §14.

### Confidence, and how it degrades

Same four-signal shape and the same thresholds as the model's confidence, so
"Medium" means a comparable thing in both columns. It degrades with:

| | |
|---|---|
| **few comparables** | a price from three asks is a rumour |
| **wide tolerance needed** | the window was opened until it caught maps that are not really like yours |
| **stale listings** | an ask from two days ago is not today's market |
| **high price dispersion** | the comparables do not agree, so no single number from them means much |

### Model vs market, side by side

Running the live mode does not overwrite the model's numbers. Both are kept, and
a **Model vs market** table shows them together sorted by how far apart they are.
That gap is the interesting output: it is either a map the model has never seen
anything like, or a corner of the market that has moved since training. Neither
is something to average away.

### Limitations, specifically of this mode

- **Still asking prices.** Same caveat as everywhere else in this README — the
  trade site publishes asks, never sales.
- **Online sellers only, by default** (`status: online`), because an offline
  seller's ask is not an offer you can take. Set it to `any` for a bigger sample.
- **A quiet market gives no answer.** Some maps genuinely have no comparable
  listed at any tolerance. The mode says so rather than inventing a number.
- **Absence of a reward is categorical.** A map with no More Scarabs is compared
  only against maps with no More Scarabs, never against maps carrying +50%.
  Verified live: those two sets split the market 492 / 152.
- **It cannot price what the model can't either.** Magic maps, wrong tier, wrong
  corruption state — the scope in `config.scope` applies identically.

### From the CLI

```bash
python -m src.cli price-tab-market --tab "Originator Maps" --budget 150
python -m src.cli price-tab-market --json my-stash-export.json --comparables
python -m src.cli price-tab-market --tab "Maps" --tolerance 1 2 4 8
```

It prints progress, stops at the budget, and re-running continues. With a model
loaded it also prints where the two modes disagree most.

Configuration lives in the `market_comparison` block of `config.yaml`; every knob
above is there with a comment explaining it.

---

## 9. Affix count and whether a map is worth an Exalted Orb

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

So the estimate for the *next* orb enumerates rather than samples. It takes every
affix that could still roll — excluding those already on the map, and respecting
a full prefix or suffix block — applies each one's exact contribution, prices the
result with the same model that priced the map, and weights by how often that
affix actually appears in the collected market. Filling *every* open slot is
sampled instead: two open slots is some four thousand combinations and three is
a hundred and twenty thousand, per map, on a page that prices sixty at once. The
sample is seeded per map, so a verdict about spending currency does not change
when you refresh the page.

```
5/6 affixes  ·  1 open suffix  ·  1 orb at 1.25c, plus 5c to list and trade it
Worth exalting. Filling 1 open slot costs 6.25c and is expected to add 85c
— a net +79c.
Likely range: 68c to 129c gain     Bar to clear: 15c net
Chance it beats the cost: 100%     Model can resolve: ±62c
```

### Why it is not simply "is the gain bigger than the orb"

It was, and it said yes to **every map it was ever shown** — 700 of 700 on the
collected market, which is not a recommendation, it is a stuck needle. Four
things were missing.

**The orbs, plural.** A map with two open slots is a two-orb decision, and
charging one orb prices a plan nobody carries out.

**The trade.** Every gain has to be realised by listing the map, waiting,
answering a whisper and meeting someone. That cost is flat — a trade takes the
same few minutes whatever the map is worth — which is exactly why it bites
hardest on the cheap maps this was saying yes to.

**The model's own uncertainty, which is the one that matters.** The gain is a
*difference between two predictions of the same model*, and on the collected
market **85% of those differences were smaller than the model's own 80%
conformal band on the very same map**. Acting on a difference the instrument
cannot resolve is not a gamble, it is noise with a recommendation attached. So
the net gain — what survives paying for the orbs and the trade — has to clear
that whole band. Stated plainly: *keep more than the model admits it cannot
measure.*

**The floor.** Under 30c the model is not fitted at all; those listings are held
out of training, and §11 is blunt that the number down there is not a cheap
price but the absence of one. Maps below it now get **no verdict** rather than a
cheerful one — a third answer, drawn in brass rather than as a quieter "no",
because "we will not judge this" and "we judged it and the answer is no" are
different things.

Together these take 700 of 700 down to **106 of 555** — the denominator shrinks
because maps under the floor now get no verdict rather than a bad one. The share
held steady across a full model retrain (35% then, 28% now), and across trade
costs of 3c, 5c and 8c, which says the uncertainty gate is doing the work and
the numbers are not tuned to one lucky value. All of it is in `exalt:` in
`config.yaml`, and a rejected map still shows its range, its odds and the bar it
missed — a rejection is exactly when those are worth reading.

**Not every reward is affix-driven, and the catalogue refuses the ones that are
not.** Each feature has to predict held-out data before it is modelled.
`more_divination_cards` scores a held-out R² near zero, so exalting is never
credited with adding any, where a less careful version would have invented value
on every estimate. Which rewards passed is printed when the model trains.

The reason it fails that test is worth stating, because an earlier version of
this section guessed wrong and blamed the Originator memory. **It is a chisel.**
On 1,925 freshly collected listings, `More Divination Cards` and
`Quality (Divination Cards)` co-occur perfectly — 111 maps carry both, and not
one map carries either alone — and the value is always exactly `+50%`, never
anything else. So the feature is not a reward roll at all; it is a binary record
of a crafting decision somebody made, which is precisely why no affix predicts
it.

It is still worth keeping, and by a wide margin. Fitted on a seller-grouped
random split so the held-out slice carries the feature at its real rate (77 of
1,042 maps), dropping it takes overall MAE from 42.5c to 46.1c and R² from 0.545
to 0.480 — and on the maps that actually carry the flag, error nearly doubles,
from 39c to 76c. Those maps sell for about 3.3x the median despite carrying
*fewer* of the other rewards, and nothing else in the feature set knows that.

Four more chisel properties are visible in the same payloads and are currently
read by nothing: `Quality (Currency)` on 73 maps, `Quality (Scarabs)` on 34,
`Quality (Pack Size)` on 5 and `Quality (Rarity)` on 3. Whether they carry
signal the way the divination-card one does is untested.

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

## 10. Finding bulk lots to buy

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

## 11. Training the model

### What the model is optimised for

A price on an Originator Map feeds exactly one of two decisions, and they want
completely different things:

| | |
|---|---|
| **under ~30c** | the map gets rerolled, and nobody reads the number |
| **50c and up** | the number is what you list it for, and it is money |

Trained on plain average error over everything, the model optimises the first
one — the reroll pile is a third of all rows, so it dominates the average and
every model-selection decision gets made by maps whose prices do not matter.

Three changes follow from that, all measured on a chronological split averaged
over three split points and two seeds:

```
                              accuracy on maps    sellable maps the
                              worth selling       app called "reroll"
all rows, uniform weight          36.9%                  9.4%
floor + value weighting           29.5%                  0.8%
```

1. **A training floor.** Listings under 30c are held out of the fit. They stay
   in the database and in every count — they are real listings, not bad data —
   but fitting to their exact price spends capacity on a number nobody reads. If
   a dataset is mostly cheap listings the floor is skipped and the run says so.
2. **Value weighting.** Each row's weight rises with the square root of its
   price, so an error on a 400c map counts for more than the same error on a 40c
   one. Weighting linearly was measured too: it removes the low-end bias just as
   well but hands the fit to the top band, and the share of junk correctly
   identified as junk fell from 88% to 71%.
3. **Selection on the band that matters.** Backends are ranked by median error
   over maps worth selling, not overall median error. The old metric kept
   choosing the model that was best on the maps you throw away.

**29.5% is the floor, not a disappointment.** Identical maps — same seven reward
values — are listed at prices scattering about 29% around their own median at
every price level, so that is the irreducible error in asking-price data. An
oracle that already knew the right answer would score the same. When `sell_mape`
is near 29 the model is done; chasing it lower is chasing noise.

Because of the floor, every map below it comes back at roughly the same number
(around 24c — a 0-reward map and a 40-quantity map both land there). That figure
is not a cheap price, it is the absence of one, so the tables show an **Action**
column — `Reroll` / `Marginal` / `Sell` — rather than letting it read as a
precise valuation.

### Above 200 chaos the market has four prices

Measured on 4,102 live listings: above 200c, 71% are priced in divine and
**every one of those is a whole number of divine** — 1d, 2d, 3d, 4d. Below 200c
it is 100% chaos, on round numbers. So a suggested price of "just under 340c" is
not a more precise 2 divine; it is a price nobody types.

Suggested listing prices and the pasteable `~price` note snap to whole divine in
that band. The displayed table value does not — a column where every expensive
map reads the same handful of numbers cannot be sorted or compared. The rule
stops at 5 divine, where the sample runs out.

### Things that were measured and rejected

Kept here so they do not get re-suggested:

- **Affix features.** Every listing stores its explicit affixes as stat hashes.
  Adding 100 of them as indicator columns made the model *worse* (29.5% → 36.4%
  R², sell error up): on these maps the affixes essentially *are* the reward
  values, so it is redundancy plus 100 columns of noise.
- **Isotonic calibration of the top band.** Halves the top-end bias and costs
  more than it saves everywhere else (median error 13c → 23c).
- **Quantile loss above the median.** Improves top-end bias, but the gain sits
  inside the run-to-run noise band and it adds a knob.
- **Dropping flat-priced seller blocks.** A controlled A/B removed 8% of the
  data and bought nothing.

### When training happens: only when you ask

**Nothing retrains on its own.** Collecting more listings does not change a
single price until you retrain — the data and the model are separate things, and
growing the first leaves the second exactly as it was.

```bash
python -m src.ml.train                 # or: python -m src.cli train
python -m src.cli train --tune-weights # also search recency-weight schedules
```

or **Collect and retrain** on the Market Data page, which does both in one go
and is the button to reach for by default — collecting without retraining is
what leaves the app showing old numbers over new data. **Retrain on stored
data**, beside it, trains on everything already in the database — including
whatever a cancelled collection left behind — without calling the API.
**Retrain model** in Settings does the same thing from the other page.

> Passive collection (§4) can retrain on its own, every N scans and only when
> the scans actually inserted rows — a quiet league returns the same listings,
> and refitting them spends minutes to produce the model already loaded.
> `model.retrain_interval_hours` used to sit in the config as a schedule that
> nothing acted on; it is gone, and `collection.passive.retrain_every_scans` is
> the honest version of it.

Because that is easy to lose track of, the **Dashboard** compares the two and
says which state you are in — either *"Model is current: trained on 9,085 of
9,085 usable listings"* or a warning naming the gap and pointing at Retrain.
Check there after a collection run.

### The trained model ships with the repo

Since 0.2.0 the model itself is tracked, not just the data it was fitted on:

```
models/originator_model.pkl      the model            1.2 MB
models/originator_model.json     metrics and schema
models/affix_catalogue.json      affix contributions
```

Every clone and every download therefore prices a map on its first run, instead
of showing an empty app that first asks for a 20-minute collection. It also
means anyone can rebuild an identical release zip, where before only the machine
that had trained could build a useful one.

Three things to know about a model you did not fit yourself:

- **It is a snapshot of one league, at one moment.** The same caveat the CSV
  export carries below, and the reason to retrain rather than trust it forever.
  `models/originator_model.json` records the data window it saw; the Model
  Analysis page shows it.
- **Retraining rewrites all three files**, so they turn up in `git status` and
  in diffs. That is a real change to what the next release ships, worth a commit
  message, not noise to suppress.
- **It is a pickle**, so loading it executes code from it. That is fine for a
  file you or this repo produced and would not be fine for one a stranger sent
  you. A model whose feature schema does not match the current parser is refused
  at load time regardless.

### Sharing the dataset instead of everyone collecting it

Collecting a few thousand listings takes hours of rate-limited requests, and
there is no reason for each person to repeat it. A ready-made export lives in
the repo:

```
data/originator_listings_allflame.csv     # 15,695 listings, ~1.6 MB
```

You do not have to do anything with it: the first run imports it (§6). It is
also there to import by hand on **Market Data → Import → CSV**, and it is the
same format the export button produces, so anyone can share theirs the same way
(**Market Data → Inspect and export**, which offers an anonymised export).
Rebuild it from your own database with `python scripts/export_sample.py --seed`.
That also records the new file as already loaded in the database it came from,
so the next launch does not helpfully re-import your own rows on top of
themselves — which is worth a sentence because it happened, and took a database
from 9,552 rows to 11,472 before anyone noticed.

Three honest caveats:

- **The account names are gone and the listing ids are blank.** Each account
  becomes a label like `anon-0042` that means nothing outside the one file. The
  labels are kept rather than blanked because the train/test split groups on
  them: when a whole export shared the empty string it became one group, and
  training on it produced a validation slice of three rows. Blank listing ids
  are deliberate too — a live id would upsert over somebody's own collected row.
- **It is a snapshot, not a feed.** Prices move through a league. An export from
  three weeks ago will train a model that prices a market that no longer exists.
  `indexed_at` and `captured_at` are both preserved, so age is visible and the
  collection history survives — which is what lets the split below come out
  chronological rather than treating three weeks of collecting as one sitting.
- **It is one league.** Rates and prices do not carry across leagues, which is
  why the first-run import declines when your league is not the sample's.

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

## 12. Tracking what you listed, and learning what actually sells

Everything else in this tool is fitted on **asks** — what sellers wanted. Your
own listings are the one place it can see what a buyer actually paid, and that
makes them worth tracking on their own terms.

The **My Listings** page answers one question — *which of my maps are sitting* —
and then makes the answer actionable in the two places that help: a stash search
regex that lights the stale maps up in game, and an overlay export that puts the
new price onto them where they physically are.

### How it watches

With `POE_ACCOUNT_NAME` set, a **toggle on the My Listings page** starts a
background poller (`src/tracking/poller.py`) running one account-scoped trade
search — your own listings — while the app is open. The switch is on that page
rather than in config because it is the page the tracker reports on, and turning
it off cancels the running poller rather than only clearing a flag. The choice
is written to `listing_tracker.enabled` so it survives a restart. It reconciles each poll against the last into
events: a map appeared, a map is still sitting, a map is gone.

The cadence is deliberately uneven. A listing's first hour is where the
fast-sale signal lives — the difference between "sold in eleven minutes" and
"sold in three hours" is most of what the learning half has to work with, and a
fifteen-minute poll blurs it. A map that has sat for three days does not need a
five-minute check to confirm it is still sitting. So young listings are polled
every five minutes and old ones every fifteen, which spends requests where they
buy information. A markdown makes a listing young again, because a new price is
a new question.

The poller runs on its own thread with its own database connection, and it stops
when you close the app. It never restarts itself. §14 has the request cost.

### What it can and cannot know

**The stale half is exact.** A map still listed after sixty-two hours has been
listed for sixty-two hours whatever the polling gaps, so every "sitting for N
hours" figure on the page is a measurement.

**The sold half is bracketed.** Polling stops when the app closes, so a map that
sold overnight is known only to have sold *somewhere inside* a nine-hour window.
The code treats these as **interval- and right-censored observations**
(`src/tracking/censoring.py`) rather than throwing them away or pretending the
midpoint is the answer — discarding them would bias what is left toward whoever
happens to leave the app running. A Turnbull-style EM fit
(`src/tracking/survival.py`) turns that censored history into: *if I list at this
ratio to the model's estimate, what are the odds it clears within W hours?*

A map that vanishes and comes back within `reappearance_window_hours` was never
sold — someone else's search index blinked, or you relisted it.

### What it does with the answer

- **Re-price recommendations.** Until there is enough of your own history to fit
  the real thing, a fixed fallback ladder walks a stale map down by staleness
  (8% at 24h, 15% at 72h, 22% at a week) with a hard floor at 75% of the
  estimate. Left alone, a ladder walks a map nobody wants down until it is being
  given away — each markdown producing more evidence that the price is still too
  high, which is exactly what a ladder reacts to. Every recommendation carries a
  `source` and the page prints it: **a fallback labelled as a measurement is
  worse than no number at all.**
- **Alerts**, but only when the data is decisive. An alert fires only when the
  *whole* bracket sits on one side of the threshold — a map that sold somewhere
  between twenty minutes and nine hours might have sold fast or slowly, and the
  overnight gap that produced that bracket is not a licence to guess.
- **A personal calibration factor** — how far your listings actually clear from
  what the model says — applied as a multiplier on the **suggested listing
  price**, never on the fair-value estimate.

That factor is the most dangerous number in the app, because it silently moves
every price you are shown. So it is guarded the same way the observed-spread
multipliers are:

- **Evidence on both sides, or nothing.** Fast sales bracket the clearing ratio
  from below, stale listings bracket it from above. With only one side there is
  no bracket, just a direction — and a direction is not a factor.
- **Clamped to 0.8–1.3, and it says on the page when it clamped.**
- **A measurement outside 0.5–2.0 is refused, not clamped.** A factor of 0.25
  does not mean sell at a quarter price, it means the model is miscalibrated,
  and quietly scaling by 0.25 would hide the one thing worth seeing.
- **Bound to a model version.** The model's known underpricing is a hedging
  artefact with its own fix, and this factor is measured *on top of* whatever the
  active model already does. Carry it across a retrain and the two corrections
  stack, the second fitted on prices the first already moved.

It needs `listing_tracker.calibration.min_sold` (default 20) resolved sales
before it will produce a number at all.

### Settings

On/off lives on the page itself. The rest is under `listing_tracker:` in
`config.yaml` — poll cadences, the reappearance window, what counts as stale,
the target clearing window and probability, the calibration clamp, and the
fallback ladder's steps and floor.

---

## 13. Current limitations

Read this part.

- **These are asking prices, not sales.** Nothing in the public data says what
  anything actually sold for. A listing sitting at 300 divine for a week is
  evidence of an *ask*, not a value. Treat every number as a starting point and
  look at the comparables next to it.

- **The seven features explain a good part of the price, but far from all of
  it.** On a clean sample, `more_currency` and `quantity` carry most of the
  signal (correlations with log price around +0.6), `pack_size` follows (+0.44),
  and `rarity`, `more_maps` and `more_scarabs` are close to flat on their own.
  On the 9,085 listings the shipped model is fitted to, held-out R² lands around
  0.58 on a chronological split with a median error of ~24 chaos. Predictions
  are a good ballpark, not a quote — the comparable listings shown beside every
  estimate are doing genuine work, not decoration.

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
  number as a rough guide. It is drawn as a meter — on the grid tiles, on the
  overlay tiles and beside the number in the detail panels — because a score
  that only appears in a tooltip is a score nobody reads.

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

- **The desktop overlay is aligned by hand, and cannot draw over
  fullscreen-exclusive mode.** It is Windows-only, it does not follow the game
  window when you move it, and its calibration is tied to one screen resolution.
  Section 7 has the full list.

- **Stash access via POESESSID is not officially supported.** It works, but GGG
  offers no stability guarantee and the cookie expires.

- **The OAuth path is unverified.** See section 5.

- **The `More Divination Cards` property `type` id is unknown**, because it never
  appeared in the sampled listings. Parsing matches on name first, so this does
  not break anything, but it is documented rather than guessed.

- **Single league, single tier at a time.** Both are config values, not
  hardcoded, but a model is trained for one scope.

- **Your own sale times are bracketed, not observed.** The listing tracker (§12)
  only polls while the app is open, so a map that sold overnight is known to
  have sold somewhere inside a nine-hour window. The censored fit handles that
  honestly rather than guessing a midpoint, but it means the clearing-speed
  numbers get sharper the more you leave the app running, and the personal
  calibration factor refuses to produce anything at all until 20 sales have
  resolved. It is also the only number here fitted on outcomes rather than
  asks — which is exactly why it is clamped and why it says when it clamped.

---

## 14. GGG and API compliance

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

## 15. Project layout

```
app.py                     Streamlit entry point
src/
  schema.py                the shared NormalizedMap / MarketListing types
  config.py                config.yaml + .env loading
  auth/poe_oauth.py        OAuth flow (unverified -- see docs/data-access.md)
  stash/                   official_api / json_import / clipboard_import
                           character_api -- one character's inventory grid
  parsing/                 item_parser / implicit_parser / price_parser / affixes
  market/                  collectors/, comparables, outliers, rate_limit
                           bulk -- the buying path, never stored, never trained on
                           live_comparables -- the market-comparison pricing mode
  database/                schema, migrations, repository
  ml/                      features, models, train, predict, confidence, explain
                           affix_catalogue -- per-affix reward contributions
  pricing/                 currency, sell_strategy, fair_value, exalt
                           market_comparison -- live mode wired to PricedMap
  tracking/                your own listings: reconcile (poll -> events),
                           censoring + survival -- bracketed sale times, fitted
                           honestly; reprice, alerts, calibration, poller
  overlay/                 the desktop overlay: state (the JSON contract),
                           calibration, layout -- and window, the only module
                           that imports tkinter
                           profiles -- stash tab vs inventory: two calibrations,
                           two state files, two processes
  ui/                      one module per page, over one design system in
                           common.py: page_header, stat_rail, section, panels
                           and the CSS -- see "Colour is data" below
  cli.py                   everything the UI does, without Streamlit
docs/
  originator-modifiers.md  Phase 1 research, confirmed / inferred / unknown
  data-access.md           Phase 2 research, what was verified live
  architecture.md          how the pieces fit and why
tests/                     937 tests, including live-render checks of every page
```

See **[docs/architecture.md](docs/architecture.md)**.

### Colour is data

The interface has one rule, and it is the reason it looks the way it does.

**Every hue on screen belongs to a value band, a confidence level or an exalt
verdict. Everything structural is slate and brass.** Nav, borders, buttons,
labels, rules, the active tab -- none of them are allowed a signal colour.

It is not decoration, it is legibility. The stash grid already spends six
colours on value bands -- slate, teal, green, amber, rust, magenta, plus a
near-black for anything under 30c -- and one more on the exalt ring, and those
are the whole point of the page. Streamlit's default red primary button, its blue
charts and its pastel alert blocks added three more hues that meant nothing, and
they were the brightest things on screen. Demoting the accent to a muted brass
is what lets a purple tile read as "this map is worth something" at a glance.

Three consequences worth knowing before editing a page:

- **Figures are set in the mono face**, with tabular figures, so a column of
  prices aligns and a four-digit total cannot be mistaken for a three-digit one.
- **`stat_rail` replaces pipe-separated captions.** The page used to say
  "26 maps found | 25 priceable | 1 unsupported | ..." in one grey line; every
  figure in it was worth reading and none could be found.
- **A single-series chart uses `CHART_SERIES`**, which is deliberately none of
  the band colours -- a histogram of every listing is not a percentile band
  and must not claim to be one.

`.streamlit/config.toml` carries the same palette, so the parts of Streamlit
that CSS cannot reach start from the same ground.

---

## 16. Tests

```bash
python -m pytest
```

Covers Originator identification, T16 detection, corruption detection, implicit
and property parsing in both formats, missing and unknown values, stash
coordinate preservation, price parsing, divine-to-chaos conversion, duplicate
detection, outlier flagging, feature construction, time-aware validation, model
serialization and schema refusal, prediction output, comparable ranking, selling
strategies, live-comparable query construction, tolerance widening, bucket
cache hits and expiry, request budgets, resuming an interrupted run,
distance-weighted price and interval derivation, confidence degradation — and a
smoke test that renders every Streamlit page through
Streamlit's own test harness, so a broken page fails the suite instead of showing
up as a red box in your browser.
