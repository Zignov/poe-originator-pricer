# Data access: what is officially available, what was verified, and what is not

Checked live on **2026-08-23** against Path of Exile 3.29, league **Allflame**.
Where something was not verified, it says so. Nothing in this project attempts to
bypass Cloudflare, authentication, rate limits, or access controls.

---

## 1. The official developer API

Base: `https://api.pathofexile.com` — documented at
<https://www.pathofexile.com/developer/docs/index>.

**Confirmed from the docs.** The documented endpoints include Account Profile,
Account Characters, Account Leagues, **Account Stashes**, Guild Stashes, Public
Stashes, Currency Exchange, Leagues and Item Filters.

**Confirmed from the docs.** All of these require OAuth. The scope needed to read
private stash tabs is `account:stashes` — "viewing the account's stashes and
items".

OAuth endpoints:

| Endpoint | Purpose |
|---|---|
| `/oauth/authorize` | user consent page |
| `/oauth/token` | token creation |
| `/oauth/token/revoke` | revocation (`oauth:revoke` scope) |
| `/oauth/token/introspect` | introspection (`oauth:introspect` scope) |

### The blocker

**Confirmed from the docs, 2026-08-23.** GGG's developer documentation states:

> "We are currently unable to process new applications."

Client registration would normally be self-service via *Manage applications* in
account settings, but new applications are not being accepted. There is no
documented alternative route — no email application form is offered.

**Consequence.** The officially supported path to private stash tabs is
**closed to new developers right now**. This is not a limitation of this project;
it is the current state of GGG's programme.

**What was built anyway.** `src/auth/poe_oauth.py` implements the full
authorization-code flow with PKCE and a localhost redirect catcher, and
`src/stash/official_api.py:OAuthStashSource` implements the stash reads behind
the same `StashSource` interface as everything else. Supplying
`POE_OAUTH_CLIENT_ID` is the only change needed if registration reopens.

**This is explicitly NOT verified.** It has never been run against a real
`client_id`, because none can be obtained. The endpoint paths and parameter names
follow GGG's published documentation. It is scaffolding, and the code says so in
its own docstring.

---

## 2. The legacy stash endpoint (what actually works today)

```
GET https://www.pathofexile.com/character-window/get-stash-items
    ?accountName=...&realm=pc&league=Allflame&tabs=1&tabIndex=0
Cookie: POESESSID=<the user's own session cookie>
```

**Not verified against a live account.** No PoE account credentials were
available while building this, so the request/response shape is implemented from
the long-standing well-known format used by community tools, and the code path is
covered by tests against captured-shape fixtures rather than a live call. It is
implemented as `src/stash/official_api.py:SessionStashSource`.

**Why this is not a bypass.** The user supplies their own session cookie, for
their own account, and it is sent to GGG's own site. No authentication is
circumvented, nothing is spoofed, and no other user's data is reachable. It is
the same request the user's own browser makes when they open their stash tab. The
brief says to prefer real authentication "where possible" over pasted cookies —
here the officially supported alternative is closed, so this is the remaining
option, and the app is honest with the user about what it is.

**Caveats surfaced in the UI:**

- The cookie expires; a 403 is reported as "log in again and copy a fresh one".
- The endpoint is **not** in GGG's documented API reference. GGG's docs state:
  *"Requests for access to any other internal website APIs or in-game resources
  will be denied."* That sentence is about support requests for access, not a
  prohibition on the endpoint your own browser already uses — but it does mean
  GGG offers no stability guarantee, and this endpoint could change or disappear
  without notice. That is exactly why it sits behind the `StashSource`
  abstraction.

**Rate limiting.** The adapter enforces a minimum interval between calls and
reports GGG's `Retry-After` on 429 rather than retrying immediately.

---

## 3. The trade search API (market data)

```
POST https://www.pathofexile.com/api/trade/search/{league}
GET  https://www.pathofexile.com/api/trade/fetch/{ids}?query={queryId}
GET  https://www.pathofexile.com/api/trade/data/{stats|filters|leagues|items}
```

**Confirmed — verified live, unauthenticated, 2026-08-23.** All of the above
returned HTTP 200 with no credentials. 2000+ real Originator Map listings were
collected through them for this project's dataset.

### Rate limits, read from GGG's own response headers

The server states its policy on every response, and the client obeys whatever it
reports rather than guessing:

```
Search:  X-Rate-Limit-Ip: 5:10:60,15:60:300,30:300:1800,600:21600:3600
Fetch:   X-Rate-Limit-Ip: 12:4:10,16:12:300,50:300:300,1000:21600:1800
```

Each triplet is `max_hits : period_seconds : penalty_seconds`. The matching
`X-Rate-Limit-Ip-State` header reports current usage. `src/market/rate_limit.py`
parses both, waits until a request would not breach any rule, and sleeps out any
penalty the server signals. During the real 2000-listing collection it correctly
paused for several minutes at a time when the fetch policy demanded it.

GGG's docs: rate limits are *"dynamic and subject to change"*, and *"violating
rate limits frequently can result in revoked access"*. Hence: read the headers,
never hardcode, never retry through a 429.

### Narrow "find me maps like this one" queries — verified live 2026-08-25

The market-comparison pricing mode (`src/market/live_comparables.py`) needs a
different query from the collector's: instead of sampling the market broadly, it
asks for the listings that sit inside a window around one particular map. Four
searches and one fetch were spent confirming it works, against league
**Allflame**:

**Confirmed.** `map_iiq`, `map_iir` and `map_packsize` accept `{min, max}` and
are honoured together with a pseudo-stat range. One search carrying `map_iiq`
74-94, `map_iir` 124-144, `map_packsize` 22-42 and
`pseudo.pseudo_map_more_currency_drops` 37-57 returned listings whose printed
properties all sat inside those windows (`+77%`, `+128%`, `+28%`, `+47%`).
`map_iir` was not previously used by this project; the other two were.

**Confirmed.** A tight window really does come back nearly empty. That same
query matched a `total` of **1** in the whole league. This is why the mode widens
its tolerance adaptively rather than using a fixed window, and why the tolerance
that produced an answer is recorded as a confidence signal.

**Confirmed.** `{"type": "not"}` stat groups work, and absence of a reward is
worth expressing. `not` on `pseudo.pseudo_map_more_scarab_drops` with `min: 1`
returned **492** listings against **152** for the matching `and` group — the two
halves partition the market, so a map with no scarab bonus is compared only
against maps that also have none.

**Confirmed.** The search policy is the cost model for this mode:
`5:10:60,15:60:300,30:300:1800,600:21600:3600`. The third rule — thirty searches
per five minutes, one every ten seconds sustained — is the binding one on a long
run, not the five-per-ten-seconds burst rule. `RateLimiter.sustained_interval()`
reports that figure so the UI quotes an honest estimate.

**Measured end to end, 2026-08-25.** Pricing three maps out of a 20-item stash
export cost 14 requests on the first run and 4 on a second run of the same tab,
the difference being bucket-cache hits. Roughly 4-7 requests per distinct map
when the tolerance has to widen to its last step, 2-4 when it does not.

### User-Agent

GGG requires the form `OAuth {clientId}/{version} (contact: {contact})`. With no
client id obtainable, the client name occupies that slot, and the contact address
is real and configurable:

```
OAuth poe-originator-pricer/0.1.0 (contact: you@example.com)
```

Set it in `config.yaml` (`http.user_agent_contact`) or `.env`
(`POE_CONTACT_EMAIL`). It is not optional — GGG asks automated clients to be
identifiable.

### Is bulk collection permitted?

**Partly unknown, and treated conservatively.**

What is known: the endpoints are public and unauthenticated; GGG publishes and
enforces rate limits on them; a large ecosystem of community tools uses them; GGG
has not published a blanket prohibition on reading them.

What is *not* known: `/api/trade/*` is a website API, not part of the documented
developer API reference. There is no published Terms-of-Service clause explicitly
granting or denying programmatic use of it.

The position taken here:

- collect at a **modest, self-limiting rate**, driven by GGG's own headers;
- identify the client honestly;
- back off rather than retry on 429;
- **never** spoof multiple clients, rotate IPs, evade limits, or touch
  Cloudflare;
- keep the whole thing behind `MarketCollector`, so the app remains fully
  functional on manual imports alone if this path ever becomes inappropriate.

**Not done, deliberately:** no continuous background polling, no distributed
collection, no attempt to enumerate the full 10,000-result cap on every query.

---

## 4. Instant Buyout

**Investigated. Finding: there is no separate Instant Buyout endpoint.**

"Instant Buyout" is not a distinct listing feed. It is a *sale type* on the same
trade search. The live filter definition at `/api/trade/data/filters` is:

```json
{
  "id": "sale_type",
  "text": "Sale Type",
  "option": {"options": [
      {"id": "any",               "text": "Any"},
      {"id": null,                "text": "Buyout or Fixed Price"},
      {"id": "priced_with_info",  "text": "Price with Note"},
      {"id": "unpriced",          "text": "No Listed Price"}
  ]}
}
```

**Confirmed by testing.** Omitting `sale_type` selects *"Buyout or Fixed Price"* —
precisely the instant-buyout population — and that is what the collector does.
Passing `"priced_with_info"` explicitly was tested and returned **zero** results
for this query, so it is not used.

The price type of each listing (`~b/o` vs `~price`) is stored per row regardless,
because the distinction is useful signal for outlier detection. In the collected
sample, 1344 of 1403 usable listings were `~b/o`.

**No user-triggered browser-capture workflow was needed**, since the endpoint is
directly usable. One is provided anyway
(`src/market/collectors/manual_import.py:JsonExportCollector`) for the case where
that stops being true: the user runs their own search, saves the JSON their own
browser already received, and imports the file. Nothing is fetched on their
behalf.

---

## 5. Currency rates

```
GET https://poe.ninja/poe1/api/economy/exchange/current/overview
    ?league={league}&type=Currency
```

**Confirmed — verified live, 2026-08-24.** One request returns every currency as
`primaryValue` (its chaos price) and `volumePrimaryValue` (chaos traded per
hour). Cross-checked against poe.ninja's own rendered page: Divine Orb read
210.9 / 9668224 against a displayed "211" and "9.7M". Note the old
`poe.ninja/api/data/currencyoverview` path 404s — poe.ninja moved its API under
`/poe1/api/economy/` in mid-2026.

An unrecognised league answers **HTTP 200 with an empty `lines` list** rather
than an error, so the client names that case explicitly instead of reading it as
a market with no data.

**GGG's own `POST /api/trade/exchange/{league}` was tried first and abandoned.**
It returns a live order book, which is a different thing from a rate: on the
exalted book it gave 1, 2, 2.95, 3, 20, 25, 50, 50, 90, 150 chaos, and no
statistic over ten listings distinguishes the real market (~1.25) from the
outliers. Volume is what settles it, and poe.ninja has it.

Rates are stored **timestamped** in `currency_rates` / `app_settings` and are
always overridable by hand in Settings. No exchange rate is hardcoded anywhere as
a permanent constant.

Observed on 2026-08-23: 1 divine ≈ **214.5** chaos.

---

## 6. Writing to stash item notes

**Investigated. Not implemented, deliberately.**

No documented API for setting an item's price note exists in GGG's API reference.
Doing it any other way would mean automating the game client, which GGG's rules
restrict to manually-invoked, single-action macros. This project therefore
produces recommended prices for the user to type in themselves, and provides
copy-ready `~price N divine` strings and a copy-all block.

The application never modifies the game client, the stash, or performs any
gameplay action.

---

## 7. Summary table

| Capability | Status | Mechanism |
|---|---|---|
| Market listings | **Verified working** | `/api/trade/search` + `/fetch`, unauthenticated, rate-limit-obedient |
| Divine/chaos rate | **Verified working** | `/api/trade/exchange`, plus manual override |
| Stat & filter metadata | **Verified working** | `/api/trade/data/*` |
| Instant Buyout subset | **Verified working** | default `sale_type` on the same search |
| Private stash via OAuth | **Blocked by GGG** | new client applications not being accepted; code written but unverified |
| Private stash via POESESSID | **Implemented, unverified live** | user's own session cookie, user's own account |
| Stash JSON / CSV / clipboard import | **Verified working** | no credentials required |
| Writing stash notes | **Not implemented** | no supported API; would require client automation |
