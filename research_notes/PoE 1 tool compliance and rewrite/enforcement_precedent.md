# PoE 1 Enforcement Record: Price-Check Tools, Trade-API Scraping, Overlays, POESESSID

Research date: 2026-11-22. Scope: Path of Exile 1. PoE 2 incidents are included only
where no PoE 1 equivalent exists, and are explicitly labelled as PoE 2.

**Evidence labelling used throughout:**
- **[GGG-PRIMARY]** — a statement by an identified GGG staff account, or GGG's own documentation.
- **[DEV-PRIMARY]** — a tool author's own statement in their own repo/changelog.
- **[SELF-REPORT]** — a player's unverified account of their own ban. Tool inventory is unverifiable.
- **[COMMUNITY]** — forum/Reddit consensus, no primary basis. Folklore unless otherwise noted.

---

## Q1: Has GGG ever actioned accounts over a price-checking tool, an overlay, programmatic trade-API use, or POESESSID use?

### Takeaway

There is exactly **one well-documented GGG-confirmed enforcement event** involving a
price-checking overlay (POE Overlay, May 2020) — and GGG's own staff stated the accounts
were **locked, not banned**, that the trigger was **~6 million HTTP 429 responses in a day**
from three accounts, and that normal-volume users were "fine". Every other ban claim in the
record is a **self-report with an unverifiable tool inventory**, and the clearest ones are
**PoE 2**, not PoE 1. I found **no GGG statement, and no documented case, of an account being
actioned for a price-check tool operating at ordinary human request volume.** For POESESSID
specifically, GGG has stated on the record that it **had not banned anyone** for sharing it.

### Cited Findings

**The POE Overlay event (May 2020) — the only GGG-confirmed incident [GGG-PRIMARY]**
- Bex_GGG said the Overlay-related account locks were because the tool was "hammering the ever-living sh*t out of our website", and she believed it was hitting the API "a few million times a day" — [GGG staff posts, r/pathofexile 2020, via DevTrackers](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Novynn (GGG) gave hard numbers: on "the 28th", poe-overlay sent about **6.7 million requests, ~6.1 million of which returned HTTP 429**, and **~5.9 million of those 429s came from just three accounts** — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Novynn stated the detection rule: GGG considers **only failed requests (roughly HTTP 4xx)** when scanning for bad actors, and "If you're not generating millions of 429's (which is what this tool is sometimes doing) then you're fine." — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Outcome per Bex_GGG: the accounts were **"locked, not banned"**; she said any genuinely banned accounts would be a separate issue she had no details on — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Aggravating factor named by GGG: **some versions of poe-overlay randomised/obfuscated their User-Agent**. Novynn said GGG "really don't condone" user-agent obfuscation — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Bex_GGG restated the client-interaction policy, attributed to Chris Wilson and unchanged for ~8 years: "You may not run programs that interact with the Path of Exile game client." Programs running **entirely outside the game** — ones that would work on a second computer, e.g. reading client log files — are allowed, and **reading clipboard data is also acceptable** under the same test — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Note: the OP's framing that "auto-pricing is bannable" is **the OP's interpretation**; there is no staff statement in that thread addressing auto-pricing — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)

**GGG's documented sanctions ladder (live developer docs, 2026) [GGG-PRIMARY]**
- "In order to protect our services we employ the use of rate limits on the majority of our endpoints." Limits are dynamic; apps must parse `X-Rate-Limit-*` headers and honour `Retry-After` — [pathofexile.com/developer/docs](https://www.pathofexile.com/developer/docs/index)
- "Exceeding these limits frequently will result in your **application access being revoked**." Apps/users making too many 4xx requests in a short period are **restricted from further access** — [same source](https://www.pathofexile.com/developer/docs/index)
- Creating or using executables that **interact with the game** "will result in **immediate account termination**", and macro/overlay rule violations can lead to account termination **for both the developer and their users** — [same source](https://www.pathofexile.com/developer/docs/index)
- The docs' language is "access revoked / restricted / account termination" — the first two tiers are **access controls, not account bans** — [same source](https://www.pathofexile.com/developer/docs/index)

**Self-reported bans (none GGG-confirmed, none clearly PoE 1)**
- **[SELF-REPORT, game ambiguous]** A player banned **2025-04-06** for reason "Third Party Software" says the only tools used were **Path of Building 2** and **PoE Overlay** (price checking only). The ban was **upheld as permanent** under Terms of Use §7 via a template reply. The only GGG reply in-thread was Joel_GGG (2025-04-08): "Hi! Could you please contact us at support@grindinggear.com." — [PoE forum thread 3748982](https://www.pathofexile.com/forum/view-thread/3748982). The tool names (PoB2) suggest PoE 2 context; the thread does not state which game.
- **[SELF-REPORT, PoE 2]** A GeForce-Now player banned ~May 2025 shortly after setting up **Exiled Exchange 2** (a price checker, fork of Awakened PoE Trade). Appeal **auto-rejected within minutes**; support replied "If your appeal has been denied we are not able to assist with this further via email." Appeal/rejection screenshots in the thread were **removed by Support**. Other users in-thread speculated excessive requests; one cited ToU §7C — [PoE 2 forum thread 3777565](https://www.pathofexile.com/forum/view-thread/3777565)
- **[SELF-REPORT]** User 265Spike (2025-04-12) claims a permanent ban after ~a month of using the **Overwolf PoE Overlay II**, with no specific reason given and a failed appeal. They note the app had prompted them to install a **Chrome plugin** days before — a confounder — [PoE forum thread 3637217](https://www.pathofexile.com/forum/view-thread/3637217)
- **[COMMUNITY]** Counter-evidence in the same discussions: multiple users state Awakened PoE Trade (the PoE 1 tool) "has been used for almost a decade and no one is getting banned from this", and in the April 2025 thread other users using only awakened-poe-trade reported no action — [PoE 2 forum thread 3777565 p.3](https://www.pathofexile.com/forum/view-thread/3777565/page/3)

**GGG's position when asked directly [GGG-PRIMARY]**
- CoryA_GGG, **2024-12-15**: "we are unable to guarantee if a tool is allowed or would remain allowed in the future", and GGG generally discourages third-party tools because they may give players advantages — [PoE forum thread 3637217](https://www.pathofexile.com/forum/view-thread/3637217)
- Earlier staff phrasing in the same vein: GGG "cannot comment on the legality of third-party tools, as we aren't able to thoroughly and accurately check exactly how they work. We do not encourage the creation or use of third-party tools because they provide advantages for players that use them." — surfaced in forum discussion of the official policy ([PoE forum thread 2984212](https://www.pathofexile.com/forum/view-thread/2984212/page/2#p23655339))

### Inferences

- GGG's **enforcement trigger in the only confirmed case was request volume and 4xx/429 rate**, not the tool's category, its UI, or the fact that it was an overlay. A tool that issues one search per user keypress is three to four orders of magnitude below the documented trigger.
- The documented sanction for API abuse is the **least-severe tier GGG has**: website account lock / app access revocation. Game-account termination is reserved in GGG's own docs for **executables that interact with the game client**.
- The 2025 self-reported permanent bans are **not demonstrably caused by the price checker**. In each case the ban reason is a generic ToU §7 citation, the tool inventory is self-asserted, and at least one case has an obvious confounder (a browser extension). Attributing these to price checking is an inference the posters made, not a GGG finding.
- The 2020 incident is **six years old** and predates the OAuth programme, the published developer docs, and PoE 2. It should be read as establishing *what behaviour GGG polices* (volume, 4xx, UA obfuscation) rather than as current severity calibration.

### Gaps

- **No GGG statement anywhere confirming that a specific named price-check tool caused a specific ban.** Every ban attribution to a price checker is the player's own inference.
- No case found where GGG published a mass-ban announcement whose categories included price-check tools, overlays, or trade-API use.
- The April 2025 ban thread does not state whether it concerns PoE 1 or PoE 2; this could not be resolved.

---

## Q2: History of rate-limiting, IP-blocking and Cloudflare-blocking of automated clients on pathofexile.com

### Takeaway

Rate limiting on pathofexile.com is **real, routine, per-IP rather than per-account, and is the
normal enforcement mechanism** — tools hit 429s and multi-minute to 30-minute lockouts constantly,
including in the last week before this research date, and this is treated by both GGG and tool
authors as ordinary operation, not punishment. I found **no evidence of Cloudflare-specific or
permanent IP blocking** aimed at PoE tools.

### Cited Findings

- **Mechanism is per-IP, not per-account.** A community explanation (not staff) of the lockouts: "Rate limit is activated when an IP 'spams' GGG backend API's. You will need to e-mail GGG support for them to investigate this" — and that the account **is not banned** — [PoE forum thread 3257868, 2022-03-20](https://www.pathofexile.com/forum/view-thread/3257868). **[COMMUNITY]**, but consistent with GGG's docs and with tool-author statements below.
- **2021 [DEV-PRIMARY]** SnosMe (Awakened PoE Trade maintainer): the limit "is set by GGG to keep trade site in working state at league start"; APT **queues** requests to stay under it, with a residual chance of hitting the limit from latency and GGG-side load — [awakened-poe-trade issue #309](https://github.com/SnosMe/awakened-poe-trade/issues/309)
- **2021 [DEV-PRIMARY/COMMUNITY]** A feature request for proxy support was filed because **two people sharing one IP** were being rate-limited out of the trade site — [issue #317](https://github.com/SnosMe/awakened-poe-trade/issues/317). (Relevant: IP rotation is something tool *users* have asked for; GGG has separately condemned UA obfuscation.)
- **2026-11-19 [DEV-PRIMARY]** A user reported APT queueing many trade requests on **each stat toggle** in the overlay, producing an **1800-second (30 minute) limit from a single item search** — [issue #1965](https://github.com/SnosMe/awakened-poe-trade/issues/1965)
- **2026-11-21 (one day before this research) [DEV-PRIMARY]** SnosMe on a 1800s block: "**Limits are set by GGG.** This can occur if you have multiple people on same IP (CGNAT)", and a rarer cause: "if you made a search in browser and it was the last free slot available", after which APT's next request trips the block. **No bans or account actions mentioned.** — [issue #1968](https://github.com/SnosMe/awakened-poe-trade/issues/1968)
- **2026-03 [DEV-PRIMARY/unresolved]** A user hit rate limiting while their trade-site account looked normal and asked whether APT caused it; no resolution in the issue — [issue #1779](https://github.com/SnosMe/awakened-poe-trade/issues/1779)
- **Shared quota between browser and tool.** A third-party tool's own docs note GGG counts trade searches **per IP**, so an open trade-site tab and a tool spend the same allowance, and exceeding it locks you out for ~half an hour — [poe-ninja-build-cost README](https://github.com/yaredmax/poe-ninja-build-cost)
- **Best-practice pacing is header-driven, not fixed-delay.** That same tool drives its pace from GGG's own rate-limit response headers rather than a hardcoded sleep — [poe-ninja-build-cost](https://github.com/yaredmax/poe-ninja-build-cost)
- **Mandatory identifiable User-Agent [GGG-PRIMARY].** "Any application that interacts with our API must set an identifiable User Agent header", in the format `User-Agent: OAuth {$clientId}/{$version} (contact: {$contact})`. Failing the guidelines "may result in your application's access being revoked" — [developer docs](https://www.pathofexile.com/developer/docs/index)
- **Historical limit magnitude [COMMUNITY]** 2021 player reports describe roughly 10 searches/minute, then waits of ~1–5 minutes, with "Rate limit exceeded" resetting the timer — [PoE forum thread 3156222](https://www.pathofexile.com/forum/view-thread/3156222) and [thread 3015085](http://www.pathofexile.com/forum/view-thread/3015085). Numbers are old and almost certainly stale.

### Inferences

- The behaviours GGG has actually penalised or named are: **sustained high volume**, **ignoring 429s and continuing to send** (Novynn noted locked accounts kept sending requests anyway), and **User-Agent obfuscation**. IP rotation is adjacent — it is what users asked tools to do to evade per-IP limits, and is the natural analogue of UA obfuscation that GGG condemned.
- A 30-minute lockout from one item search (issue #1965) shows the limit is tight enough that **a naive implementation that fires a search per UI interaction will trip it with zero malicious intent**. The rewrite's main real-world risk is therefore degraded function, not banning.
- Because the quota is per-IP and shared with the browser, **CGNAT and shared households produce lockouts that look like targeted blocking but are not**.

### Gaps

- **No evidence found of Cloudflare challenge pages, bot-detection, or IP bans specifically targeting PoE third-party tools.** The only access-denial artefact found was a plain "PERMISSION DENIED" page on a malformed `get-stash-items` request ([thread 3805108](https://www.pathofexile.com/forum/view-thread/3805108)), which was a URL-encoding bug, not a block.
- No incident found where trade-site *scraping by a public site* (poe.ninja, poeprices.info, etc.) triggered a documented GGG block. Searching specifically for poe.ninja blocks returned nothing.
- Current numeric rate-limit policy values were not retrieved (GGG documents them as dynamic, delivered in headers).

---

## Q3: POESESSID — track record, GGG protections, staff comments, tools forced to abandon it

### Takeaway

GGG has repeatedly and publicly told players **not to put their POESESSID into third-party apps**
— most recently a GGG web developer in a **PoE 1** thread in July 2025 — and has built OAuth 2.1
as the sanctioned alternative. But GGG's own December 2022 announcement stated that **it had not
banned anyone for sharing a session cookie**, and I found **no case of an account being actioned
for POESESSID use by a tool**. The pressure has been **architectural (deprecate it in favour of
OAuth) rather than punitive.**

### Cited Findings

- **2022-12-14 [GGG-PRIMARY]** Official announcement by Community_Team, "Do not share POESESSID values with other people": some third-party tools ask for the POESESSID, which can give the holder near-complete access to the website account; sharing login information **violates the Terms of Use**; GGG said it **had not yet banned anyone for this**, but misuse of a shared session could make things worse for the account holder; tools may store it insecurely and a tool's behaviour can change later; **OAuth is the recommended path** and GGG was working to expand the resources available to it, **including the trade site** — [PoE forum announcement 3328601](https://www.pathofexile.com/forum/view-thread/3328601)
- **Session invalidation mechanism [GGG-PRIMARY]** The same announcement states that **logging out of pathofexile.com and back in resets your POESESSID**, invalidating any previously shared cookie — [same source](https://www.pathofexile.com/forum/view-thread/3328601)
- **2022-12 [GGG-PRIMARY]** The official @pathofexile account amplified it: "Do not share POESESSID values with other people" — [X/Twitter, 2022-12-13](https://x.com/pathofexile/status/1602851059361550337)
- **2025-07 [GGG-PRIMARY, PoE 1]** Novynn, labelled **Web Developer / Grinding Gear Games**, replying in a **PoE 1 Technical Support** thread about poeprices.info stash access: **"Don't put your POESESSID in any third party app"**, with a link to the 2022 announcement. That was his only post in the thread; he did not threaten any action — [PoE forum thread 3805108, 2025-07-02/03](https://www.pathofexile.com/forum/view-thread/3805108)
- **Tools moved off it voluntarily [DEV-PRIMARY]** Immediately under the 2022 announcement, a tool author (ClumsyParasite) posted that they would **move away from session-ID authentication** in their apps and linked GGG's OAuth documentation — [thread 3328601](https://www.pathofexile.com/forum/view-thread/3328601)
- **Named POESESSID-using tools [COMMUNITY]** Users in that thread named Acquisition, CurrencyCop, Chaos Recipe Enhancer and Exilence Next as POESESSID consumers. Unverified user claims.
- **Why desktop tools struggle to switch [GGG-PRIMARY doc, quoted in forum]** GGG's developer docs state they **do not support the implicit grant or PKCE**; a distributed desktop app therefore needs a **secure backend server** to hold its credentials, which is why Exilence runs one — [PoE forum thread 3332267](https://www.pathofexile.com/forum/view-thread/3332267/page/1#p25096638); note GGG's current docs say executables must use a **public** OAuth client — [developer docs](https://www.pathofexile.com/developer/docs/index). **These two statements appear to conflict; the forum quote is older and may be stale.**
- **Community read of GGG's motive [COMMUNITY]** Forum users argued GGG's objection is to session IDs rather than to the tools, and that GGG "was advising the devs that make them to use OAuth, not telling players to stop using third-party tools" — [community discussion](https://lr.mint.lgbt/r/pathofexile/comments/zlf0gw/do_not_share_poesessid_values_with_other_people/). This is **interpretation, not a GGG statement.**
- **Contested community claims [COMMUNITY]** In a Feb 2021 thread with **no staff posts at all**, one user asserted a POESESSID cannot be used to log in or change password/email but can be used to buy MTX and view purchase history, and that GGG "accepts" POESESSID use in third-party programs. Another user asserted the opposite ("no tools regardless of reason"). Both are unsourced — [PoE forum thread 3037529](https://www.pathofexile.com/forum/view-thread/3037529)

### Inferences

- GGG's POESESSID posture is a **security advisory plus a migration push**, not an enforcement programme. The 2022 announcement is unusually explicit in saying no one had been banned for it, and no later statement reverses that.
- Staff attention is **ongoing and current** (July 2025, in a PoE 1 thread), so a tool shipping in 2026 that asks the user to paste a POESESSID should expect to be publicly discouraged by GGG staff if it comes to their attention — but the evidenced consequence is a staff reply, not an action.
- The session-reset-on-logout behaviour means POESESSID-based tools are inherently fragile; that, plus GGG expanding OAuth to the trade site, is the practical reason tools abandon it.

### Gaps

- **No evidence found that GGG shortened POESESSID lifetime, forcibly rotated cookies en masse, or added a technical protection specifically to break third-party use.** The only invalidation mechanism documented is the user's own logout.
- No case found of a tool being **forced** (as opposed to choosing) to abandon POESESSID.
- GGG has not published a deprecation date for session-cookie access to the website endpoints, as far as I could find.

---

## Q4: Enforcement history around the trade site specifically (sniping, bulk scraping, live-search abuse)

### Takeaway

Despite years of loud player complaint about trade-sniping bots, I could find **no official GGG
announcement of enforcement against trade-site abuse** — no ban wave post, no stated live-search
cap, no published actioned-account count. The only GGG-documented trade-infrastructure enforcement
remains the 2020 POE Overlay 429 episode. Player sentiment is that GGG does **not** act here.

### Cited Findings

- **Player complaints, late 2025 / 2026 [COMMUNITY]** Multiple PoE 1 Feedback threads report sniping bots buying underpriced items within ~1 second: [thread 3879741](https://www.pathofexile.com/forum/view-thread/3879741), [thread 4010412](https://www.pathofexile.com/forum/view-thread/4010412), [thread 3875438](https://www.pathofexile.com/forum/view-thread/3875438)
- **Claimed absence of enforcement [COMMUNITY]** One post asserts "literally ZERO bans any time recently on bot sites" and asks why GGG doesn't use honeypots — [thread 3134820 p.5](https://www.pathofexile.com/forum/view-thread/3134820/page/5)
- **Proposed-but-unimplemented fixes [COMMUNITY]** Player suggestions include capping live searches per account and penalising sub-second purchases. **None is confirmed implemented** — [thread 4010412](https://www.pathofexile.com/forum/view-thread/4010412)
- **The one confirmed trade-infrastructure action remains 2020 [GGG-PRIMARY]** See Q1: website account locks for ~6M 429s/day — [DevTrackers](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- **Rate limiting is the de facto trade-abuse control [GGG-PRIMARY]** The developer docs frame rate limits as the protective mechanism and access revocation as the sanction — [developer docs](https://www.pathofexile.com/developer/docs/index)

### Inferences

- What distinguished the 2020 actioned behaviour from ordinary tool use was **not** that it was a price checker and **not** that it read the trade API — it was **volume, a 91% failure rate (6.1M of 6.7M requests were 429s), continuing to send after being limited, and disguising identity**. That is the operative line.
- GGG appears to rely on **throttling rather than banning** for trade-site misuse. For a tool author, this means the realistic worst case is the tool stops working or the user's IP is locked out for 30 minutes, not an account ban.
- The persistent sniping-bot problem is weak evidence that GGG's trade-side enforcement bandwidth is low, which further reduces the plausibility of GGG hunting low-volume read-only price checkers.

### Gaps

- **No GGG announcement of any trade-related ban wave was found**, in Announcements or elsewhere. This is a well-evidenced absence for the sniping/bot category, though absence of a *public* announcement does not prove absence of quiet action.
- No GGG statement found defining a live-search cap or what live-search behaviour is abusive.

---

## Q5: Have well-known tool authors been contacted, asked to change their tool, or had access revoked?

### Takeaway

The evidenced pattern is **public pressure, not private contact**: GGG staff criticised poe-overlay's
behaviour in a public thread, and the author then changed it. I found **no developer post-mortem,
GitHub issue, or Discord announcement in which a major PoE tool author describes being contacted by
GGG, asked to change their tool, or having access revoked.**

### Cited Findings

- **[GGG-PRIMARY]** Novynn said GGG does not condone UA obfuscation and was "glad to see that the author of this particular tool has removed this in their most recent update" — i.e. **the author changed the tool after GGG's public comment** — [DevTrackers](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- **[GGG-PRIMARY]** In the same thread Novynn said only that "the tool's author will need to look into it" — **no claim of direct outreach** — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- **[DEV-PRIMARY]** SnosMe's public statements about rate limits (2021 and 2026) consistently describe GGG limits as an engineering constraint to design around, and never mention GGG contact, warnings, or threats — [issue #309](https://github.com/SnosMe/awakened-poe-trade/issues/309), [issue #1968](https://github.com/SnosMe/awakened-poe-trade/issues/1968)
- **[GGG-PRIMARY, content unknown]** Kane_GGG, 2020-11-04, in the "3rd Party Tools" thread: "Hi there! I've PM'd you about this. :)" — the only staff post in that thread; its substance is not public — [thread 2984212](https://www.pathofexile.com/forum/view-thread/2984212/page/2#p23655339)
- **[COMMUNITY]** Exilence Next lapsed ("no longer maintained", May 2024) and was later picked up by a new maintainer; the stated reason for its backend architecture was GGG's OAuth grant-type restrictions, not GGG intervention — [thread 3332267](https://www.pathofexile.com/forum/view-thread/3332267/page/1#p25096638)
- **[GGG-PRIMARY]** GGG's documented lever against a developer is **revoking the OAuth application's access**, and the docs warn termination can apply "for both the developer and their users" in the macro/overlay-violation case — [developer docs](https://www.pathofexile.com/developer/docs/index)

### Inferences

- GGG does have a formal lever (OAuth client revocation) that did not exist in 2020, which makes **quiet, non-public enforcement against a registered app more likely today than in 2020** — but I found no instance of it being used.
- The absence of developer accounts of GGG contact across many years and many popular tools is meaningful: these authors talk openly in public issue trackers, and a cease-and-desist or forced change would almost certainly have surfaced.

### Gaps

- **No post-mortems or developer blog posts from poe.ninja, Awakened PoE Trade, Exilence Next, PoE Overlay or PoE Antiquary describing GGG contact were found.** Discord announcement channels were not searchable with the tools available, so this is a genuine blind spot.
- The content of Kane_GGG's 2020 PM is unknown.

---

## Q6: GGG's anti-cheat posture for PoE 1 in 2026 — can an overlay, global hotkey, or clipboard read be detected or misread?

### Takeaway

I found **no primary evidence that the PoE 1 client ships a kernel-level or process-inspecting
anti-cheat**, and GGG's own long-standing test is behavioural: software that **does not interact with
the game client** — including clipboard reading and log-file reading — is allowed. However, there is
**credible 2025 evidence of a client-side "cheating software" detector with false positives** (in
PoE 2), which means misidentification is not purely hypothetical.

### Cited Findings

- **GGG's stated test [GGG-PRIMARY]** "You may not run programs that interact with the Path of Exile game client." Programs running entirely outside the game — ones that would work from a second computer, e.g. log-file readers — are allowed, and **reading clipboard data is also acceptable** — [DevTrackers, Bex_GGG 2020](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay). This is the single most load-bearing primary statement for the user's question, but it is **six years old**.
- **Documented in-game detection message, 2025-12-13 [SELF-REPORT, PoE 2 content]** Players reported an in-game message: **"you have been detected running cheating software"** and **"this is your only warning"**, starting after a league-start patch. Reporters variously had **only Discord / Nvidia App / Steam overlays**, no third-party tools, and in one case had never used PoB. Workarounds that stopped the warning included **switching the render engine DX12 → Vulkan → DX12** and **deleting the game's Documents folder** (a stale filter with custom FilterBlade sounds was suspected). **No GGG staff reply; no bans reported.** — [PoE forum thread 3886186](https://www.pathofexile.com/forum/view-thread/3886186). Posted in the PoE 1 Technical Support section but the content refers to PoE 2 (0.3 → league start), so **treat as PoE 2**.
- **[SELF-REPORT, PoE 2]** A player banned for running **Process Lasso** (a CPU-affinity/stutter utility, not a game tool) asked GGG to refine its "EAC anti-cheat detection logic". The EAC attribution is **the player's word, not GGG's** — [PoE 2 forum thread 3986046](https://www.pathofexile.com/forum/view-thread/3986046)
- **[SECONDARY / vendor]** Backgrind's overlay-compatibility checker states PoE 1 and PoE 2 judge cheating from **server-side behaviour** and **do not load a kernel-mode driver**, and rates plain always-on-top overlay windows as safe. It distinguishes **DLL-injecting overlays** (e.g. Discord's in-game overlay drawing inside the game) from separate windows, and notes exclusive fullscreen hides overlays at the OS level — [backgrind.com](https://backgrind.com/overlay-anticheat-checker/path-of-exile/). **This is a commercial site; I could not corroborate its claims against any GGG statement. Do not treat as established.**
- **[COMMUNITY, old]** An OwnedCore thread claims a **January 2015** patch added an unannounced client-side cheat-detection system. Eleven years old, second-hand, from a cheating forum — [ownedcore thread](https://www.ownedcore.com/forums/mmo/path-of-exile/poe-bots-programs/508787-warning-anti-cheat-implemented-stop-using-any-hack-bot-proof-inside-8.html)
- **[GGG-PRIMARY]** GGG's developer docs explicitly contemplate **macro and overlay rules** whose violation can lead to account termination for developer and users — so overlays are a category GGG regulates in writing, independent of whether it can detect them — [developer docs](https://www.pathofexile.com/developer/docs/index)

### Inferences

- The three specific mechanisms the user asked about map to GGG's stated test as follows: **clipboard reading is explicitly allowed** by name; an **always-on-top separate window** that does not inject into or read the game process does not "interact with the game client"; a **global hotkey** registered with the OS likewise does not touch the client, though sending synthetic input *into* the client would.
- The December 2025 false-positive episode shows the detector (whatever it is) **keys on things other than injected code** — render-engine state and stale config files were implicated — so misidentification is possible in principle. Importantly, the documented outcome was a **warning message, not a ban**, and the reporters remained able to log in.
- Because the credible detector evidence is all from **PoE 2**, and the user's target is **PoE 1**, the PoE 1 detector posture in 2026 is effectively **undocumented** rather than confirmed absent.

### Gaps

- **No official GGG statement from 2024–2026 on PoE 1 anti-cheat, overlays, or whether the client inspects other running processes was found.** This is the weakest-evidenced area of this research.
- Whether PoE 1 ships Easy Anti-Cheat could **not be confirmed or refuted** from any primary source. The only EAC references are player speculation about PoE 2. The reliable way to settle it is to inspect the PoE 1 install directory for EAC components.
- No evidence either way on whether global hotkey registration is observable to GGG.

---

## Q7: Is there any documented case of an account being actioned for something that was *only* reading information?

### Takeaway

**No.** I found no documented case — and certainly none GGG-confirmed — of an account being actioned
for purely reading information at ordinary volume. The nearest thing is the 2020 POE Overlay episode,
which **was** read-only (price checking) but was actioned for **volume**, and even then the outcome
was a **website account lock, not a game ban**. This is a well-evidenced absence, with the caveat
that the self-reported 2025 bans cannot be fully excluded because their causes were never disclosed.

### Cited Findings

- The 2020 POE Overlay case is **read-only price checking** and is the only GGG-confirmed action; the stated cause was ~6.1M 429s/day and continued sending after limiting, and the outcome was accounts **"locked, not banned"** — [DevTrackers](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- Novynn's threshold statement is the clearest safe-harbour signal in the record: "**If you're not generating millions of 429's ... then you're fine.**" — [same source](https://devtrackers.gg/pathofexile/p/cdea4d78-psa-waves-of-players-being-banned-for-use-of-third-party-tool-poe-overlay)
- GGG's own tiering puts **account termination** behind "executables that interact with the game", and **access revocation/restriction** behind rate-limit and 4xx abuse — i.e. a reader that does not touch the client sits in the lower tier by GGG's own taxonomy — [developer docs](https://www.pathofexile.com/developer/docs/index)
- GGG stated it had **not banned anyone** even for the ToU-violating act of sharing a POESESSID — [announcement 3328601, 2022-12-14](https://www.pathofexile.com/forum/view-thread/3328601)
- The three 2025 permanent-ban self-reports ([3748982](https://www.pathofexile.com/forum/view-thread/3748982), [3777565](https://www.pathofexile.com/forum/view-thread/3777565), [3637217](https://www.pathofexile.com/forum/view-thread/3637217)) all involve read-only price checkers **by the poster's own account**, but in every case GGG gave only a generic ToU §7 citation and the posters' full software inventories are unverifiable; one has a browser-extension confounder, and the clearest one is PoE 2 on a cloud-streaming platform.
- Counterweight: Awakened PoE Trade — the dominant PoE 1 read-only price checker — has run for years with an active public issue tracker in which **no user has reported an account ban**; the tracker's ban-adjacent traffic is entirely about **rate limits** — [issues #309](https://github.com/SnosMe/awakened-poe-trade/issues/309), [#1779](https://github.com/SnosMe/awakened-poe-trade/issues/1779), [#1965](https://github.com/SnosMe/awakened-poe-trade/issues/1965), [#1968](https://github.com/SnosMe/awakened-poe-trade/issues/1968)

### Inferences

- On the evidence, **the actual enforcement risk to a low-volume, read-only, non-injecting PoE 1 tool is rate-limit lockout, not account action.** The historical ban record for read-only tools is empty of confirmed cases and the one confirmed action was a lesser sanction for a 10,000x volume outlier.
- The residual risk is **not zero but is unquantifiable**: GGG issues template ban reasons, refuses to discuss tool legality, explicitly declines to guarantee any tool remains allowed (CoryA_GGG, Dec 2024), and auto-rejects appeals. So a false positive cannot be ruled out, and if it happened the player would have no effective recourse — which is exactly the pattern in the 2025 self-reports.
- The widely-repeated community claim "price checkers are fine, everyone uses them" is **Reddit/forum consensus** that happens to be consistent with the primary record, not an established GGG permission. It should be stated as such.

### Gaps

- The causes of the 2025 permanent bans were **never disclosed by GGG**, so they can be neither confirmed nor excluded as read-only-tool bans. This is the single unresolvable hole in the risk assessment.
- No GGG statement after 2020 restates or updates the "millions of 429s" threshold, so it is unknown whether the practical threshold is now lower.

---

## Recency flags for the report writer

| Evidence | Date | PoE version | Still representative? |
|---|---|---|---|
| POE Overlay locks, 429 thresholds, clipboard/log-reading allowance (Bex_GGG, Novynn) | May 2020 | PoE 1 | **Only GGG-confirmed enforcement case, but 6 years old** — predates OAuth programme and PoE 2. Good for *what GGG polices*, weak for *current severity*. |
| "Do not share POESESSID" announcement (no bans issued) | 2022-12-14 | PoE 1 | Still the standing announcement; superseded by nothing found. |
| Jan 2015 client-side detection claim (OwnedCore) | 2015 | PoE 1 | **Stale and second-hand; do not rely on.** |
| CoryA_GGG "unable to guarantee if a tool is allowed" | 2024-12-15 | Both | Current official posture. |
| Self-reported permanent bans (PoB2+PoE Overlay; Exiled Exchange 2; Overwolf overlay) | Apr–May 2025 | PoE 2 / ambiguous | **Not PoE 1-confirmed**; unverified causes. |
| Novynn "Don't put your POESESSID in any third party app" | 2025-07-02 | **PoE 1 forum** | Most recent GGG staff statement on POESESSID; current. |
| "Detected running cheating software" false positives, DX12/Vulkan + stale filter triggers | 2025-12-13 | **PoE 2** | Most recent client-detector evidence; warning only, no bans. |
| APT rate-limit reports, 1800s lockouts, "Limits are set by GGG" | 2026-11-19 / 2026-11-21 | PoE 1 | **1–3 days before this research. Current.** |
| GGG developer docs sanctions ladder, User-Agent format, OAuth 2.1 | live, 2026 | Both | Current authoritative text. |
