# GGG's Written, Official Policy on Third-Party Tools for Path of Exile 1

**All live pages in this file were fetched directly on 2026-11-22** (raw HTML pulled and converted to text, not from memory). Where a page could not be reached, that is stated explicitly.

Primary documents used:
- Developer Docs — Overview / Third-Party Policy / Getting Started / Available Resources / Developer Guidelines / Errors / Rate Limits: https://www.pathofexile.com/developer/docs/index (fetched 2026-11-22; footer "© 2010 - 2026 Grinding Gear Games"; no document version or "last updated" stamp on the page itself)
- Developer Docs — Authorization (OAuth 2.1, Client Types, Available Scopes, Grant Types): https://www.pathofexile.com/developer/docs/authorization (fetched 2026-11-22)
- Developer Docs — API Reference: https://www.pathofexile.com/developer/docs/reference (fetched 2026-11-22)
- Developer Docs — Changelog: https://www.pathofexile.com/developer/docs/changelog (fetched 2026-11-22)
- Terms of Use, Privacy Notice and Cookies Notice: https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy (fetched 2026-11-22; the Privacy Policy portion carries "Last Updated: October 2024"; the Terms of Use portion carries **no date at all**)

---

## Q1. What the Terms of Use / User Agreement say about third-party programs, automation, bots, macros, overlays, programmatic website access and scraping

### Takeaway
The Terms of Use contain a single list of restrictions (clause 7, "Restrictions", lettered a–i) that is the binding backstop, and it is written broadly against the *tool-user*, not the tool-author: bots, non-client connections to the servers, data-extraction tools against the website, and reverse engineering are all forbidden absent GGG's prior written approval. The words "macro", "overlay", "scrape", "clipboard" and "price check" do **not** appear anywhere in the Terms of Use — those concepts live only in the Developer Docs policy (Q2). A separate clause 22 makes the Developer Docs' "API Policies" contractually binding.

### Cited Findings
Clause 7 opens: "Restrictions: Under no circumstances, without the prior written approval of Grinding Gear Games, may you:" — [Terms of Use, cl. 7](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)

The sub-clauses relevant to a price-checker, in the order they appear (lettering a–i inferred from order; GGG's own Developer Docs cite these as "7b, 7c, 7i", which matches this ordering exactly):

- **7a** — forbids: "Adapt, reproduce, store, distribute, print, display, publish or create derivative works from any part of the Website, Materials or Services other than in accordance with the Licence." — [Terms of Use, cl. 7a](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7b** — forbids: "Modify or adapt (including through third parties and third-party tools) the game client or its data, other than in the normal course of PoE gameplay as permitted in accordance with the Licence." — [Terms of Use, cl. 7b](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7c** — forbids: "Utilise any automated software or 'bots' in relation to your access or use of the Website, Materials or Services." — [Terms of Use, cl. 7c](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7d** — forbids: "Knowingly perform any actions that may cause the computers used to support the Website, Materials and Services (the 'Servers') to become overloaded or crash." — [Terms of Use, cl. 7d](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7e** — forbids: "Connect to the Servers through any software other than the authorised game client software." — [Terms of Use, cl. 7e](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7f** — forbids: "Use any data gathering and extraction tools or software to extract information from the Website or utilize framing techniques to enclose any of the contents of the Website." — [Terms of Use, cl. 7f](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7h** — forbids: "Perform in-game services during your use of the Website, Materials or Services for any form of compensation outside of PoE." — [Terms of Use, cl. 7h](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)
- **7i** — forbids: "Reverse engineer, de-compile or disassemble the Website, Materials or Services or seek to establish the technical processes, operations and communication protocols of the Website, Materials or Services through any means, including without limitation by reference to the input or output of the Website, Materials or Services or the internal structure and workings of the Website, Materials or Services." — [Terms of Use, cl. 7i](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)

Clause 22, "Website APIs", is the hook that makes the Developer Docs binding: GGG "may modify, add, remove, replace or upgrade any or all of its Website APIs ... at any time at its discretion", and "You must comply with Grinding Gear Games' technical documentation and policies (as published and as updated on the Website from time to time ...) regarding use of the Website APIs ('API Policies')". It adds: "you must comply with the Website API call limits set out in the API Policies ... and not attempt to circumvent those limits." — [Terms of Use, cl. 22](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)

The licence grant (clause 6) is for "your own personal and non-commercial use", and terminates immediately on "any behaviour which Grinding Gear Games deems in its sole discretion is not in keeping with the intended spirit of participation in PoE" — [Terms of Use, cl. 6](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)

Clause 16 (Cancellation of Membership) reserves the right to cancel or restrict an account "if it believes you have breached these Terms of Use", for the "spirit of participation" reason, "or for any other reason whatsoever", with no obligation to give "prior notice or explanation". — [Terms of Use, cl. 16](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)

Privacy Policy (the "Anti-cheat and security related information" row) confirms GGG collects, among other things, the PC "running process list", "unique hardware ID", "memory, and driver information" and "user behaviour and status that may be related to cheating software", used "to detect and observe cheating behaviours and consider restricting or banning access to the service". — [Privacy Policy, Last Updated October 2024](https://www.pathofexile.com/legal/terms-of-use-and-privacy-policy)

### Inferences
- A pure web-based price checker is unaffected by 7b and 7e (no client interaction, no non-client server connection). The genuinely dangerous clauses for a *desktop* price checker are **7c** (automated software "in relation to your access or use of the Website") and **7f** (data gathering/extraction tools against the Website) — both of which, read literally, would also forbid the official API being polled by a program. The Developer Docs are what rescue that: clause 22 makes the API Policies the permission regime that carves out approved API use.
- There is **no separate "User Agreement"** document; the single combined page "Terms of Use, Privacy Notice and Cookies Notice" is the operative instrument. Treat "Terms of Use" and "Terms of Service" as the same document — note the Developer Docs call it "Terms of Service" in one place and "Terms of Use" in another, referring to the same page.

### Gaps
- The Terms of Use portion carries **no last-updated date**, so it is impossible to establish from the page whether it has changed since the user's 2026-08-23 notes. Only the Privacy Policy is dated (October 2024).
- No clause anywhere in the Terms of Use mentions overlays, screen reading, clipboard reading, or price checking. Those words appear only in the Developer Docs.

---

## Q2. What the Developer Docs say — API Policies, client rules, User-Agent, rate limits, internal APIs

### Takeaway
The Developer Docs at /developer/docs/index are the real governing instrument for a third-party tool, and they are unusually specific: they classify application types by risk, state that desktop executables are "not encouraged" but **permitted**, and then set out two named rule sets — **macro rules** (for input automation) and **overlay rules** (for screen/clipboard/log reading). This is the only place GGG writes down the line the community believes exists.

### Cited Findings — the policy section ("API Policies and Third-Party Requirements")
- The docs declare themselves binding: "This documentation includes requirements that form our API Policies. Applications that interface with our APIs must adhere to these, as well any requirements in our Terms of Service and Privacy Policy." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Discretionary revocation: "We reserve the right to remove access to any or all of our APIs and services without notice at our discretion." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Commercial use: "As a general rule, we cannot allow our Intellectual Property to be used to generate commercial revenue. This includes creating and selling physical items, or copying our assets to make other games." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- GGG's stated default preference, verbatim: "Ordinarily we only allow the creation of applications that run completely separate from the game. We don't encourage players to download or run 'tools' on their machine as these are never guaranteed to be free from malware or nefarious practices." Followed by: "For these reasons and more, the safest kind of application for our players is a website that interacts with our API." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Three-tier classification of application types:
  1. **"Websites and web apps"** — "Little to no risk for end users."
  2. **"Executable apps that run independently from the game"** — "While not encouraged, these are permitted. They must use a public OAuth client if interacting with our APIs."
  3. **"Executable apps that interact with the game or game files"** — "This behaviour is strictly against our Terms of Use (sections 7b, 7c, 7i)." and "Creation or use of these type of applications will result in immediate account termination."
  — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- **Macro rules** (verbatim, under tier 2): "Automation of key-strokes (or other inputs) that affect the game in any way must follow our macro rules:" — then three bullets: "Macros must be invoked manually by the user (automated invocations such as but not limited to: timers, reacting to file changes, or from reading the screen are not allowed)."; "Each macro invocation must have one set function (it cannot cycle between actions)."; "The resulting function may only perform one action that interacts with the game (sending a single chat message or command counts as one action)." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- **Overlay rules** (verbatim, under tier 2): "Apps that use information sourced from the screen, the clipboard, or the game's logs must follow our overlay rules:" — then two bullets: "You may only read this information as long as the user is aware of what you are doing with that data."; "Reading from the screen or clipboard may only be performed once per manual key press." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Consequence clause, applying to both rule sets: "Introducing features against these guidelines or our Terms of Use can result in account termination for you as well as your users." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)

### Cited Findings — Available Resources / internal APIs
- Verbatim: "We can only support resources defined in our API Reference or listed in our Data Exports." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Verbatim: "Requests for access to any other internal website APIs or in-game resources will be denied. It is against our Terms of Use (section 7i) to reverse-engineer endpoints outside of this documentation." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)

### Cited Findings — Developer Guidelines
- Preamble: "Failing to meet any guidelines listed here may result in your application's access being revoked." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- OAuth credentials: "Keep your credentials safe."; "Don't share your account or application's credentials with anyone else."; "Don't include any application keys or credentials in your code."; "Don't embed keys or credentials in a distributed binary." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- "One product per registered application" — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- **User Agent (required format, verbatim):** "Any application that interacts with our API must set an identifiable User Agent header prefixed using the following format:" followed by `User-Agent: OAuth {$clientId}/{$version} (contact: {$contact}) ...` and the example `User-Agent: OAuth mypoeapp/1.0.0 (contact: mypoeapp@gmail.com) SomeOptionalThingHere` — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- **Third-Party Notice:** "You may not present your project or tool in a way that could be construed as it being developed or officially authorised by Grinding Gear Games. This includes but is not limited to using our company or game logos in a way that imitates official platforms. Every public or widely available application should include some form of the following statement in a visible location:" — and the prescribed statement: "This product isn't affiliated with or endorsed by Grinding Gear Games in any way." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)

### Cited Findings — rate limits and invalid-request threshold
- "In order to protect our services we employ the use of rate limits on the majority of our endpoints. These limits are dynamic and can change at any time depending on our requirements. For this reason we include headers in response headers that should be parsed and followed by your application." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Documented headers: `X-Rate-Limit-Policy`, `X-Rate-Limit-Rules` ("Common rules are: ip, account, and client"), `X-Rate-Limit-{$rule}` (triplet = "the maximum hits" / "the period (in seconds) tested" / "the time restricted (in seconds) if the rule is broken"), `X-Rate-Limit-{$rule}-State` (triplet = "the current hit count" / "the period (in seconds) tested" / "the active time restricted in seconds"), and `Retry-After` ("Time to wait (in seconds) until the rate limit expires."). Worked examples given use policy `ladder-view` with `X-Rate-Limit-Client: 10:5:10`. — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Sanction: "Exceeding these limits frequently will result in your application access being revoked." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- **Invalid Requests Threshold:** "Applications (and users) that make too many invalid requests in a short period of time will be restricted from further access to our service."; "Invalid requests include any response codes in the HTTP 4xx range. This includes common codes such as 401 (Unauthorized), 403 (Forbidden), and 429 (Too Many Requests)."; "Reasonable attempts must be made in order to avoid passing the threshold." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Stability disclaimer: "the details in this document may change without notice as we adjust to protect our backend from being overloaded." and "We make no guarantee of website uptime, or that API endpoints will remain available indefinitely." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)

### Inferences
- The *only* written text in GGG's corpus that is favourable to a desktop price-check tool is the tier-2 classification plus the overlay rules. Those rules read as a conditional permission: reading the clipboard is contemplated and bounded ("once per manual key press"), not forbidden.
- "Reading from the screen or clipboard may only be performed once per manual key press" is a hard architectural constraint: continuous clipboard polling, clipboard-change watchers, or OCR loops are outside the written permission. A hotkey-triggered single read is inside it.
- The macro rules and the overlay rules are separate and cumulative. A tool that only reads and displays never engages the macro rules at all; a tool that also sends a whisper or `/hideout` enters the macro rules and is capped at one game-affecting action per manual invocation.

### Gaps
- The docs page carries no last-modified header or visible date; recency is established only via the Changelog (see Q7).

---

## Q3. VERIFIED 2026-11-22: is OAuth client registration still closed?

### Takeaway
**Yes — still closed.** The live Developer Docs page on 2026-11-22 states, under the heading "Registering your Application", exactly: "We are currently unable to process new applications." There is no application form, no email address for applications, and no stated reopening date. Registration has **not** reopened.

### Cited Findings
- Verbatim, under "Getting Started" → "Registering your Application": "We are currently unable to process new applications." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index), fetched 2026-11-22.
- The only adjacent sentence is management-only, not creation: "You can manage applications owned by your account by visiting the Manage applications link in your profile." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index), fetched 2026-11-22.
- Prerequisites listed before that line (still phrased as if registration existed): applicants must have "A basic understanding of what OAuth is and what it lets you do", have "Read and understand the different types of OAuth clients, as well as the difference in grant types available", and have "Read the list of scopes and confirm that they provide access to the resources you are interested in." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- `https://www.pathofexile.com/my-account/applications` returns **HTTP 401** (exists, login required) when fetched unauthenticated on 2026-11-22; `https://www.pathofexile.com/my-account/applications/new` also returns **HTTP 401**; `https://www.pathofexile.com/developer/applications` returns **HTTP 404**. — direct HTTP probes, 2026-11-22.
- The Authorization page confirms the self-service surface that *does* exist is for review/revocation only: "Users can review and revoke their tokens on their profile's applications page at any time." and, for client-credentials tokens, "They can still be revoked manually via your application settings page." — [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)

### Inferences
- The `/my-account/applications/new` path returning 401 rather than 404 is weak evidence that a creation route exists behind login, but 401 is this site's generic unauthenticated response and proves nothing about whether the form is enabled. **No conclusion should be drawn from it.** The written policy is unambiguous: new applications are not being processed.
- Practical consequence for this project: there is **no documented route to a `client_id` today**. Any design that requires OAuth (and therefore any design that reads private stashes via `account:stashes`, or consumes the public stash river via `service:psapi`) is blocked at the credential step unless the user already owns a registered application.

### Gaps
- Whether the self-service "Manage applications" page currently shows a working "create application" button **could not be verified** — it requires an authenticated account and I have no credentials. The user can check this themselves at https://www.pathofexile.com/my-account/applications while logged in; that is the single remaining unknown on this question.
- No forum announcement or dated statement explaining *when* registration closed, or any reopening timeline, was found. The docs give no date for the closure.

---

## Q4. OAuth scopes, and what the docs say each grants

### Takeaway
The docs list six `account:*` scopes and six `service:*` scopes. `account:stashes` grants private stash + item reading via user consent; `service:psapi` grants the public stash river and is **structurally unavailable to desktop apps** — service scopes require a confidential client with the `client_credentials` grant, and the docs state outright that public (desktop) clients "Cannot use any service:* scopes."

### Cited Findings — scope list (verbatim descriptions)
Accounts:
- "account:profile — for access to the account's basic profile information."
- "account:leagues — for viewing the account's available leagues (including private leagues)."
- "**account:stashes — for viewing the account's stashes and items.**"
- "account:characters — for viewing the account's characters and inventories."
- "account:league_accounts — for viewing the account's allocated atlas passives."
- "account:item_filter — for managing the account's item filters."
— [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)

Services — introduced by the line "Requires a confidential client with the client_credentials grant type.":
- "service:leagues — for fetching leagues."
- "service:leagues:ladder — for fetching league ladders."
- "service:pvp_matches — for fetching PvP matches."
- "service:pvp_matches:ladder — for fetching PvP match ladders."
- "**service:psapi — for access to the Public Stash API.**"
- "service:cxapi — for access to the Currency Exchange API." with the note "Note that this API can also be accessed publicly. See the Currency Exchange API reference for details."
— [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)

A further scope appears only in the API Reference, not in the scope list: "Guild Stashes (PoE1 only) — Required scope: account:guild:stashes (on special request)" — [Developer Docs, reference](https://www.pathofexile.com/developer/docs/reference)

OAuth server endpoints also gate two scopes: `/oauth/token/revoke` "(requires the oauth:revoke scope)" and `/oauth/token/introspect` "(requires the oauth:introspect scope)". — [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)

### Cited Findings — documented eligibility (client types)
- "OAuth 2.1 defines two client types which we support in different ways. The vast majority of applications should use a Confidential Client." — [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)
- **Confidential clients:** "A confidential client is backend by a secure server controlled by the application owner." They "May use any of the available grant types. Any redirect URIs must point to a secure (HTTPS-enabled) URI with a registered domain controlled by the application owner. We cannot accept IP addresses or localhost domains even for in-development projects."; access tokens "last for 28 days and refresh tokens that last for 90 days"; "Usually have individual rate-limits per client." — [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)
- **Public clients:** "A public client is one without a method of securely storing any credentials. The most common example of this is an application that runs on the user's desktop." They "Must only use the Authorization Code (with PKCE) grant type and must use a local redirect URI (ie. http://127.0.0.1:8080/callback)."; "**Cannot use any service:\* scopes.**"; access tokens last "10 hours and refresh tokens that last for 7 days"; "Have shared rate-limits between all other public clients."; "Display a warning message to the end user when authorizing; stating that the authenticity of the request is unverifiable." — [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)
- Tier-2 rule cross-reference: desktop executables "must use a public OAuth client if interacting with our APIs." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
- Client-credentials warning: "tokens generated via the Client Credentials method have their identity set to the registered owner of the application. This means that tokens with appropriate scopes will have access your account details. It is extremely important that you keep your client credentials a secret." — [Developer Docs, authorization](https://www.pathofexile.com/developer/docs/authorization)

### Cited Findings — the endpoints those scopes unlock (PoE 1)
- "Account Stashes (PoE1 only) — Required scope: account:stashes". Endpoints: `GET /stash[/<realm>]/<league>` returning "A list of all the account's stash tabs in the specified league. This includes sub-tabs and stash tabs in folders."; and `GET /stash[/<realm>]/<league>/<stash_id>[/<substash_id>]` returning "The stash object requested, including any contained items (when applicable)." — [Developer Docs, reference](https://www.pathofexile.com/developer/docs/reference)
- "Public Stashes (PoE1 only) — Required scope: service:psapi". Endpoint `GET /public-stash-tabs[/<realm>]`, returning "A stream containing all public stashes in all leagues for the given realm. Providing the next_change_id as the id query parameter serves as a way to paginate through current and newly listed stashes." With: "If the stashes array is empty then you have reached the end of the stream."; and "Please note: There is currently a 5-minute delay on results using this API." — [Developer Docs, reference](https://www.pathofexile.com/developer/docs/reference)
- The Currency Exchange endpoint is the one genuinely open, no-credential data source: "Currency Exchange — Public API", `GET https://web.poecdn.com/api/currency-exchange[/<realm>][/<id>]`, where "realm can either be xbox, sony, or poe2. If omitted then the PoE1 PC realm is assumed", returning "aggregate Currency Exchange trade history across all leagues grouped into hourly digests", keyed by a `next_change_id` that is a "unix timestamp truncated to the hour". The docs add: "If the next_change_id is the same unix timestamp as the one you passed in, then you have reached the current end of the stream. You should wait until the next hourly boundary before calling this endpoint again." — [Developer Docs, reference](https://www.pathofexile.com/developer/docs/reference)

### Inferences
- **A desktop price checker can never legitimately hold `service:psapi`.** Two written rules combine to forbid it: desktop apps "must use a public OAuth client", and public clients "Cannot use any service:* scopes". Any desktop tool consuming the public stash river is therefore either (a) using someone's confidential credentials embedded in a binary — expressly against "Don't embed keys or credentials in a distributed binary" — or (b) going through a server-side relay the author controls, which is the architecture GGG actually prefers.
- `account:stashes` *is* reachable from a desktop app (public client, Authorization Code + PKCE, loopback redirect) — but only with a `client_id`, which is currently unobtainable (Q3).
- The Currency Exchange CDN endpoint is the only PoE 1 pricing-relevant data source the docs describe as publicly accessible with no OAuth and no registration. It is hourly-aggregated market data, not per-listing data. This is the one architecture that is clean today under the written rules.

### Gaps
- The docs state no additional *merit* or *review* criteria for `account:stashes` or `service:psapi` beyond client type and grant type — i.e. **no written eligibility test** (no "must be an established tool", no user-count threshold). The only written gates are client type, grant type, and the closed registration queue. `account:guild:stashes` is the sole scope marked "(on special request)"; the docs do not say how to make that request.
- No written rate-limit *numbers* for the stash or PSAPI endpoints: the docs say limits are "dynamic", and the only concrete figures given are in the `ladder-view` worked example.

---

## Q5. Official GGG staff statements on third-party tools, overlays and API use

### Takeaway
Every staff statement I could retrieve is from **GGG support staff in the forum's "Help and Information" section**, and all of them say essentially the same four things, in near-identical boilerplate repeated across five years: GGG does not encourage third-party tools; GGG will not rule on any specific tool; GGG cannot guarantee a tool stays allowed; and the user should avoid anything that automates, does more than one action per keypress, or surfaces information "that isn't normally visible". **I found no post by Bex_GGG, Rory_GGG, Chris Wilson, Jonathan Rogers, Novynn, Mark_GGG, Community_Team or CommunityTeam_GGG on this topic.** All statements are PoE 1 forum threads.

### Cited Findings (all fetched 2026-11-22 via the forum's staff-post filter)
- **JC_GGG, 11 May 2021** — thread "About Awakened PoE Trade": "In general, we do not encourage the creation or use of third-party tools because they provide advantages for players that use them. I'm afraid, due to the dynamics of our policies, we're unable to guarantee if a tool is allowed or would remain allowed in the future." Plus: "I would recommend refraining from creating or using any program that automates gameplay or does more than one action with a keystroke or mouse click as well as anything that interacts with the game client to provide an advantage over other players or provide information that isn't normally visible." — [forum/view-thread/3116826 (staff filter)](https://www.pathofexile.com/forum/view-thread/3116826/filter-account-type/staff)
- **Chulainn_GGG, 4 Jan 2024** — thread "Is Awakened Poe Trade allowed?": "In general, we do not encourage the creation or use of any third-party tools because they may provide advantages for players that use them. I'm afraid, due to the dynamics of our policies, we would be unable to guarantee if a specific tool is allowed or would remain allowed in the future." — [forum/view-thread/3474232 (staff filter)](https://www.pathofexile.com/forum/view-thread/3474232/filter-account-type/staff)
- **Nichelle_GGG, 15 Feb 2024** — thread "Trade macro and awakened poetrade", replying to a player explicitly asking for "an official answer ... aside from posted ToS": "Unfortunately, we cannot comment on the legality of third-party tools, as we aren't able to thoroughly and accurately check exactly how they work." Followed by the same boilerplate and the same "automates or does more than one action with a keystroke or mouse click ... or provide information that isn't normally visible" recommendation. — [forum/view-thread/3491960 (staff filter)](https://www.pathofexile.com/forum/view-thread/3491960/filter-account-type/staff)
- **ThomasK_GGG, 18 Mar 2024** — thread "[Guide] What a macro is allowed to do in Path of Exile": same boilerplate, plus "I would recommend refraining from creating or using any programs that automates or does more than one action with a keystroke or mouse click as well as anything that interacts with the game client to provide an advantage over other players or provide information that isn't normally visible." Signature block: "Need help with anything? Feel free to contact us at: support@grindinggear.com" — [forum/view-thread/2077975 (staff filter)](https://www.pathofexile.com/forum/view-thread/2077975/filter-account-type/staff)
- **Drew_GGG, 13 Aug 2024** — thread "Awakened PoE Trade can be use?": same boilerplate plus the "automates gameplay or does more than one action with a keystroke or mouse click" recommendation. — [forum/view-thread/3552928 (staff filter)](https://www.pathofexile.com/forum/view-thread/3552928/filter-account-type/staff)
- **CoryA_GGG, 15 Dec 2024** — thread "Is Overwolf PoE Overlay II Bannable?" (an overlay-specific question): "In general, we do not encourage the creation or use of third-party tools because they may provide advantages for players that use them. I'm afraid that we're unable to guarantee if a tool is allowed or would remain allowed in the future." **Nothing further — the staff reply does not address overlays as a category at all.** — [forum/view-thread/3637217 (staff filter)](https://www.pathofexile.com/forum/view-thread/3637217/filter-account-type/staff)

A web search also surfaced a staff formulation that policy on client-interacting programs "is the same as it has been for the last eight or so years", and a reply declining to comment on the Mappy stash-search overlay ("not able to comment on any 3rd party tools"), but I could not pin those to a specific post URL, author and date within my budget — treat them as **unverified** pending direct confirmation. — [WebSearch summary of pathofexile.com forum threads](https://www.pathofexile.com/forum/view-thread/2996559/page/2)

### Inferences
- The staff line is deliberately non-committal and, crucially, **adds a criterion that appears nowhere in any written policy**: "provide information that isn't normally visible". This phrase recurs in four of six quoted posts and is the most restrictive thing GGG staff say about read-only tools. It is advisory wording in a support reply, not policy text — but it is the closest thing to a staff-stated limit on information display.
- Note the mismatch: a price checker's whole function is to show a value that is *not* visible in-game. Under the Developer Docs' overlay rules that is permitted (bounded only by "once per manual key press"); under the support boilerplate's "information that isn't normally visible" it is discouraged. These two GGG-authored texts pull in opposite directions. **This is category (4): ambiguous wording readable both ways.**
- Support staff explicitly disclaim the ability to adjudicate specific tools. There is therefore no route to written pre-approval for a named tool; the written rules in the Developer Docs are the only safe harbour.

### Gaps
- **No statement found from any named GGG developer or community manager** (Bex_GGG, Rory_GGG, Chris Wilson, Jonathan Rogers, Novynn, Mark_GGG, Community_Team/CommunityTeam_GGG) on third-party tool policy. "Novynn" appears in the Developer Docs only as the placeholder `username` in OAuth response examples — not as an author of any policy statement.
- I did not find any **Announcements**-forum post or patch-note entry that states third-party tool policy. The policy appears to live exclusively in the legal pages and the Developer Docs.
- No current community-manager identity could be established from the sources I reached.

---

## Q6. Where does GGG's own writing draw the line? (gameplay automation vs. reading and displaying)

### Takeaway
The community's understanding — automate input = bannable, read and display = accepted — **is substantially supported by GGG's own written text**, but only in the Developer Docs, and in a narrower form than the community states. The written line is not "reading is fine"; it is "reading is fine **if the user knows what you do with the data, and at most one read per manual key press**". Anything continuous or event-driven falls outside the written permission.

### Cited Findings — the written line, assembled
1. **Explicitly forbidden in writing**: executables that "interact with the game or game files" — "strictly against our Terms of Use (sections 7b, 7c, 7i)", with "immediate account termination". — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
2. **Explicitly permitted in writing (conditionally)**: "Executable apps that run independently from the game ... While not encouraged, these are permitted." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
3. **The input-automation line, in writing**: macros "must be invoked manually by the user", with "timers, reacting to file changes, or from reading the screen" named as disallowed triggers; one set function per invocation; "may only perform one action that interacts with the game". — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
4. **The reading line, in writing** — and this is the specific language the question asks for on overlays, screen reading and the clipboard: "Apps that use information sourced from the screen, the clipboard, or the game's logs must follow our overlay rules: You may only read this information as long as the user is aware of what you are doing with that data. Reading from the screen or clipboard may only be performed once per manual key press." — [Developer Docs, index](https://www.pathofexile.com/developer/docs/index)
5. **The tension**: support staff repeatedly advise against anything that would "provide information that isn't normally visible" (JC_GGG 2021; Nichelle_GGG 2024; ThomasK_GGG 2024; Drew_GGG 2024) — a criterion absent from all written policy. — [forum/view-thread/3491960 (staff filter)](https://www.pathofexile.com/forum/view-thread/3491960/filter-account-type/staff)

### Classification, per the user's four-way test
- **(1) Explicitly permitted in writing**: a standalone desktop app that does not touch the game or its files; reading the clipboard, the screen, or the client log, once per manual key press, with the user informed of the data's use; using documented API endpoints with the prescribed `User-Agent`; displaying the "This product isn't affiliated with or endorsed by Grinding Gear Games in any way." notice.
- **(2) Explicitly forbidden in writing**: reading/writing game memory or game files; injecting input in any automated (timer, file-watch, screen-trigger) way; more than one game-affecting action per manual invocation; more than one screen/clipboard read per manual key press; embedding OAuth credentials in a distributed binary; a public (desktop) client using any `service:*` scope; reverse-engineering undocumented endpoints; bypassing rate limits; presenting the tool as official or GGG-authorised; generating commercial revenue from GGG IP.
- **(3) No written rule found either way**: reading the clipboard **from a web page** rather than a desktop overlay (the overlay rules are written under the "executable apps" tier); a tool that is purely read-only and makes **zero** API calls (e.g. parsing clipboard text against a locally cached price table) — no written rule addresses this configuration at all; caching or redistributing price data derived from the API; whether an always-on-top display window that never reads anything is governed by "overlay rules"; what the "once per manual key press" counter means for a single key press triggering several HTTP requests (the rule constrains the *read*, not the requests). **The word "scrape" or "scraping" appears in neither document.** Scraping the trade site HTML is not addressed by name — it is covered only indirectly by Terms 7c/7f and by the Developer Docs' statement that only documented resources are supported.
- **(4) Ambiguous, readable both ways**: Terms 7c ("any automated software or 'bots' in relation to your access or use of the Website") and 7f ("data gathering and extraction tools or software to extract information from the Website") read literally cover **any** program that queries pathofexile.com, including a fully compliant API client — the Developer Docs are what make this workable, via Terms clause 22. The support boilerplate's "information that isn't normally visible" vs. the Developer Docs' bounded permission to read screen/clipboard/logs is the second genuine ambiguity, and it goes directly to whether a price-checker's core function is blessed or merely tolerated.

### Inferences
- The safest written-compliant architecture for a PoE 1 price checker, derived purely from GGG's text: a hotkey-triggered single clipboard read; a visible statement of what is done with that data; a disclaimed non-affiliation notice; the prescribed `User-Agent`; strict `X-Rate-Limit-*`/`Retry-After` obedience; no input automation; no `service:*` scope in a desktop binary; and no use of undocumented endpoints.
- The strongest structural risk is **not** the overlay rules — it is credentials: registration is closed (Q3), and the two realistic data sources for live PoE 1 prices (the trade site's own search endpoints, and PSAPI) are respectively undocumented and off-limits to desktop clients.

### Gaps
- No written GGG text uses the term "price check" or "price checker"; the category is never named or blessed by name.
- No written GGG text addresses the official **trade site's** internal search endpoints (`/api/trade/...`) at all. By the "Available Resources" rule they are not in the API Reference, which places them in the "any other internal website APIs" bucket whose access "will be denied", and whose reverse-engineering is called a Terms 7i breach. That is an inference from two written rules, not an explicit statement about trade endpoints.

---

## Q7. Support/help-centre article; recency; what changed since 2026-08-23

### Takeaway
There is **no support or help-centre knowledge-base article** on third-party programs — `support.pathofexile.com` does not resolve, and `pathofexile.com/support` is a contact form with no policy text. The role is filled by the forum "Help and Information" section and by `support@grindinggear.com`. On recency: the Developer Docs **did change after the project's 2026-08-23 notes** — on 12 November 2026 GGG explicitly rewrote the macro and overlay policy wording.

### Cited Findings
- **`https://support.pathofexile.com` does not resolve** (connection failed, no HTTP status returned) when probed on 2026-11-22 — direct HTTP probe.
- `https://www.pathofexile.com/support` returns HTTP 200 but its content is rendered client-side; the server HTML contains only site navigation and no third-party-program policy text (probe + text extraction, 2026-11-22). No help-centre article could be located. — https://www.pathofexile.com/support
- The de-facto support channel for this question is the forum's "Help and Information" board (where all six staff replies in Q5 live) and the email in ThomasK_GGG's signature: "support@grindinggear.com". — [forum/view-thread/2077975 (staff filter)](https://www.pathofexile.com/forum/view-thread/2077975/filter-account-type/staff)
- **CHANGE SINCE 2026-08-23 — the key recency finding.** The Developer Docs changelog's newest entry reads: "3.30.0: Covens of Umbra" and its first bullet is, verbatim: "**Updated the Policy wording around macros and overlays to be clearer about when they apply.**" The entry is stamped "Updated 12/11/2026." — [Developer Docs, changelog](https://www.pathofexile.com/developer/docs/changelog)
- Other 3.30.0 changelog bullets are schema-only (new `wickerman` property on Character, `variants` on LeagueAccount, `isMultipleChoiceVariant`/`variantEffects`/`connections` on PassiveNode, `rareonly` on ItemMod, `doubleCorrupted`/`unidentifiedTier` now appearing in PoE1). — [Developer Docs, changelog](https://www.pathofexile.com/developer/docs/changelog)
- Preceding entries, for dating context: "3.29.3b ... Updated 05/09/2026."; "3.29.0: Curse of the Allflame ... Updated 21/07/2026." — the 3.29.0 entry includes "The Currency Exchange endpoint is now available publicly. Use the CDN API to load cached copies of the data." — [Developer Docs, changelog](https://www.pathofexile.com/developer/docs/changelog)
- **Current PoE 1 version and league**, established from GGG's own docs: version **3.30.0**, league name **"Covens of Umbra"** (changelog heading, dated 12/11/2026). — [Developer Docs, changelog](https://www.pathofexile.com/developer/docs/changelog)
- PoE 1 vs PoE 2 separation is explicit in the reference: endpoints are marked "(PoE1 only)" — including Account Stashes, Public Stashes, League Accounts, Guild Stashes, PvP Matches and Account Leagues — while Build Planner is "(PoE2 only)". — [Developer Docs, reference](https://www.pathofexile.com/developer/docs/reference)

### Inferences
- The macro/overlay wording quoted throughout this document is the **post-12-November-2026 revision**, i.e. newer than the project's 2026-08-23 notes. If those notes quoted the macro or overlay bullets, **they should be re-checked against the text quoted in Q2 above**, which is the current wording as of 2026-11-22. GGG describes the change as a clarification of "when they apply" rather than a substantive loosening or tightening, so the practical effect is likely that the rules' scope (which application tier they bind) was made explicit — consistent with the current page placing both rule sets under the "Executable apps that run independently from the game" tier.
- The public Currency Exchange endpoint became public only at 3.29.0 (21/07/2026) — recent enough that it may post-date or barely pre-date the project's notes, and it is the most policy-clean PoE 1 price data source available without credentials.

### Gaps
- I could not retrieve the *previous* version of the macro/overlay wording to diff it against the current text (no archived copy fetched within budget). The nature of the 12/11/2026 clarification is therefore known only from GGG's one-line changelog description.
- The Terms of Use carries no date, so whether it changed since 2026-08-23 **cannot be determined** from the page.
- I did not verify PoE 1 3.30.0 patch notes independently of the developer changelog; the version/league names above rest on that single (but official, first-party) source.

---

## Cross-cutting note for the report writer

Quotations above are short operative clauses reproduced for compliance analysis, each attributed with its source URL; the full documents are not reproduced here. Every fetch was performed on **2026-11-22**. The one question I could not close is whether the logged-in "Manage applications" page currently offers application creation — that requires the user's own account, and the written policy ("We are currently unable to process new applications.") remains the only public statement.
