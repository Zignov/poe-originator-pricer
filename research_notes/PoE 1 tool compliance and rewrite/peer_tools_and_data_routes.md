# Peer Tools and Data Routes: How Long-Surviving PoE 1 Tools Stay Compliant, and Which Official Doors Are Open (as of 2026-11-22)

> **Reading note on evidence labels.** Throughout, findings are tagged:
> - **[DOCUMENTED POLICY]** — written in GGG's developer documentation or an official GGG post.
> - **[OBSERVED PRACTICE]** — what a tool demonstrably does. A tool doing something for years without being stopped is **evidence of tolerance, not of permission**.
> - **[SECONDHAND]** — community forum/third-party characterization, not verified against a primary source.
>
> **Two hard blockers found on live sources today, stated up front:**
> 1. GGG's developer API application process is **closed**: "We are currently unable to process new applications." ([Developer Docs](https://www.pathofexile.com/developer/docs)) `service:psapi` is therefore **not obtainable by a new independent developer today**.
> 2. GGG's documentation **does not list a trade API at all** ([Developer Docs index](https://www.pathofexile.com/developer/docs/index)), yet every surviving price-checker queries the trade site. That is the central compliance tension of this whole ecosystem.

---

## Q1. What each tool does technically: data sources, authentication, and what it deliberately avoids

### Takeaway
The survivors cluster into two compliant-by-construction shapes: **offline/no-network planners** (Path of Building) and **clipboard-triggered, one-action-per-keypress price checkers that read aggregate community data plus the trade site** (Awakened PoE Trade, Sidekick, Xiletrade). The tools that died or stalled are the ones that needed *authenticated account-wide data* (Exilence Next, stash overlays) — exactly the category GGG has gated behind a closed OAuth application process. Overlays are universally implemented as **separate processes with their own always-on-top windows fed by the clipboard**, never by reading game memory.

### Cited Findings

**Path of Building Community** — `PathOfBuildingCommunity/PathOfBuilding`
- Alive and very actively maintained: `pushed_at` **2026-11-20T00:19:51Z**, `archived: false`, 5,536 stars, 1,018 open issues — [GitHub API](https://api.github.com/repos/PathOfBuildingCommunity/PathOfBuilding)
- Self-described as an "Offline build planner for Path of Exile" with offence/defence calculations, passive tree planner, skill planner, item planner — [repo](https://github.com/PathOfBuildingCommunity/PathOfBuilding)
- Data ingress is by **import and paste, not by an authenticated API session**: "You can import passive tree, items, and skills from existing characters"; "Can import PathOfExile.com and PoEPlanner.com passive tree links; links shortened with PoEURL.com also work"; items can be added "by copying and pasting them straight into the program!" Builds are shared "by generating a share code." — [repo README](https://github.com/PathOfBuildingCommunity/PathOfBuilding)
- Licence: `NOASSERTION`/"Other" per the GitHub API; the README references MIT with third-party licences in LICENSE.md — [GitHub API](https://api.github.com/repos/PathOfBuildingCommunity/PathOfBuilding); [repo](https://github.com/PathOfBuildingCommunity/PathOfBuilding)
- The README carries **no** GGG-affiliation disclaimer and **no** statement of game-data provenance — [repo](https://github.com/PathOfBuildingCommunity/PathOfBuilding)
- The original `Openarl/PathOfBuilding` was superseded by the Community fork; the Community fork is the live one. **[OBSERVED PRACTICE]**

**Awakened PoE Trade** — `SnosMe/awakened-poe-trade`
- **Alive.** `pushed_at` **2026-11-19T09:36:16Z**, `updated_at` 2026-11-21T11:24:33Z, `archived: false`, 362 open issues, default branch `master` — [GitHub API](https://api.github.com/repos/SnosMe/awakened-poe-trade)
- Description: "Path of Exile app for price checking." Windows & Linux desktop app. Repo topics include `electron` and `vue`; acknowledgments name **libuiohook, RePoE, poeprices.info, and poe.ninja** — [repo](https://github.com/SnosMe/awakened-poe-trade)
- Technical shape: Electron + Vue desktop app with a global hotkey hook (libuiohook), driven by **clipboard item text**: the app works "based on text of item in clipboard," and items the game does not copy to clipboard "can't be price checked" — [FAQ](https://snosme.github.io/awakened-poe-trade/faq)
- Documentation set is explicitly overlay/OCR-aware: the docs site navigation lists "Quick Start, Chat commands, **OCR Guide**, Common issues, FAQ" — [docs](https://snosme.github.io/awakened-poe-trade/)
- **Deliberate avoidance, stated by the developer:** an app that follows the game ToS, "does one server action per button press and doesn't interact with the game client itself" can be considered safe; things to avoid are "injecting into the process, changing the process memory aka cheats" — [FAQ](https://snosme.github.io/awakened-poe-trade/faq) **[OBSERVED PRACTICE + developer's own compliance reasoning]**
- On approval status, the developer is blunt: "There are no approved apps created by community." On bans: "If you get banned, the first action is to Contact Support." — [FAQ](https://snosme.github.io/awakened-poe-trade/faq)
- The README and FAQ do **not** mention POESESSID or OAuth at all — [repo](https://github.com/SnosMe/awakened-poe-trade); [FAQ](https://snosme.github.io/awakened-poe-trade/faq)

**Sidekick** — `Sidekick-Poe/Sidekick` (the live successor organisation)
- **Most actively maintained of the lot.** `pushed_at` **2026-11-22T00:04:42Z** (today), `archived: false`, 516 stars, 40 open issues, MIT — [GitHub API](https://api.github.com/repos/Sidekick-Poe/Sidekick)
- Description/README: "A Path of Exile and Path of Exile 2 companion tool. Price check items, check for dangerous map modifiers, and more!" — **supports PoE 1 and PoE 2 from one codebase** — [repo](https://github.com/Sidekick-Poe/Sidekick)
- Technical shape: a **Blazor web app hosted in a native WebView** — "WebView2 on Windows, WebKitGTK on Linux". Credits **TextCopy** (clipboard) and **SharpHook** (global input hooks). On KDE Wayland it needs input-device control because "Sidekick needs this to copy the item from the game." — [repo](https://github.com/Sidekick-Poe/Sidekick)
- **No authentication whatsoever:** "Sidekick has no login, so anyone who can reach the published port can use it, including your settings." — [repo](https://github.com/Sidekick-Poe/Sidekick)
- Data-source credits name **Path of Exile Trade, poe2scout.com, poe.ninja, poeprices.info, poewiki.net, poedb.tw** — [repo](https://github.com/Sidekick-Poe/Sidekick)
- **Carries GGG's required disclaimer verbatim:** "This product isn't affiliated with or endorsed by Grinding Gear Games in any way." — [repo](https://github.com/Sidekick-Poe/Sidekick)
- Lineage: the original `domialex/Sidekick` was **archived by the owner on 2025-06-16**, now read-only, and points users to the `Sidekick-Poe` repo — [search result citing GitHub](https://github.com/domialex/Sidekick)

**Exilence Next** — `viktorgullmark/exilence-next`
- **Effectively unmaintained.** `pushed_at` **2023-05-23T12:14:27Z** — no code pushed in ~3.5 years. `archived: false`, `disabled: false`, 96 open issues. Description: "Desktop application that helps you summarize your wealth in Path of Exile" — [GitHub API](https://api.github.com/repos/viktorgullmark/exilence-next)
- The predecessor `viktorgullmark/exilence` was **archived by the owner on 2023-05-01** and is read-only; the maintainer stated it was replaced by Exilence Next — [releases page](https://github.com/viktorgullmark/exilence/releases)
- **Auth model: OAuth via a hosted backend**, not POESESSID. Its backend is at `next.exilence.app`. When users hit auth failures after GGG's POESESSID announcement, the thread answer was that "Exilence Next works on OAuth and should not have been affected." — [PoE forum thread 3332267](https://www.pathofexile.com/forum/view-thread/3332267) **[SECONDHAND — community forum, 2023-era]**
- Community reports describe a third party taking over maintenance at some point, with a 2024 reply confirming a fix worked for one user — [PoE forum thread 3332267](https://www.pathofexile.com/forum/view-thread/3332267) **[SECONDHAND]**
- **Significance:** Exilence Next is the *one* tool in this set that did the officially sanctioned thing — confidential OAuth client with a server-side backend to read `account:stashes` — and it is the one that has stalled. That is directly relevant to the rewrite decision.

**PoE Overlay** — `kyusung4698/PoE-Overlay`
- **Dead as a standalone project.** `pushed_at` **2022-04-16T03:01:33Z**, description "An Overlay for Path of Exile. Built with Overwolf and Angular." — [GitHub API](https://api.github.com/repos/kyusung4698/PoE-Overlay)
- The repo ships a `DEPRECATED.md` telling users the version they have "is deprecated and may stop working after a major Path of Exile update," that the project moved to a **new Overwolf-based version**, and to uninstall and reinstall from the Overwolf Store — [DEPRECATED.md](https://raw.githubusercontent.com/kyusung4698/PoE-Overlay/master/DEPRECATED.md)
- The Overwolf 1.0 rewrite added a market module (item search and bulk exchange), replay module, inspect module, Vulkan support and native fullscreen support, while **removing** stash navigation/highlighting and stash price tagging — [DEPRECATED.md](https://raw.githubusercontent.com/kyusung4698/PoE-Overlay/master/DEPRECATED.md)
- Data sources credited: **poe.ninja for market statistics, poeprices.info for item price prediction, libggpk for parsing PoE data**. Overwolf is credited as the "Overlay Platform." No POESESSID or login mentioned. — [repo](https://github.com/kyusung4698/PoE-Overlay)
- **Overlay implementation is the interesting part:** rather than hand-rolling a click-through window, it migrated to **Overwolf**, a sanctioned third-party in-game overlay SDK. The Overwolf store page describes it as "the original trade companion for Path of Exile, trusted by thousands of exiles since 2020" with price checks using "live data from the official trade site" — [Overwolf app page](https://www.overwolf.com/app/kyusung4698-poe_overlay)
- Conflict to flag: the Overwolf listing reportedly showed reviews dated November 2026, suggesting the Overwolf build still has users, while the GitHub repo has had no pushes since April 2022 — [Overwolf app page](https://www.overwolf.com/app/kyusung4698-poe_overlay); [GitHub API](https://api.github.com/repos/kyusung4698/PoE-Overlay). Treat the Overwolf build's maintenance status as **unverified**.
- A separate fork, `wratho/PoE-Overlay-Community-Fork`, "is a fork of the original PoE Overlay that was snapshotted on 2020-06-10, before it was converted to utilizing Overwolf" — [fork repo](https://github.com/wratho/PoE-Overlay-Community-Fork). It includes a stash grid overlay, and some features require signing in to a PoE account through the tool's settings — [search result citing the fork](https://github.com/wratho/PoE-Overlay-Community-Fork)

**poe.ninja**
- Aggregate economy site. Its own API documentation page at `https://poe.ninja/docs/api` and its Swagger page at `https://poe.ninja/swagger/index.html` **both returned HTTP 403 Forbidden to automated fetching** on 2026-11-22. Verified directly. **This is itself a compliance signal — their edge actively blocks non-browser clients.**
- Base URL and endpoint shape are documented only by the community: `https://poe.ninja/api/data/` with `/currencyoverview` (types `Currency`, `Fragment`) and `/itemoverview` (types including `Oil`, `Incubator`, `Scarab`, `Fossil`, `Resonator`, and newer ones such as `Memory`, `Coffin`, `Allflame Ember`), taking required `league` and `type` plus optional `language` (default `en`) — [community API doc](https://github.com/ayberkgezer/poe.ninja-API-Document/blob/main/README.md); [2025 community guide](https://github.com/Davenads/poeninjaAPI-2025); [5k-mirrors doc](https://github.com/5k-mirrors/misc-poe-tools/blob/master/doc/poe-ninja-api.md) **[OBSERVED PRACTICE — the API is undocumented-but-used]**
- How poe.ninja itself gets the data: it is a public-stash-tab-API consumer. **[GAP — not verified on a live primary source in this session; see Gaps.]**

**PoE Antiquary**
- Described in community indexes as a historic price-data site; a PoEDB tools page calls it "an index of older league price data from poe.ninja" — [awesome-poe](https://github.com/5k-mirrors/awesome-poe); [PoEDB Tools](https://poedb.tw/us/Tools)
- `poe-palette` can open an item's previous-league price history in PoE Antiquary (opt-in) — [poe-palette](https://github.com/flbraun/poe-palette)
- Site address `poe-antiquary.xyz`. A forum thread reports it "stopped collecting data in Necropolis" and does not have standard-league prices; the same thread points to **poe.ninja's data dumps** as the alternative, which reportedly go back to Essence League — [PoE forum thread 3867005](https://www.pathofexile.com/forum/view-thread/3867005) **[SECONDHAND — user reports, not confirmed on the site itself]**

**"PoE Stash Tab Overlay"**
- I could **not** identify a single canonical project under that exact name. The closest POESESSID-based stash-overlay projects found were: `darvid/stashvision` (stash indexing with in-game highlighting; config file takes your POESESSID; overlay requires windowed-borderless and probably English client; highlights one tab at a time), `azra1l/poe_qolV2` (account name + POESESSID in `Setup.ini`), `CLOUGH/poe-recipe` (Electron app that **reads POESESSID out of your browser cookies**), and `wratho/PoE-Overlay-Community-Fork` (stash grid overlay, in-tool account sign-in) — [stashvision](https://github.com/darvid/stashvision); [poe_qolV2](https://github.com/azra1l/poe_qolV2); [poe-recipe](https://github.com/CLOUGH/poe-recipe); [PoE-Overlay-Community-Fork](https://github.com/wratho/PoE-Overlay-Community-Fork). Also `Zyertdox/EnhancePoEApp` appeared in this cluster — [EnhancePoEApp](https://github.com/Zyertdox/EnhancePoEApp)
- A community guide page exists purely to teach users how to extract their POESESSID from browser devtools, while warning that the value identifies your account and should never be shared — [cptpingu poe-stash POESESSID guide](https://cptpingu.github.io/poe-stash/poesessid.html); [cptpingu/poe-stash](https://github.com/cptpingu/poe-stash)

**De-facto standard price checker in 2026 (PoE 1)**
- Two credible live candidates, both pushed within the last three days:
  - **Awakened PoE Trade** — pushed 2026-11-19, still PoE 1 focused — [GitHub API](https://api.github.com/repos/SnosMe/awakened-poe-trade)
  - **Sidekick** — pushed 2026-11-22, covers PoE 1 **and** PoE 2 — [GitHub API](https://api.github.com/repos/Sidekick-Poe/Sidekick)
- **Xiletrade** is a third live option: a free open-source price-check overlay for PoE 1 and PoE 2 on Windows, default hotkey Ctrl+D, with prices "based on the official trade websites" — [Xiletrade site](https://maxensas.github.io/xiletrade/)
- **PoE 2 only, mentioned because it informs the PoE 1 picture:** `Kvan7/Exiled-Exchange-2` is a fork of Awakened PoE Trade targeting PoE 2, and Awakened PoE Trade does **not** support PoE 2 — development stayed on PoE 1 — [Exiled Exchange 2 fan page](https://exiledexchange2.net/awakened-poe-trade-poe-2/) **[SECONDHAND — the site self-identifies as a fan site using "unofficial successor" framing]**. The practical consequence for PoE 1: APT remained a PoE-1 tool rather than splitting its attention, which is part of why it is still alive.

### Inferences
- The **clipboard + global hotkey + separate-process window** architecture is the load-bearing compliance decision shared by every surviving price checker. It satisfies GGG's written macro and screen/clipboard-reading rules (see Q2) almost by accident of design: one keypress, one copy, one query.
- Nobody in the live set reads game memory or injects into the process, and the APT developer names those explicitly as the line not to cross. The survivors' common trait is that **they treat the game client as untouchable and the website as the only interface**.
- The tools that needed account-scoped authenticated data (wealth trackers, stash overlays) are precisely the dead/stalled ones. With the OAuth application process closed, that category is currently a dead end for new entrants regardless of intent.
- Sidekick's "no login" line is a deliberate scope choice, not an oversight: by never holding credentials it eliminates the entire POESESSID risk class that GGG warned about.

### Gaps
- Exact last-commit dates are from the GitHub API `pushed_at` field, which reflects pushes to any branch; I did not open commit logs per repo.
- Whether Path of Building Community's character import uses POESESSID, an unauthenticated public character endpoint, or OAuth is **not stated in its README** and I did not read its source. This is an important unresolved detail for the rewrite.
- No canonical "PoE Stash Tab Overlay" project identified; the named candidates above are my best match and should be treated as a cluster, not an identification.
- PoE Antiquary's current liveness is unverified — I did not reach `poe-antiquary.xyz` directly.

---

## Q2. POESESSID vs OAuth vs unauthenticated: who uses what, and has anyone migrated away?

### Takeaway
**GGG publicly told users in December 2022 not to give POESESSID to third-party tools and pointed at OAuth as the secure alternative** — a documented, quotable policy moment. Every tool in the live 2026 set responded not by migrating to OAuth but by **dropping the need for authentication entirely**: the surviving price checkers use no credentials at all. The one tool that did migrate to OAuth properly (Exilence Next) is the one that has stopped moving.

### Cited Findings
- **[DOCUMENTED POLICY — official GGG post]** "Do not share POESESSID values with other people," posted by **Community_Team (Grinding Gear Games) on 14 December 2022, 3:20:40 AM**. Key quotes: "Some third-party tools ask that you give them your Path of Exile website session cookie"; "This cookie value gives the recipient almost complete access to your Path of Exile account on the website"; "there is nothing to stop someone updating them in the future to harvest credentials"; and the remedy: "The secure way of granting tools access to your data is via OAuth." — [PoE forum announcement 3328601](https://www.pathofexile.com/forum/view-thread/3328601)
- In the same post GGG states that **sharing login information violates its Terms of Use**, that tools storing credentials locally often do so insecurely, that the cookie permits spending points and posting on the forums as you, and that logging out and back in on pathofexile.com invalidates previously shared session cookies. GGG also said it supports OAuth on its officially documented API endpoints and was "working to extend this to resources like the trade website." — [PoE forum announcement 3328601](https://www.pathofexile.com/forum/view-thread/3328601)
- **[DOCUMENTED POLICY]** Public (non-confidential) OAuth clients "Cannot use any `service:*` scopes"; service scopes require "a confidential client with the `client_credentials` grant type," where a confidential client is one "backend by a secure server controlled by the application owner." Client-credentials tokens "have their identity set to the registered owner of the application." — [Authorization docs](https://www.pathofexile.com/developer/docs/authorization)
- **[DOCUMENTED POLICY]** GGG "do not support the implicit grant or the PKCE (proof key for code exchange) extension," and "a distributed app will need to be backed by a secure server in order to keep your application credentials secret" — quoted from GGG's developer docs via a forum discussion — [PoE forum thread 3332267](https://www.pathofexile.com/forum/view-thread/3332267)
- **Uses OAuth:** Exilence Next, via its own hosted backend at `next.exilence.app` — [PoE forum thread 3332267](https://www.pathofexile.com/forum/view-thread/3332267) **[SECONDHAND]**
- **Uses no authentication at all:** Sidekick — "Sidekick has no login" — [repo](https://github.com/Sidekick-Poe/Sidekick); Awakened PoE Trade — no POESESSID or OAuth mention anywhere in README or FAQ — [repo](https://github.com/SnosMe/awakened-poe-trade), [FAQ](https://snosme.github.io/awakened-poe-trade/faq); Path of Building Community — import/paste only, offline planner — [repo](https://github.com/PathOfBuildingCommunity/PathOfBuilding)
- **Still uses POESESSID (the stash-overlay cluster):** `darvid/stashvision` takes POESESSID in its config file; `azra1l/poe_qolV2` takes account name + POESESSID in `Setup.ini`; `CLOUGH/poe-recipe` reads POESESSID straight out of browser cookies — [stashvision](https://github.com/darvid/stashvision); [poe_qolV2](https://github.com/azra1l/poe_qolV2); [poe-recipe](https://github.com/CLOUGH/poe-recipe)
- **[DOCUMENTED POLICY]** `account:stashes` and `account:characters` scopes exist, so a *sanctioned* route to stash/character data does exist on paper — but it needs a registered application, and registration is closed (Q4) — [Authorization docs](https://www.pathofexile.com/developer/docs/authorization); [Developer Docs](https://www.pathofexile.com/developer/docs)

### Inferences
- The December 2022 POESESSID post is the single most citable compliance document in this space, and it is the reason a 2026 tool that asks for POESESSID is **indefensible by design**, not merely risky: GGG has said in writing that sharing login information violates the ToU.
- The industry's actual migration path was **not POESESSID → OAuth**. It was **POESESSID → no auth at all**, by narrowing feature scope to things that need only public data. That is a strategic lesson: features requiring account scope are the ones that got abandoned.
- Because GGG refuses PKCE and the implicit grant, a *distributed desktop app* cannot hold OAuth credentials securely without the author running a server. That architectural requirement, plus closed registration, makes OAuth practically unreachable for a hobbyist desktop tool in 2026.

### Gaps
- GGG said in Dec 2022 it was "working to extend" OAuth "to resources like the trade website." I found **no evidence either way** that OAuth-authenticated trade-site access shipped by November 2026 — the developer docs index still lists no trade API.
- I did not verify in Exilence Next's source which scopes it requests, nor whether its OAuth client is still registered and functioning in Nov 2026.

---

## Q3. Trade API directly vs poe.ninja/aggregate, and documented rate-limiting discipline

### Takeaway
The survivors do **both**, and the split is functional: **unique/currency/bulk prices come from aggregates (poe.ninja, poeprices.info)** while **rare-item price checks hit the official trade site live**. The rate-limiting discipline is almost entirely **header-driven and reactive** rather than a published numeric budget — and the official docs explicitly make the numbers non-contractual.

### Cited Findings
- **[DOCUMENTED POLICY]** "In order to protect our services we employ the use of rate limits on the majority of our endpoints"; "These limits are dynamic and can change at any time depending on our requirements." Applications must parse the `X-Rate-Limit-*` and `Retry-After` response headers. Penalty: "Exceeding these limits frequently will result in your application access being revoked." — [Developer Docs index](https://www.pathofexile.com/developer/docs/index)
- **[DOCUMENTED POLICY]** Every app using the API must send an identifiable User-Agent in the form `User-Agent: OAuth {$clientId}/{$version} (contact: {$contact}) ...` — [Developer Docs](https://www.pathofexile.com/developer/docs)
- Rate-limit header semantics (community-sourced): `X-Rate-Limit-Policy` names the policy and `X-Rate-Limit-Rules` lists which rules apply, "Common rules are: ip, account, and client" — [hproxy write-up](https://hproxy.com/blog/proxies-for-path-of-exile); [poe-ninja-build-cost README](https://github.com/yaredmax/poe-ninja-build-cost) **[SECONDHAND for the header list; the "ip, account, client" phrasing is attributed to GGG's own docs by the second source]**
- Older community-reported trade-search numbers: the `X-Rate-Limit-{Method}` value is "a comma delimited list (,) of rate limiting rules, which themselves are delimited by colons (:)", with an example of a 60-second limit after more than 20 requests in 5 seconds — [PoE forum thread 2079853](https://www.pathofexile.com/forum/view-thread/2079853) **[SECONDHAND and ~2018-era; almost certainly stale]**
- **Rate limits are charged to the user, not the tool.** A GGG staffer is quoted as clarifying that rate-limiting "does not apply to your use of the official website, or to your use of third-party tools: it applies to YOU — regardless of what you use," and that being limited on one channel can lock you out of all of them — [PoE forum thread 3801062](https://www.pathofexile.com/forum/view-thread/3801062) **[SECONDHAND — staff statement reported via search, I did not read the post directly]**
- GGG staff troubleshooting advice when trade search misbehaves, from **Lisa_GGG, 22 February 2021**: "If you are using any 3rd party Path of Exile tools can you please disable these" — [PoE forum thread 3056323](https://www.pathofexile.com/forum/view-thread/3056323)
- **Aggregate consumers:** Awakened PoE Trade credits poe.ninja and poeprices.info — [repo](https://github.com/SnosMe/awakened-poe-trade). PoE Overlay used "poe.ninja for market statistics" and "poeprices.info for item price prediction" — [repo](https://github.com/kyusung4698/PoE-Overlay). Sidekick credits Path of Exile Trade, poe2scout.com, poe.ninja and poeprices.info — [repo](https://github.com/Sidekick-Poe/Sidekick)
- **Live trade-site consumers:** PoE Overlay's Overwolf build price checks "using live data from the official trade site" — [Overwolf app page](https://www.overwolf.com/app/kyusung4698-poe_overlay). Xiletrade's prices are "based on the official trade websites" — [Xiletrade](https://maxensas.github.io/xiletrade/)
- poe.ninja's own concurrency expectations, reported by a developer reading their docs: a descriptive User-Agent and "reasonable concurrency," with misbehaving clients blocked — [poestash-workers PR #33](https://github.com/sdelanos/poestash-workers/pull/33) **[SECONDHAND]**
- **Conflicting poe.ninja rate-limit figures:** a 2025 community document lists **12 requests per 5 minutes** — [Davenads/poeninjaAPI-2025](https://github.com/Davenads/poeninjaAPI-2025); a 2026 PR instead describes capping at **at most 2 requests in flight**, retrying a 429 after its `Retry-After` at most twice — [poestash-workers PR #33](https://github.com/sdelanos/poestash-workers/pull/33); a separate developer observed poe.ninja returning 429 on whole leagues roughly one run in three when sending ~68 concurrent requests — [poestash-workers PR #33](https://github.com/sdelanos/poestash-workers/pull/33). **These do not reconcile; treat all three as unofficial.**
- Rare-item pricing quality caveat from users: price checks for rares "are not very reliable outside of uniques and currencies" — [Steam discussion](https://steamcommunity.com/app/238960/discussions/0/1635292137574610206) **[SECONDHAND]**

### Inferences
- The only *documented* rate-limit discipline that exists is "read the headers, obey `Retry-After`, send an identifying User-Agent." Any tool that hardcodes numeric budgets is building on sand, because GGG reserves the right to change them at any time.
- Because the limit is charged to the **user's account/IP**, a price-checker that burns the budget doesn't just risk its own access — it breaks the player's ability to use the official trade site, which is the mechanism that generates GGG support tickets and therefore GGG's irritation. **A new tool's single highest-value compliance investment is a shared, header-aware rate-limit governor across every outbound call.**
- Lisa_GGG's 2021 "please disable these" is not an accusation, but it does show GGG's internal posture: third-party tools are the first thing support asks you to turn off.

### Gaps
- I could not reach poe.ninja's official docs page (403 to automated clients), so **poe.ninja's actual published rate limits as of Nov 2026 are unverified**. Every number above is secondhand and mutually inconsistent.
- I found **no** published numeric rate-limit table for the trade search endpoint in current GGG documentation, because the trade endpoint is not in the documentation at all.
- No tool in the live set publishes its own rate-limiting design document that I could find.

---

## Q4. THE PUBLIC STASH TAB API: is `service:psapi` obtainable in November 2026?

### Takeaway
**No. The application process is closed.** GGG's developer documentation states plainly, as of 2026-11-22, "We are currently unable to process new applications." `service:psapi` additionally requires a confidential OAuth client with the `client_credentials` grant — i.e. a server-side application — which a public desktop client is explicitly forbidden from having. **For an independent hobbyist developer today, `/public-stash-tabs` is not an available data source.** Existing holders (poe.ninja and similar) are grandfathered; new entrants are not.

### Cited Findings
- **[DOCUMENTED POLICY — the decisive quote]** "We are currently unable to process new applications." — [Developer Docs](https://www.pathofexile.com/developer/docs)
- **[DOCUMENTED POLICY]** Endpoint: `GET /public-stash-tabs[/<realm>]`, optional realm `xbox` or `sony` (omitted = PoE 1 PC), optional `id=` pagination code. **Required scope: `service:psapi`.** It returns a stream of public stashes across all leagues with a `next_change_id` and a `stashes` array; unlisted stashes keep only `id` and `public`. "There is currently a 5-minute delay on results using this API." "If the stashes array is empty then you have reached the end of the stream." Marked **PoE 1 only**. — [API Reference](https://www.pathofexile.com/developer/docs/reference)
- **[DOCUMENTED POLICY]** `service:psapi` is "for access to the Public Stash API" and, like all `service:*` scopes, "Requires a confidential client with the `client_credentials` grant type." Public clients "Cannot use any `service:*` scopes." — [Authorization docs](https://www.pathofexile.com/developer/docs/authorization)
- **[DOCUMENTED POLICY]** What registration requires of an applicant: an understanding of OAuth, knowledge of the available client and grant types, and confirmation that the documented scopes cover the resources needed. Existing applications are managed from the account's applications page. **The docs describe no approval criteria, no queue, no eligibility tiers and no timeline** — only that new applications cannot currently be processed. — [Developer Docs](https://www.pathofexile.com/developer/docs); [Authorization docs](https://www.pathofexile.com/developer/docs/authorization)
- **[DOCUMENTED POLICY]** "We reserve the right to remove access to any or all of our APIs and services without notice at our discretion." — [Developer Docs index](https://www.pathofexile.com/developer/docs/index)
- Historically the public stash endpoint was unauthenticated; community wiki documentation of the older open version describes it as containing no historical data and always returning current stash state — [PoE Wiki: Public stash tab API](https://pathofexile.fandom.com/wiki/Public_stash_tab_API). The move to a required OAuth service scope is what closed that door.
- For PoE 2 context only: a forum post reports "still no Stash API" for PoE 2 — [PoE forum thread 3657474](https://www.pathofexile.com/forum/view-thread/3657474) **[SECONDHAND; the search result's own date metadata was self-contradictory, so treat the date as unreliable]**

### Inferences
- The psapi door is closed in **two independent ways**: the registration freeze, and the confidential-client requirement. Even if registration reopened tomorrow, a distributed desktop tool could not use the scope without the author operating a server that holds the client secret and relays data.
- This explains the structure of the whole ecosystem: because only grandfathered holders can read the firehose, **poe.ninja's aggregate output is effectively a monopoly utility** that every downstream tool depends on.
- A new tool that wanted the firehose would have to either (a) consume someone else's derived aggregate, or (b) do its own trade-site querying per item. There is no third option available to a new developer.

### Gaps
- I found **no** public statement from GGG explaining *why* applications are not being processed, how long the freeze has lasted, or whether partners are being onboarded through a private channel. The docs simply state the freeze.
- I could not confirm whether any currently-approved application was granted psapi recently, so I cannot say whether "closed" means "closed to everyone" or "closed except private arrangements." **Report this as: documented as closed, with no documented exception process.**

---

## Q5. Is there an official Currency Exchange API, and is it open?

### Takeaway
**Yes, and this is the one genuinely good piece of news for a new developer: it is officially documented AND publicly accessible without OAuth.** It provides hourly historical digests of aggregate trade volume and price ratios per market per league. The trade-off is that it is *purely historical* and hourly — no current-hour data — so it is an economy-trend source, not a live price-check source.

### Cited Findings
- **[DOCUMENTED POLICY]** Endpoint: `GET https://web.poecdn.com/api/currency-exchange[/<realm>][/<id>]`. Optional realm `xbox`, `sony` or `poe2` (omitted = PoE 1 PC). Optional `id` is a Unix timestamp from a previous response; omitted returns the first hour of history. The section is labelled **"Public API"** and **no required scope is listed**. — [API Reference](https://www.pathofexile.com/developer/docs/reference)
- **[DOCUMENTED POLICY]** Returns hourly digests of aggregate trade history across **all leagues**. Each active market in the requested hour includes: league, market ID and pair, volume traded, lowest and highest stock, and lowest and highest ratio. — [API Reference](https://www.pathofexile.com/developer/docs/reference)
- **[DOCUMENTED POLICY]** Pagination: when `next_change_id` matches the timestamp you sent, you have reached the current end, and you should wait until the next hourly boundary before calling again. — [API Reference](https://www.pathofexile.com/developer/docs/reference)
- **[DOCUMENTED POLICY]** "the responses from this endpoint are purely historical" — current-hour data is not available, and old history entries may be removed in the future. — [API Reference](https://www.pathofexile.com/developer/docs/reference)
- **[DOCUMENTED POLICY]** A `service:cxapi` scope exists "for access to the Currency Exchange API," but the docs note "this API can also be accessed publicly," so the scope is not needed for every use. — [Authorization docs](https://www.pathofexile.com/developer/docs/authorization)
- The Currency Exchange API is listed in the official docs index alongside Public Stashes — [Developer Docs index](https://www.pathofexile.com/developer/docs/index)

### Inferences
- `web.poecdn.com` rather than `api.pathofexile.com` tells you this is served as static-ish CDN content, consistent with hourly pre-baked digests. That is a strong signal GGG intends it to be hammered by many clients — the cheapest possible thing for them to serve.
- For a pricer, this is the **defensible backbone for currency and bulk-exchange valuation**: officially documented, officially public, no credentials, no scope, explicit polling guidance (hourly boundary). It cannot price a rare item, and it lags by at least an hour.
- The existence of `service:cxapi` alongside public access suggests the authenticated variant may offer higher limits or finer data, but the docs as fetched do not say what the difference is.

### Gaps
- The API Reference has a Rate Limits section whose content did not come through in my fetch, so **I cannot state the Currency Exchange rate limits**. This should be checked directly before building.
- What `service:cxapi` grants *beyond* public access is not documented in what I retrieved.
- Whether the Currency Exchange API covers only the in-game Currency Exchange market (bulk currency trading) or also item markets is only partly clear — it refers to "each active market... with its league, market ID and pair," which reads as currency pairs.

---

## Q6. Community-run aggregate APIs: are they explicitly open for third-party use?

### Takeaway
**None of them is explicitly, publicly open with documented terms that I could verify.** poe.ninja's API is the classic undocumented-but-universally-used endpoint: its own documentation pages now **actively 403 automated clients**, its rules reach developers only secondhand, and the reported rules include closing non-economy endpoints to third parties. This is the weakest link in the whole dependency chain — every live tool credits poe.ninja, and none of them can point to a licence.

### Cited Findings
- **Verified directly on 2026-11-22:** `https://poe.ninja/docs/api` returned **HTTP 403 Forbidden** and `https://poe.ninja/swagger/index.html` returned **HTTP 403 Forbidden** to automated fetching. The documentation exists but is not machine-readable by non-browser clients.
- Reported rules from a developer who read poe.ninja's docs: the docs **close the builds/profiles API and every non-economy endpoint to third parties**, and block clients that misbehave; they ask for a descriptive User-Agent and reasonable concurrency — [poestash-workers PR #33](https://github.com/sdelanos/poestash-workers/pull/33) **[SECONDHAND]**
- Conflicting rate-limit reports: 12 requests / 5 minutes — [Davenads/poeninjaAPI-2025](https://github.com/Davenads/poeninjaAPI-2025); vs. ≤2 concurrent with `Retry-After`-respecting retries capped at two — [poestash-workers PR #33](https://github.com/sdelanos/poestash-workers/pull/33). **Unreconciled.**
- Third-party wrappers disclaim official status: one states it "isn't an official poe.ninja API," and that poe.ninja has its own usage terms which "may change without notice" — [parse.bot listing](https://parse.bot/marketplace/c5021715-7468-40f7-b591-c724ce322558/poe-ninja-api) **[SECONDHAND — a commercial marketplace listing, low authority]**
- Community-documented endpoint surface (base `https://poe.ninja/api/data/`): `/currencyoverview?league=&type=Currency|Fragment&language=en` and `/itemoverview?league=&type=...` with types spanning Oil, Incubator, Scarab, Fossil, Resonator, UniqueMap, Essence, Memory, Coffin, Allflame Ember — [ayberkgezer doc](https://github.com/ayberkgezer/poe.ninja-API-Document/blob/main/README.md); [Davenads 2025](https://github.com/Davenads/poeninjaAPI-2025); [JakubRak guide](https://github.com/JakubRak-gamedev/poeninja_API_guide); [5k-mirrors doc](https://github.com/5k-mirrors/misc-poe-tools/blob/master/doc/poe-ninja-api.md)
- poe.ninja also publishes **bulk data dumps** going back to Essence League, recommended in a community thread as the historical-price source of record now that PoE Antiquary reportedly stalled — [PoE forum thread 3867005](https://www.pathofexile.com/forum/view-thread/3867005) **[SECONDHAND]**
- **poe.watch:** I found **no evidence of its current status either way**. A targeted search for a 2026 shutdown returned nothing about the site at all. **Report as status unknown — do not assume it is alive.**
- **poe2scout:** credited as a data source by Sidekick alongside poe.ninja — [Sidekick repo](https://github.com/Sidekick-Poe/Sidekick). Named `poe2scout.com`, i.e. a **PoE 2** aggregate; relevant to PoE 1 only as evidence that the community rebuilt a parallel aggregate for the new game, and that tools happily depend on undocumented community aggregates.
- **poeprices.info:** credited by Awakened PoE Trade ("item price prediction") and PoE Overlay and Sidekick — [APT repo](https://github.com/SnosMe/awakened-poe-trade); [PoE Overlay repo](https://github.com/kyusung4698/PoE-Overlay); [Sidekick repo](https://github.com/Sidekick-Poe/Sidekick). It is the de-facto ML rare-item pricer that tools call rather than reimplement. **[GAP — I did not retrieve its terms.]**

### Inferences
- The 403s on poe.ninja's own API documentation are significant: an operator that wanted broad programmatic use would not gate the docs. Combined with the reported closure of non-economy endpoints, the fair reading is **"economy endpoints tolerated, everything else not, no written permission, subject to change without notice."**
- Because every live tool credits poe.ninja in an acknowledgments list rather than citing a licence or API agreement, the ecosystem norm is **attribution-as-goodwill**, not a legal arrangement. That is exactly the "tolerance, not permission" pattern.
- The dependency graph is uncomfortably concentrated: poe.ninja holds the grandfathered psapi feed, publishes the only broad economy aggregate, and gates its own docs. A tool built solely on poe.ninja has one point of failure that it has no agreement with.

### Gaps
- **poe.ninja's actual current terms, rate limits, and permitted-use statement are unverified** — the authoritative page blocks automated fetching. Someone should open `https://poe.ninja/docs/api` in a real browser and record it verbatim. This is the single most important follow-up.
- poe.watch's existence/status in Nov 2026: unresolved.
- poeprices.info's terms, rate limits and liveness: not retrieved.
- PoE Antiquary's current data coverage: unverified.

---

## Q7. Has any of these tools been asked by GGG to change behaviour, lost access, or published a compliance decision?

### Takeaway
I found **no documented instance of GGG ordering a specific named tool in this set to change its behaviour or revoking its access.** What exists instead is (a) GGG's generic written policy, (b) one GGG post aimed at the POESESSID *practice* rather than at a named tool, and (c) developers publishing their own compliance reasoning pre-emptively. The compliance playbook is **self-imposed**, derived from reading GGG's policy text, not negotiated.

### Cited Findings
- **The closest thing to a GGG intervention** is the Dec 2022 POESESSID announcement, which targets a practice ("Some third-party tools ask that you give them your Path of Exile website session cookie") without naming tools, states that sharing login information violates the ToU, and directs tools to OAuth — [PoE forum announcement 3328601](https://www.pathofexile.com/forum/view-thread/3328601)
- **A developer's published compliance decision (Awakened PoE Trade):** the FAQ states there are "no approved apps created by community," and lays out the author's own safety test — follows the game ToS, "does one server action per button press and doesn't interact with the game client itself" — while naming the prohibited behaviours as "injecting into the process, changing the process memory aka cheats" — [APT FAQ](https://snosme.github.io/awakened-poe-trade/faq)
- **A published compliance artefact (Sidekick):** the README carries GGG's required wording, "This product isn't affiliated with or endorsed by Grinding Gear Games in any way." — [Sidekick repo](https://github.com/Sidekick-Poe/Sidekick)
- **A compliance-driven feature removal (PoE Overlay):** the Overwolf 1.0 rewrite **removed stash navigation and highlighting, and stash price tagging** — [DEPRECATED.md](https://raw.githubusercontent.com/kyusung4698/PoE-Overlay/master/DEPRECATED.md). The document gives no reason, so I cannot attribute this to GGG pressure. **[OBSERVED PRACTICE — the removal is a fact; the motive is not documented. Do not assert GGG caused it.]**
- **GGG support posture toward third-party tools**, Lisa_GGG, 22 Feb 2021: "If you are using any 3rd party Path of Exile tools can you please disable these" — [PoE forum thread 3056323](https://www.pathofexile.com/forum/view-thread/3056323)
- **An unflattering characterization of GGG's attitude, clearly not official:** a January 2026 forum thread requesting a public trade API reports that "GGG detests those tools. They lead to people hammering the API, getting rate-limited, and contacting Support saying 'not even the official trade website works.'" — [PoE forum thread 3901902](https://www.pathofexile.com/forum/view-thread/3901902) **[SECONDHAND — this is a forum user's characterization, NOT a GGG statement. Must be labelled as such in any writeup.]**
- **[DOCUMENTED POLICY — the enforcement clauses that matter]**, all from [Developer Docs](https://www.pathofexile.com/developer/docs) and [Developer Docs index](https://www.pathofexile.com/developer/docs/index):
  - Apps that interact with the game or its files violate the Terms of Use and lead to **immediate account termination**.
  - Macros must be **manually invoked, perform one action each**, and cannot be automated or cycle between actions.
  - Apps that read the screen, clipboard, or logs must **keep the user aware of what is being read, and only read once per manual key press**.
  - You must not imply official affiliation; public apps should include a notice such as "This product isn't affiliated with or endorsed by Grinding Gear Games in any way."
  - **Commercial use of GGG intellectual property is generally not allowed.**
  - Websites and web apps "carry the least risk for users"; **standalone executables are permitted but discouraged**, and if they use the API they must use a **public** OAuth client (which, per Q2, bars `service:*` scopes).
  - Ordinarily only applications that "run completely separate from the game" are allowed.
  - "Requests for access to any other internal website APIs or in-game resources will be denied," and **reverse-engineering undocumented endpoints violates the Terms of Use**.
  - "We reserve the right to remove access to any or all of our APIs and services without notice at our discretion."

### Inferences
- **The most load-bearing compliance rule for a price-checker is the screen/clipboard clause**: read once per manual key press, and keep the user aware of what is read. Every surviving price checker's hotkey-and-clipboard architecture is a direct implementation of that sentence, whether or not the authors describe it that way.
- **The most dangerous written clause for the whole ecosystem** is "reverse-engineering undocumented endpoints violates the Terms of Use," read against the fact that the trade search API is undocumented. By the letter of the policy, *every live price-checker is non-compliant*, and has been for years, without consequence. That is the clearest possible example of tolerance rather than permission — and it should be stated in exactly those terms.
- GGG's complaint mechanism is load, not principle: the only documented penalty trigger is "exceeding these limits frequently." A tool that is quiet survives; a tool that is noisy gets noticed. Compliance here is mostly a rate-limiting engineering problem wearing a legal costume.
- "Commercial use of their intellectual property is generally not allowed" is worth flagging: APT runs a Patreon ([docs nav](https://snosme.github.io/awakened-poe-trade/)) and has not been stopped — again tolerance, not permission.

### Gaps
- No GGG post naming any of these tools in an enforcement context was found. I cannot confirm that no such post exists; I can only say I did not find one.
- PoE Overlay's removal of stash features is unexplained in its own documentation. Attributing it to GGG would be speculation.
- I did not read project Discord announcements or changelogs (not reachable without authentication), which is where compliance decisions are most likely discussed informally. **This is a real coverage gap for Q7.**

---

## Q8. Which data source does a new PoE 1 tool realistically build on in 2026?

### Takeaway
A three-layer stack, in descending order of defensibility: **(1) the official Currency Exchange public API** — documented, open, no auth, hourly historical, the only fully sanctioned bulk price source available to a new developer; **(2) the official trade site, queried live, one request per manual keypress, strictly header-rate-limited** — undocumented and therefore formally non-compliant but universally tolerated and the only way to price a specific rare; **(3) poe.ninja economy endpoints as a cache/fallback** — undocumented, operator-gated, attribution-by-goodwill. The **public stash tab API is unavailable** and should be struck from the design. Account-scoped features (wealth tracking, stash overlays) should be considered **out of scope** because the OAuth route that legitimizes them is closed.

### Cited Findings
- Officially sanctioned and open: Currency Exchange, `GET https://web.poecdn.com/api/currency-exchange`, labelled "Public API," no scope listed, hourly digests, explicit guidance to wait for the next hourly boundary — [API Reference](https://www.pathofexile.com/developer/docs/reference)
- Officially closed: `service:psapi` requires a confidential client ([Authorization docs](https://www.pathofexile.com/developer/docs/authorization)) and "We are currently unable to process new applications" ([Developer Docs](https://www.pathofexile.com/developer/docs))
- Officially absent: the docs index lists Server Endpoint, Account Profile, Account Item Filters, Leagues, PvP Matches, Account Leagues, Account Characters, Account Stashes, League Accounts, Guild Stashes, Public Stashes, Currency Exchange, Type Definitions, Extra Definitions — and **"The index does not list a trade API."** — [Developer Docs index](https://www.pathofexile.com/developer/docs/index)
- What every live tool actually does: live trade-site queries for item pricing ([Overwolf PoE Overlay](https://www.overwolf.com/app/kyusung4698-poe_overlay), [Xiletrade](https://maxensas.github.io/xiletrade/), [Sidekick credits "Path of Exile Trade"](https://github.com/Sidekick-Poe/Sidekick)) plus aggregate lookups against poe.ninja and poeprices.info ([APT](https://github.com/SnosMe/awakened-poe-trade), [Sidekick](https://github.com/Sidekick-Poe/Sidekick), [PoE Overlay](https://github.com/kyusung4698/PoE-Overlay))
- Mandatory engineering requirements, documented: parse `X-Rate-Limit-*` and `Retry-After`; send `User-Agent: OAuth {clientId}/{version} (contact: {contact})`; "Exceeding these limits frequently will result in your application access being revoked" — [Developer Docs index](https://www.pathofexile.com/developer/docs/index); [Developer Docs](https://www.pathofexile.com/developer/docs)
- Mandatory product requirements, documented: one action per manual key press for anything reading screen/clipboard/logs; keep the user aware of what is read; no interaction with the game process or files; carry the non-affiliation notice; no commercial use of GGG IP — [Developer Docs](https://www.pathofexile.com/developer/docs)
- The rate-limit budget belongs to the player, and exhausting it on one channel can lock them out of all of them — [PoE forum thread 3801062](https://www.pathofexile.com/forum/view-thread/3801062) **[SECONDHAND staff quote]**

### Inferences
- **The practical compliance playbook of the survivors, distilled:**
  1. Never touch the game process, memory, or files. (Documented ToU line; APT names it explicitly.)
  2. Trigger every network action from an explicit manual keypress; one keypress, one request. (Documented screen/clipboard clause; it is also what makes rate limits survivable.)
  3. Get item data from the clipboard, not from screen scraping or memory. (OCR exists in APT as a fallback, not a default.)
  4. Hold no credentials. Ask for no POESESSID. Prefer having no login at all over having a login you must secure.
  5. Run as a separate process with its own window; if you want a true in-game overlay, use a sanctioned overlay platform (Overwolf) rather than hooking the client.
  6. Obey `Retry-After` and `X-Rate-Limit-*` globally, as a single shared budget, because the budget is the player's.
  7. Send an identifying User-Agent with contact details — be findable, so GGG can ask before it blocks.
  8. Ship the non-affiliation notice verbatim.
  9. Credit your data sources (poe.ninja, poeprices.info) prominently; attribution is the currency of continued tolerance.
  10. Scope features to what public data can support; abandon features that need account scope.
- **The honest framing for the rewrite decision:** there is no route by which a new independent PoE 1 pricer becomes *formally* sanctioned, because the only sanctioned bulk source is a closed application process and the endpoint everyone actually uses is undocumented. The achievable goal is **maximum defensibility**: every official route that *is* open is used (Currency Exchange), every documented behavioural rule is followed to the letter, the rate-limit discipline is better than the official site's own users', and nothing about the tool touches the game. That is precisely the posture of the tools that have survived a decade.
- Choosing poe.ninja as the *primary* source trades GGG risk for single-operator risk with no agreement and 403'd docs. Using the Currency Exchange API as the primary bulk-price spine, with poe.ninja as an enrichment layer, is strictly more defensible.

### Gaps
- Currency Exchange rate limits are undocumented in what I retrieved — must be confirmed before designing a polling schedule.
- Whether Currency Exchange data is granular enough to replace poe.ninja for anything beyond currency pairs is unverified; it describes "market ID and pair," which suggests currency markets rather than item markets.
- Whether GGG has shipped OAuth for the trade website (promised in Dec 2022) by Nov 2026 is unresolved. If it has, that changes the recommendation materially and should be checked directly on the developer docs.

---

## Appendix: liveness table (verified 2026-11-22)

| Tool | Repo | Last push (`pushed_at`) | Status | Auth | Primary data |
|---|---|---|---|---|---|
| Path of Building Community | `PathOfBuildingCommunity/PathOfBuilding` | 2026-11-20 | **Alive**, 5.5k stars, 1018 open issues ([API](https://api.github.com/repos/PathOfBuildingCommunity/PathOfBuilding)) | None documented in README; import/paste | Offline; imported trees/items |
| Sidekick | `Sidekick-Poe/Sidekick` | **2026-11-22** | **Alive**, most active; PoE1+PoE2 ([API](https://api.github.com/repos/Sidekick-Poe/Sidekick)) | **None — "no login"** | PoE Trade, poe.ninja, poeprices, poe2scout |
| Awakened PoE Trade | `SnosMe/awakened-poe-trade` | 2026-11-19 | **Alive**, PoE 1 focused ([API](https://api.github.com/repos/SnosMe/awakened-poe-trade)) | None mentioned | poe.ninja, poeprices.info; clipboard |
| Xiletrade | `maxensas/xiletrade` | not checked | Appears alive; PoE1+PoE2 ([site](https://maxensas.github.io/xiletrade/)) | not checked | Official trade sites |
| Exilence Next | `viktorgullmark/exilence-next` | **2023-05-23** | **Stalled ~3.5y** ([API](https://api.github.com/repos/viktorgullmark/exilence-next)) | **OAuth** via `next.exilence.app` backend | Account stashes |
| Exilence (original) | `viktorgullmark/exilence` | — | **Archived 2023-05-01** ([releases](https://github.com/viktorgullmark/exilence/releases)) | — | — |
| PoE Overlay | `kyusung4698/PoE-Overlay` | **2022-04-16** | **Deprecated**; moved to Overwolf ([DEPRECATED.md](https://raw.githubusercontent.com/kyusung4698/PoE-Overlay/master/DEPRECATED.md)) | None mentioned | poe.ninja, poeprices, libggpk |
| PoE Overlay Community Fork | `wratho/PoE-Overlay-Community-Fork` | not checked | Fork of pre-Overwolf 2020-06-10 snapshot | In-tool PoE account sign-in for some features | — |
| Sidekick (original) | `domialex/Sidekick` | — | **Archived 2025-06-16**, points to Sidekick-Poe | — | — |
| poe.ninja | — | — | Alive; **API docs 403 to automated clients** (verified) | n/a | Grandfathered psapi consumer |
| PoE Antiquary | `poe-antiquary.xyz` | — | **Liveness unverified**; reported to have stopped collecting around Necropolis ([forum](https://www.pathofexile.com/forum/view-thread/3867005)) | n/a | Derived from poe.ninja |
| poe.watch | — | — | **Status unknown — no evidence found either way** | n/a | — |
| Stash-overlay cluster | `darvid/stashvision`, `azra1l/poe_qolV2`, `CLOUGH/poe-recipe`, `Zyertdox/EnhancePoEApp` | not checked | Various | **POESESSID** (poe-recipe reads it from browser cookies) | Account stashes |
