# Originator Maps in Path of Exile 3.29

Everything below is labelled **Confirmed**, **Inferred**, or **Unknown**.

"Confirmed" means it was read directly out of a live 3.29 response from GGG's own
endpoints on **2026-08-23**, league **Allflame**. Nothing here is recalled from
memory, and no modifier range has been invented.

Sources used:

- `GET https://www.pathofexile.com/api/trade/data/stats`
- `GET https://www.pathofexile.com/api/trade/data/filters`
- `GET https://www.pathofexile.com/api/trade/data/leagues`
- `POST https://www.pathofexile.com/api/trade/search/Allflame`
- `GET https://www.pathofexile.com/api/trade/fetch/{ids}?query={id}`

The fetch endpoint returns `item.extended.text`, which is a base64 copy of the
exact text the game puts on the clipboard. Every item-text sample quoted here is
a real listing, not a reconstruction.

---

## 1. The headline correction to the original brief

**Confirmed.** The values this project prices are **not implicit modifiers**.

The brief assumed the seven pricing features live on the item as special
implicits. They do not. In the live data they arrive as item **properties**, in
the block above the first divider, exactly where `Item Quantity` has always sat:

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
Monster Level: 83
--------
Area is Influenced by the Originator's Memories
--------
<8 map modifiers>
```

There **is** exactly one genuine implicit, and it carries no number:

```
Area is Influenced by the Originator's Memories
```

The brief anticipated this possibility ("whether any implicit-looking values are
actually generated from normal map properties"), and the answer is yes. It does
not change the modelling approach — the seven values are still seven independent
numeric features — but it does change the parser, which reads the property block
rather than the implicit block. `src/parsing/implicit_parser.py` handles both
shapes anyway, because community exports sometimes render them as sentences.

---

## 2. Identifying an Originator Map

**Confirmed.**

| | |
|---|---|
| Implicit text | `Area is Influenced by the Originator's Memories` |
| Trade stat id | `implicit.stat_2696470877` (`stat.implicit.stat_2696470877` in item JSON) |
| Item JSON flag | `"memoryItem": true` |
| Base type | `Map (Tier N)` — the 3.29 unified map base |

The implicit has `magnitudes: [{"min": "1", "max": "1"}]`, i.e. it is a binary
marker with no roll.

**Confirmed.** Originator influence appears on maps of many tiers, not only T16.
A live example at Tier 6:

```
Item Class: Maps
Rarity: Magic
Valdo's Map of Penetration (Tier 6)
--------
Item Quantity: +32% (augmented)
Item Rarity: +83% (augmented)
Monster Pack Size: +26% (augmented)
--------
Item Level: 73
--------
Area is Influenced by the Originator's Memories
```

**Inferred.** Originator influence cannot be applied by the player through the
Map Device; it is a drop property. Community sources state this consistently, and
nothing in the API contradicts it, but it was not verified against game
behaviour.

---

## 3. The reward properties

**Confirmed.** These are the property names and their numeric `type` ids as they
appear in live item JSON:

| Property name | `type` id | Feature name in this project |
|---|---:|---|
| `Item Quantity` | 2 | `quantity` |
| `Item Rarity` | 3 | `rarity` |
| `Monster Pack Size` | 4 | `pack_size` |
| `More Maps` | 88 | `more_maps` |
| `More Scarabs` | 89 | `more_scarabs` |
| `More Currency` | 90 | `more_currency` |
| `More Divination Cards` | 91 | `more_divination_cards` |

**Confirmed since.** The `type` id for `More Divination Cards` was unknown when
this was first written — the reward is rare enough that it did not appear in the
original captured sample — and was later observed as **91** in stored Allflame
payloads. The parser matches on the property *name* first and only falls back to
the id, so the gap never broke parsing while it lasted.

### 3a. The Covens rename

**Confirmed**, read out of 200 live listings from `POST /api/trade/search/Covens`
on **2026-11-22**. Two of the seven were relabelled:

| Through Allflame | In Covens | `type` id |
|---|---|---:|
| `Item Quantity` | `[AtlasDropScaling\|Atlas Drops]` | 2 → **112** |
| `More Scarabs` | `More [Thread\|Threads]` | 89 — **unchanged** |

Three things here are worth recording, because together they are the whole
lesson:

1. **Names now arrive wrapped in localisation markup.** `[InternalId|Display
   Text]`, and sometimes `[Word]` with no alternative. A literal name match
   fails against the raw string, so the display half is extracted first —
   `normalise_property_label` in `src/parsing/implicit_parser.py`.
2. **The numeric fallback is necessary but not sufficient.** It saved
   `More Threads`, which kept id 89. It did not save quantity, which changed
   *both* name and id. A design that relies on ids being stable is a design
   that will lose a feature eventually.
3. **The failure was silent and shaped to stay silent.** Every map collected in
   Covens read `quantity: 0`. Nothing raised, nothing logged. Near the training
   floor a missing feature barely moves the predicted price, so cheap maps
   looked fine; on a strong map it roughly halved the number. The symptom was
   therefore "the pricer is a bit pessimistic about my best maps", which is not
   a symptom anyone debugs.

Unrecognised reward-shaped properties are now reported rather than dropped —
see `looks_like_a_reward` and `tests/test_league_renames.py`. Both the old and
the new names remain valid permanently: historical rows, community CSVs and
stash exports from past leagues all still have to parse.

**Confirmed.** On the trade site, the four "More" rewards are exposed as
*pseudo* stats, not as searchable explicit or implicit mods:

```
pseudo.pseudo_map_more_map_drops     "More Maps: #%"
pseudo.pseudo_map_more_scarab_drops  "More Scarabs: #%"
pseudo.pseudo_map_more_currency_drops "More Currency: #%"
pseudo.pseudo_map_more_card_drops    "More Divination Cards: #%"
```

Item Quantity / Rarity / Pack Size have their own dedicated search filters
(`map_iiq`, `map_iir`, `map_packsize`) under `map_filters`.

---

## 4. Observed value ranges

**Confirmed for what was observed; not a claim about the full possible range.**

These are the distinct values actually seen across the collected sample of
uncorrupted T16 Originator listings. They are *observations*, not the game's
generation table, which is not published. The project deliberately does not
hardcode minimums, maximums or increments anywhere — the model learns from
observed values, and the parser accepts any integer.

| Feature | Values observed on T16 | Notes |
|---|---|---|
| `quantity` | ~75–120 | continuous-looking; comes from map affixes plus the base |
| `rarity` | ~40–160 | continuous-looking |
| `pack_size` | ~26–58 | continuous-looking |
| `more_currency` | 0, 47, 94 | **quantised** — looks like a fixed tier per roll |
| `more_maps` | 0, 35, 40, 70, 105 | **quantised** |
| `more_scarabs` | 0, 35, 36 | **quantised** |
| `more_divination_cards` | 0, 18 (rare) | very few observations |

**Inferred.** The clustering of the four "More" values on a small set of numbers
(35 / 70 / 105 for maps; 47 / 94 for currency) strongly suggests they are granted
by a small number of discrete map modifiers that can stack, rather than rolling
continuously. This is inference from the observed distribution, not confirmed
from a modifier table.

**Unknown.**
- The complete set of possible values, and whether higher tiers than those
  observed exist.
- Exact roll increments.
- Whether any of the four "More" rewards are mutually exclusive. The sample
  contains maps with **three** of them at once (`More Maps` + `More Scarabs` +
  `More Currency` on one listing), so at minimum they are not pairwise exclusive.
- How many reward properties can appear on one map at most.

---

## 5. What affects the ranges

| Question | Status | Finding |
|---|---|---|
| Does map tier affect the ranges? | **Inferred** | Lower-tier Originator maps in the sample carried lower Quantity/Rarity/Pack Size and no "More" rewards at all. Consistent with tier mattering, but tier and item level covary, so this does not isolate the cause. |
| Does item level affect them? | **Unknown** | T16 listings span ilvl 83–85 with no visible difference. Not enough spread to test. |
| Does quality affect them? | **Unknown** | No quality-bearing Originator maps were observed in the sample. Note that the separate implicit `Quality applies to Pack Size instead of Item Quantity` (`implicit.stat_3036505016`) exists in 3.29 and would change how quality interacts, but it was not seen on any Originator map. |
| Does corruption change the system? | **Confirmed** | Yes, materially. See below. |
| Does an Originator tier/rank exist? | **Unknown** | No rank or tier field for the influence was found in item JSON. The implicit is binary. |

### Corruption — the most important finding

**Confirmed.** The highest-priced T16 Originator Maps on the live trade site are
**corrupted**, carrying 8+ map modifiers. Every one of the top ten by price in
the sampled search was corrupted, at 195–300 divine.

This directly contradicts the brief's instruction to price "uncorrupted T16
Originator Maps". The project was nevertheless configured for **uncorrupted only**,
because that is what the user chose after being shown this finding. Corrupted maps
are parsed and displayed, but marked `Unsupported` rather than priced.

The scope is one config flag, not a code change:

```yaml
scope:
  map_tier: 16
  require_uncorrupted: true    # set false to price corrupted maps too
```

Setting `require_uncorrupted: false` makes the corrupted market priceable
immediately; the collector, parser, model and UI all already handle it.

---

## 6. Reward values are derived, not intrinsic

**Confirmed.** `Item Quantity`, `Item Rarity` and `Monster Pack Size` are marked
`(augmented)` in item text, meaning their displayed value is the base plus the
contribution of the map's ordinary prefixes and suffixes. `More Currency`,
`More Maps` and `More Scarabs` are marked `(augmented)` too.

The practical consequence: the seven features already summarise the reward-
relevant part of the map's affixes. The brief's decision not to model individual
map prefixes and suffixes in the first production model is well founded — those
affixes' reward contribution is already visible in these seven numbers. What the
seven numbers do *not* capture is map **danger** (the deadly modifiers that make a
map harder to run), which is a real source of residual price variance and is
noted as a known limitation in the README.

---

## 7. Modifiers that exist in 3.29 and could plausibly matter later

**Confirmed to exist** in `/api/trade/data/stats`, **not confirmed** to occur on
uncorrupted T16 Originator Maps:

| Stat id | Text |
|---|---|
| `implicit.stat_2777224821` | `#% increased Maps found in your Maps` |
| `implicit.stat_2998191728` | `#% increased Quantity of Items found in your Maps` |
| `implicit.stat_754268389` | `Area has an additional random Scarab effect` |
| `implicit.stat_3036505016` | `Quality applies to Pack Size instead of Item Quantity` |
| `implicit.stat_1522386771` | `Modifiers to Item Quantity will affect the number of encounter rewards dropped by #% of their value` |
| `pseudo.pseudo_map_conversion_memory_line` | `#% chance for dropped Maps to convert to Atlas Memories` |

`src/parsing/implicit_parser.py` already recognises the sentence forms of the
first two and folds them into `more_maps` / `quantity`. The others are preserved
in `raw_modifiers` but are not features, because there is no evidence they occur
in scope.

---

## 8. Summary for the model

Seven independent numeric features, never combined into a hand-weighted score:

```
quantity
rarity
pack_size
more_currency
more_maps
more_scarabs
more_divination_cards
```

Absence of a reward genuinely means no bonus, so an absent value normalizes to
`0` (see `empty_features()`). Raw item data and the full modifier list are
preserved alongside, so a future model can use them without re-collecting.
