# Architecture, UI Technology and Windows Distribution Strategy for a Local Web-App + Game Overlay (as of 2026-11-22)

Scope: evaluating a PoE 1 map pricing tool — Python 3.11+, Streamlit UI, SQLite, scikit-learn +
CatBoost + LightGBM + XGBoost, separate click-through tkinter overlay with `RegisterHotKey`,
~89 modules / ~1030 tests, shipped as a `.bat` that builds a venv beside itself.

Research caveat applying to everything below: a large share of the 2026 "X vs Y" comparison
material returned by search is low-quality SEO content with inconsistent and uncited numbers.
Where I could only find that class of source, I say so. Primary sources (official docs, GitHub
repos, package manifests, GitHub release API) are marked as such and should be preferred by the
report writer.

---

## Q1. Is Streamlit right for this use case?

### Takeaway

Two of the three specific frictions the author hit have been **fixed in current Streamlit** (1.67.0,
released 2026-11-05): `st.tabs` now takes `key`/`on_change`/`bind` and the active tab is readable
and settable through `st.session_state`, and widget state can now survive non-render via
`persist_state`. What remains structurally wrong for this project is not widget state — it is that
Streamlit is a *server-rendered, rerun-the-whole-script* model with a browser as its only client,
no supported way to drive a native always-on-top overlay, and a cold start measured in seconds.
Streamlit is a genuine liability for the overlay half and for long-running background work; it is
merely unfashionable for the analysis/tables half, where it is actually quite good.

### Cited Findings

**The two named frictions are now addressable (this is the single most important finding in this section).**

- `st.tabs` supports `key`, `on_change` and `bind`. Official docs: "setting a key lets you read or
  update the active tab label via st.session_state[key]" — but only when `on_change` is `"rerun"` or
  a callable, or when `bind="query-params"` is set. With the default `on_change="ignore"` the tabs
  "don't track state", every tab's content runs, and each tab's `.open` returns `None` —
  [Streamlit docs, st.tabs](https://docs.streamlit.io/develop/api-reference/layout/st.tabs)
- `bind="query-params"` syncs the active tab to a URL query parameter named by `key`, requires
  `key`, requires non-empty unique labels, ignores and strips invalid values, and makes the tabs
  track state even with `on_change="ignore"`. A bound parameter "can't be set or deleted through
  st.query_params" and "can only be programmatically changed through st.session_state" —
  [Streamlit docs, st.tabs](https://docs.streamlit.io/develop/api-reference/layout/st.tabs)
- The widget-cleanup behaviour the author hit is real and documented: when a widget isn't called
  during a script run, "none of its parts are retained, including its value in `st.session_state`",
  and "Even temporarily hiding a widget causes it to reset when it reappears; Streamlit will treat
  it like a new widget." At the end of each run Streamlit deletes data for widgets not on screen,
  and on a page switch it deletes the previous page's widget data —
  [Streamlit docs, widget behavior](https://docs.streamlit.io/develop/concepts/architecture/widget-behavior)
- But there is now a documented escape hatch: `persist_state` with a `key` — `"page"` keeps the
  value while the user stays on the page, `"session"` keeps it across page switches ("The value is
  preserved for the entire session"). `bind="query-params"` persists across page switches regardless
  of `persist_state`. Caveat from the same page: `persist_state` does **not** prevent a reset when the
  widget's *identity* changes (a `key` protects against most parameter changes, but some — e.g. a
  slider's `min`/`max` — still reset it) —
  [Streamlit docs, widget behavior](https://docs.streamlit.io/develop/concepts/architecture/widget-behavior)
- Streamlit 1.67.0 (latest at time of research) was released 2026-11-05. Its changelog includes
  "Keyed elements stay mounted when a rerun inserts content above them, so they keep their state,
  focus, and in-progress input (#17804)" and `st.feedback` gaining `persist_state` and `bind`
  (#17621) — [Streamlit changelog](https://docs.streamlit.io/develop/quick-reference/changelog)
- The changelog also lists an experimental `server.enableAgentApi` option exposing an app over HTTP
  or an MCP endpoint — [Streamlit changelog](https://docs.streamlit.io/develop/quick-reference/changelog)

**What Streamlit still makes hard.**

- Every widget interaction re-executes the whole script, which restarts ongoing computation unless
  it is cached or moved off the script thread; Streamlit does not natively keep background tasks or
  event loops alive across reruns — [Streamlit forum, event loop in session state](https://discuss.streamlit.io/t/can-i-store-event-loop-and-or-tasks-futures-in-session-state/121596)
- Long synchronous work freezes the UI: "If a click triggers a function that takes ten seconds, the
  UI freezes for ten seconds" — [Manning, Build Python Web Apps with Streamlit, ch.4 preview](https://www.manning.com/preview/build-python-web-apps-with-streamlit/chapter-4)
- Fragments do not fully solve it: a single indivisible long operation (e.g. a 30-second training
  call) blocks a new rerun until its thread finishes ("the entire app will wait for that thread to
  finish before a new rerun can start") — [Streamlit forum, st.fragment rerun blocks app](https://discuss.streamlit.io/t/st-fragment-rerun-blocks-app/86360)
- A rerun can terminate a script mid-flight. A developer building a computer-use agent on Streamlit
  reported "the Streamlit script would immediately terminate so it could rerun. This was a huge
  problem, because do_computer_use_stuff very strongly assumed that it would never be interrupted" —
  [Pushing the boundaries of Streamlit](https://twitchard.github.io/posts/2024-11-27-streamlit.html)
- The community-standard remedy for anything long-running is to split the app: "split your code into
  the Streamlit front-end and a processing backend" — [Streamlit forum, long-running tasks](https://discuss.streamlit.io/t/run-long-running-tasks-like-embedding-of-large-docs-asynchronously-without-blocking-ui-and-notify-user-when-finished/51036)
- Cold start is seconds, not milliseconds. Forum reports: "a 3-4 second load [is] about what I would
  expect for a streamlit application"; one user measured 8 s for a small app; one reports a 1-2 s lag
  before any user code runs at all, having timed every piece of their own code at under 0.1 s, i.e.
  framework startup overhead, not app logic —
  [Improving load speed](https://discuss.streamlit.io/t/how-to-improve-streamlit-app-loading-speed/52836);
  [8-second initial load](https://discuss.streamlit.io/t/why-does-my-streamlit-app-take-around-8-seconds-to-load-at-the-beginning-even-though-im-not-using-large-datasets-is-there-a-solution-to-reduce-the-initial-loading-time/82092);
  [Inconsistent full script run times](https://discuss.streamlit.io/t/full-script-run-times-are-very-inconsistant/49060)
- Streamlit's own performance guidance is to cache expensive loads and render the page before slow
  work completes — [Optimizing app performance](https://docs.streamlit.io/develop/concepts/design/performance)
- Separately reported but **unverified**: a search summary of Streamlit docs mentions an
  `st.background_task()` API as the recommended handling for an indivisible long operation. I could
  not confirm this API on the changelog page I fetched. Treat as a lead to verify, not a fact.

### Inferences

- The project's two headline Streamlit complaints are now **version problems, not framework
  problems**. Before any rewrite is costed, the author should upgrade Streamlit to 1.67.x and retest
  tab control (`key` + `on_change="rerun"` or `bind="query-params"`) and widget persistence
  (`persist_state="session"`). If that clears the friction, the case for a UI rewrite rests on the
  overlay, startup time and distribution — which are different arguments and should be argued
  separately.
- The genuine liabilities are structural and will not be patched away: (a) the overlay cannot be
  driven from Streamlit at all, so the app is already two processes; (b) a rerun-per-interaction
  model is a bad fit for a tool whose value proposition is "price this map in under a second while
  the game is running"; (c) the browser is a mandatory, visible third window for a user whose game
  is fullscreen.
- Streamlit remains a good fit for the *non-overlay* surface: model diagnostics, history tables,
  bulk pricing, settings. If a rewrite happens, the honest framing is "replace the hot path and the
  overlay; keep or demote Streamlit to an admin/analysis console", not "Streamlit is wrong".

### Gaps

- No authoritative published figure for Streamlit cold-start on a local machine; all numbers above
  are user reports on varied hardware with varied import graphs. The author should measure their own
  app with `python -m cProfile` rather than rely on these.
- Could not confirm whether `st.background_task()` exists.
- Found no evidence Streamlit publishes any guidance or support for desktop packaging / long-running
  local-app use; nothing in the changelog or docs I reached addresses it.

---

## Q2. Alternatives compared

### Takeaway

For this project the realistic shortlist is two options, and they differ mainly in how much Rust/JS
the author is willing to own: **(A) keep the Python core as a localhost service and put a native or
near-native Windows overlay + shell in front of it**, or **(B) Tauri 2 with the Python core as a
sidecar**. Electron is the proven-by-peers choice (every successful PoE companion overlay uses it)
and the one with the least unknown overlay risk, at ~100 MB installed. A full native-GUI rewrite
(Qt/Avalonia/WinUI/ImGui) buys the best overlay control and the worst preservation of the existing
codebase. "Browser + FastAPI" is the cheapest change but does not solve the overlay or the
"non-technical user" problem.

### Cited Findings

**Tauri vs Electron size — real numbers, with a quality warning.**

- Best-controlled measurement found: Better Stack (July 2026 update) reports Electron 323 MB,
  Tauri 57 MB, Electrobun 418 MB, Deno 111 MB for comparable apps —
  [Better Stack: Tauri vs Electron vs Deno vs Electrobun](https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun/)
- Commonly repeated but uncited range: Tauri 2-10 MB vs Electron 80-200 MB; one article claims a
  3.2 MB Tauri hello-world vs 85 MB for Electron 34.x, and 8.6 MB vs 244 MB for a six-window app.
  These figures recur across multiple low-quality 2026 SEO pages with mutually inconsistent Electron
  versions (25, 33, 34, 44), and the 8.6 MiB/244 MiB pair appears to be recycled from a 2025-era
  demo, so the "2026" labelling is doubtful —
  [Better Stack comparison](https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun/)
  (Better Stack is the source I would trust of these; treat the ranges as folklore.)
- Mechanism, which is not in dispute: Tauri uses the OS webview (WebView2 on Windows) instead of
  bundling Chromium — [Better Stack comparison](https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun/)

**Real-world Electron-plus-overlay installer size, from the closest possible comparable.**
- Awakened PoE Trade v3.30.102 (published 2026-11-19), GitHub release assets, exact bytes:
  Windows installer `Awakened-PoE-Trade-Setup-3.30.102.exe` = **103,729,556 bytes (~103.7 MB)**;
  Windows portable `Awakened-PoE-Trade-3.30.102.exe` = 102,869,080 bytes; Linux AppImage =
  125,751,026 bytes; macOS universal dmg = 214,909,398 bytes —
  [GitHub Releases API, SnosMe/awakened-poe-trade](https://api.github.com/repos/SnosMe/awakened-poe-trade/releases/latest)
  (This is the number to beat. ~104 MB is what a successful, widely used PoE overlay ships to
  non-technical gamers today.)

**Tauri + Python sidecar is an established pattern.**
- Tauri v2 ships external executables declared under `externalBin`; the shell plugin must
  explicitly allow each one or `spawn()` returns "not allowed". Target-triple naming applies, and
  the process must be stopped on app exit — [Tauri v2 sidecar docs](https://v2.tauri.app/ja/develop/sidecar/)
  (the English page is at v2.tauri.app/develop/sidecar/; the search only surfaced the ja locale)
- Typical implementation: PyInstaller compiles the FastAPI backend to a single binary, Tauri builds
  the installers; "Build: PyInstaller bundles Python backend into single binary, Tauri creates native
  installers" — [tauri-fastapi-full-stack-template](https://awesome.ecosyste.ms/projects/github.com%2Ffudanglp%2Ftauri-fastapi-full-stack-template)
- Lifecycle and robustness notes from practitioners: Tauri starts the sidecar on launch and
  terminates on close; if the sidecar crashes the UI survives but the backend is gone, so the
  frontend must handle that; most examples bind FastAPI to 127.0.0.1 and some add a per-launch auth
  token ("A per-launch auth token secures all HTTP traffic between the frontend and the sidecar") —
  [Evil Martians: Rust + Tauri + sidecar](https://evilmartians.com/chronicles/making-desktop-apps-with-revved-up-potential-rust-tauri-sidecar);
  [Bundling a CLI binary as a Tauri v2 sidecar](https://dev.to/chenxxpro/bundling-a-cli-binary-as-a-tauri-v2-sidecar-architecture-and-lessons-from-building-moonproxy-21n3)
- Reference implementations exist: a Next.js + Python FastAPI sidecar example, and GeoLibre, which
  runs a FastAPI sidecar on localhost for heavy geospatial work —
  [example-tauri-python-server-sidecar](https://awesome.ecosyste.ms/api/v1/projects/github.com%2Fdieharders%2Fexample-tauri-python-server-sidecar);
  [GeoLibre Python sidecar](https://instagit.com/opengeos/GeoLibre/how-does-python-sidecar-fastapi-integrate-desktop-app/)

**A lighter Python-native web shell exists.**
- pywebview does "not bundle a heavy GUI toolkit or web renderer with it keeping the executable size
  small", supports a WebView2 backend on Windows, and can optionally bundle the WebView2 runtime via
  `webview.settings['WEBVIEW2_RUNTIME_PATH']`. Without bundling it depends on the machine's WebView2
  runtime, which is normally present on Windows 10/11 — [pywebview](https://github.com/r0x0r/pywebview)

### Inferences

- **Option 2 (FastAPI/Litestar + JS in a browser)** is the lowest-risk, highest-reuse change: the
  ~89 modules and ~1030 tests survive essentially intact, the service becomes testable over HTTP,
  and the "long-running work blocks the UI" problem disappears because the server is no longer the
  renderer. It does nothing for the overlay and nothing for distribution, so on its own it is an
  incomplete answer — but it is the *enabling* step for every other option, including Tauri and a
  native overlay. This is the key structural insight: FastAPI-ising the core is not an alternative
  to Tauri/Electron/native, it is a prerequisite for all of them.
- **Option 3 (Tauri)** gives the smallest shell, native always-on-top window control from Rust, and
  a real installer, at the cost of learning Rust-adjacent tooling and owning the sidecar lifecycle.
  Combined with a PyInstaller'd or ONNX-slimmed Python sidecar it is the only route I found to a
  plausibly sub-60 MB installer that keeps the Python core.
- **Option 4 (Electron)** is the de-risked choice precisely because the overlay problem is already
  solved in public, reusable, battle-tested npm packages used by the leading PoE tools (see Q3/Q6).
  ~104 MB is the proven cost. If the priority is "ship an overlay that works for gamers", this is the
  path of least surprise.
- **Option 5 (native GUI)** discards the most and gains the least for this app: the tool's UI is
  tables, charts and forms, which the web stack does better and faster. Dear ImGui is attractive for
  the overlay *specifically* (it is designed to be drawn into a graphics context at frame rate) but a
  poor fit for the data-heavy console. PySide6 keeps Python but ships Qt, which is substantially
  larger than a webview shell (see gap below).
- **Option 6 (native overlay process + local HTTP/IPC service)** is, on this evidence, the best
  architecture *regardless of which UI stack wins*, because it is what the existing app already
  accidentally is (tkinter overlay + Streamlit server) and what the comparable tools converge on
  (Electron main process owning a native overlay window + a renderer). Formalising it — one
  Python/FastAPI service owning data, model and pricing; one thin overlay client owning hotkeys and
  the always-on-top window; one normal UI client for everything else — maximises test reuse and lets
  the overlay and console technologies be chosen independently and changed later.

### Gaps

- No published PySide6 / Qt frozen-app size figure found. The search returned nothing citable;
  I am deliberately not estimating.
- No credible head-to-head startup-time measurement for Tauri vs Electron vs a Python+pywebview
  shell on Windows 11. The Tauri/Electron comparisons I found measure bundle size, not cold start.
- Found no architecture decision record or post-mortem from any project that moved a Streamlit app
  to Tauri/Electron, so there is no direct precedent for this exact migration.
- Litestar: found no size/perf material specific to it in this research pass; the FastAPI-shaped
  argument above should apply equally but is untested for Litestar.

---

## Q3. The overlay — current best practice on Windows

### Takeaway

The current best practice for a game-companion overlay in 2026 is **a separate top-level,
always-on-top, layered + transparent window that tracks the game window via out-of-process
accessibility/WinEvent hooks** — explicitly *not* DirectX/Vulkan present-hooking and *not* DLL
injection. The leading PoE tool does exactly this, and the implementation is readable and reusable.
The one hard limitation is exclusive fullscreen: a layered window cannot draw over it, so the tool
must require borderless/windowed, which is the universal convention among these tools.

### Cited Findings

**What the actual leading PoE overlay uses (primary source: package manifests + native source).**

- Awakened PoE Trade's main process has exactly two runtime dependencies:
  `"electron-overlay-window": "4.1.0"` and `"uiohook-napi": "1.5.5"`. Dev dependencies include
  `electron: ^40.10.6`, `electron-builder: ^26.9.0`, `electron-updater: ^6.8.3`, `typescript: ^6.0.2`,
  `esbuild: ^0.28.0`, `ws: ^8.20.0` —
  [main/package.json](https://raw.githubusercontent.com/SnosMe/awakened-poe-trade/master/main/package.json)
- `electron-overlay-window`'s Windows native layer uses, verbatim from the source: `SetWinEventHook`
  with **`WINEVENT_OUTOFCONTEXT`** — "Hook flag so callbacks run in the calling process without DLL
  injection" — for `EVENT_SYSTEM_FOREGROUND`, `EVENT_SYSTEM_MINIMIZEEND`, `EVENT_OBJECT_NAMECHANGE`,
  `EVENT_OBJECT_LOCATIONCHANGE` (drives `OW_MOVERESIZE`) and `EVENT_OBJECT_DESTROY`; plus
  `GetForegroundWindow`, `GetWindowThreadProcessId`, `IsHungAppWindow` (to skip ghost windows of hung
  apps), `SetForegroundWindow` (for `ow_activate_overlay` / `ow_focus_target`), MSAA
  `AccessibleObjectFromEvent` + `IAccessible::get_accState` + `STATE_SYSTEM_FOCUSED` "since
  GetForegroundWindow can be wrong during rapid focus changes", a `SetTimer` fallback at **83 ms
  (~12 fps)** to catch foreground changes the hooks miss, `GetWindowTextLengthW`/`GetWindowTextW` +
  `WideCharToMultiByte(CP_UTF8)` for titles, and `GetClientRect` + `ClientToScreen` for geometry.
  Notably it does **not** use `SetWindowPos`, `GetWindowRect` or `DwmGetWindowAttribute` —
  [electron-overlay-window src/lib/windows.c](https://raw.githubusercontent.com/SnosMe/electron-overlay-window/master/src/lib/windows.c)
- The same source implements an explicit **UIPI (User Interface Privilege Isolation) probe**:
  `RegisterWindowMessage("ELECTRON_OVERLAY_UIPI_TEST")` then `PostMessage`; a failure with
  `ERROR_ACCESS_DENIED` indicates the target process runs at a higher integrity level —
  [electron-overlay-window src/lib/windows.c](https://raw.githubusercontent.com/SnosMe/electron-overlay-window/master/src/lib/windows.c)
  (This is the technical reason behind the folk advice to run such tools as Administrator.)
- Screen capture, where used, is plain GDI: `GetDesktopWindow`/`GetDC`, `CreateCompatibleDC`,
  `CreateDIBSection`, `BitBlt` with `SRCCOPY` — no graphics-API hooking —
  [electron-overlay-window src/lib/windows.c](https://raw.githubusercontent.com/SnosMe/electron-overlay-window/master/src/lib/windows.c)
- Library constraints (from its README): supports Windows 7-10 and Linux/X11 (EWMH:
  `_NET_ACTIVE_WINDOW`, `_NET_WM_STATE_FULLSCREEN`, `_NET_WM_NAME`); initialise once and never
  destroy the Electron window; **only one overlay window is supported**; the target stays valid even
  if its title changes; correct behaviour is guaranteed only for top-level windows. It recommends
  AccEvent and Inspect Object from the Windows SDK for development —
  [electron-overlay-window](https://github.com/SnosMe/electron-overlay-window)
- Awakened PoE Trade acquires item data by **parsing the clipboard**, not reading game memory: when
  you press Ctrl+C, PoE copies the item text to the clipboard and the app parses that text —
  [Awakened PoE Trade quick start](https://snosme.github.io/awakened-poe-trade/quick-start)
- A Windows-focused third-party guide states the app "must be started with Administrator rights to
  access the PoE window" — [ExitLag: Awakened PoE Trade](https://www.exitlag.com/blog/Awakened-PoE-Trade/)
  (secondary source; consistent with the UIPI probe in the code)
- The acknowledgements credit **libuiohook**, the cross-platform input hook library behind
  `uiohook-napi`, i.e. global hotkeys come from a low-level input hook rather than `RegisterHotKey` —
  [awakened-poe-trade](https://github.com/SnosMe/awakened-poe-trade)

**Microsoft's own statements on layered/transparent/topmost windows (primary source).**

- "Using a layered window can significantly improve performance and visual effects for a window that
  has a complex shape, animates its shape, or wishes to use alpha blending effects. The system
  automatically composes and repaints layered windows... layered windows are rendered smoothly,
  without the flickering typical of complex window regions. In addition, layered windows can be
  partially translucent, that is, alpha-blended." —
  [Microsoft Learn, Window Features](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features)
- A `WS_EX_LAYERED` window "will not become visible until the `SetLayeredWindowAttributes` or
  `UpdateLayeredWindow` function has been called for this window." Beginning with Windows 8,
  `WS_EX_LAYERED` can be used with child windows as well as top-level windows. `UpdateLayeredWindow`
  is for per-pixel alpha and faster animation and "uses memory more efficiently"; after
  `SetLayeredWindowAttributes` has been called, subsequent `UpdateLayeredWindow` calls fail until the
  layering style bit is cleared and set again —
  [Microsoft Learn, Window Features](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features)
- The authoritative click-through rule: "Hit testing of a layered window is based on the shape and
  transparency of the window. This means that the areas of the window that are color-keyed or whose
  alpha value is zero will let the mouse messages through. However, if the layered window has the
  `WS_EX_TRANSPARENT` extended window style, the shape of the layered window will be ignored and the
  mouse events will be passed to other windows underneath the layered window." —
  [Microsoft Learn, Window Features](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features)
  (So **per-pixel click-through for free** comes from alpha=0 regions without `WS_EX_TRANSPARENT`;
  `WS_EX_TRANSPARENT` is the all-or-nothing hammer. This is the mechanism for a mixed overlay that is
  click-through in empty areas but interactive on its panels, and it means toggling
  `WS_EX_TRANSPARENT` on hotkey is not the only option.)
- Topmost: "A topmost window overlaps all other non-topmost windows, regardless of whether it is the
  active or foreground window. A topmost window has the `WS_EX_TOPMOST` style. All topmost windows
  appear in the z-order before any non-topmost windows." —
  [Microsoft Learn, Window Features](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features)
- WinUI-specific warning: the layered style can bypass the XAML composition path, so each Windows
  build should be tested, and a separate overlay window is recommended over layering the main app
  shell — [Microsoft UI Reactor, windowing advanced](https://microsoft.github.io/microsoft-ui-reactor/main/windowing-advanced/)

**Exclusive fullscreen is the hard boundary.**

- OBS community guidance is blunt: such overlays "still won't allow you to draw things on top of
  exclusive fullscreen applications" — the game must be windowed or borderless —
  [OBS forum, Make any window into an overlay](https://obsproject.com/forum/resources/make-any-window-into-an-overlay.968/)
- A gamedev.net thread makes the same point about layered overlays not reaching exclusive DirectX
  applications — [gamedev.net discussion](https://gamedev.net/forums/topic/299403-how-do-i-overlay-text-and-images-over-all-programsdesktop/2900998/)
- Microsoft Q&A on HDR: in fullscreen "the game takes control of the display", so HDR must be enabled
  in-game; borderless fullscreen is suggested as the fix —
  [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/a/7695027)
- Unity's issue tracker records "HDR output fails to render in Player when using Direct3D11 in
  Exclusive Fullscreen mode" and marked it **Won't Fix** —
  [Unity Issue Tracker](https://issuetracker.unity.com/issues/6457/hdr-output-fails-to-render-in-player-when-using-direct3d11-in-exclusive-fullscreen-mode)
- Numerous player reports describe HDR content appearing washed out in exclusive fullscreen but
  correct in windowed/borderless, and whites shifting when an overlay appears (examples: Death
  Stranding, No Rest for the Wicked, Ghost of Tsushima; one user reports the third-party Borderless
  Gaming tool fixing it for RDR2) —
  [Death Stranding thread](https://steamcommunity.com/app/1850570/discussions/0/3265680184983364483);
  [No Rest for the Wicked thread](https://steamcommunity.com/app/1371980/discussions/0/4369129947733044509);
  [Ghost of Tsushima thread](https://steamcommunity.com/app/2215430/discussions/0/7093810588818749069/?ctp=3)

**Hooking, the approach to avoid.**

- Hook-based overlays are the route into exclusive fullscreen: DirectX 11/12 hook projects exist and
  ReShade hooks DirectX/OpenGL to draw overlays —
  [DirectXHook](https://github.com/losttech/DirectXHook)
- One hook-based overlay project describes using `WS_EX_LAYERED | WS_EX_TRANSPARENT` for its
  always-on-top window with "All mouse and keyboard input passes through to the game" —
  [procmod-overlay](https://docs.rs/crate/procmod-overlay/1.0.0)
- Side effect worth knowing: a forum report says Discord does not detect layered windows as games,
  which matters if the tool wants to be recognised by other overlay/game-detection software —
  [AutoIt forum](https://autoitscript.com/forum/topic/211992-avoid-click-through-in-100-transparent-ws_ex_layered-window)

### Inferences

- The detectability-relevant choice is binary and clear: **out-of-process window tracking +
  clipboard parsing (what Awakened PoE Trade does) touches no game process**, while
  present-hooking/DLL injection and memory reading do. `WINEVENT_OUTOFCONTEXT` is explicitly the
  no-injection variant. A tool that reads the clipboard, watches the game's log file, and positions
  its own top-level window is, mechanically, indistinguishable from a chat app that happens to stay
  on top. That is the design to copy, and it is the one the compliance research will find easiest to
  defend.
- Requiring borderless/windowed is not a compromise, it is the standing convention; every
  layered-window overlay has this constraint and PoE players already run borderless for exactly this
  reason. The tool should detect exclusive fullscreen and tell the user, rather than fail silently.
- HDR is an unresolved risk area rather than a solved one: all the evidence I found is anecdotal and
  game-specific, and none of it measures what a layered overlay looks like on an HDR desktop. If the
  overlay draws in sRGB over an HDR scanout, expect brightness mismatch. This needs an empirical test
  on the author's own machine, not more searching.
- The existing tkinter overlay is doing the same category of thing as `electron-overlay-window` but
  without the window-tracking machinery (WinEvent hooks, MSAA focus verification, the 83 ms polling
  fallback, the UIPI probe, DPI handling). Those are the parts that make an overlay feel correct, and
  they are ~500 lines of C that already exist under MIT. Rewriting them in Python via `ctypes` is
  possible; reusing them by adopting the Electron/Node ecosystem, or porting them to Rust for Tauri,
  are the other two options.
- `RegisterHotKey` vs libuiohook is a real decision: `RegisterHotKey` is cooperative and can be
  refused if another app owns the combination, and it does not see keys while a higher-integrity
  process has focus; a low-level input hook sees more but looks more like input interception. Given
  the compliance framing, `RegisterHotKey` is the more conservative choice and is probably worth
  keeping.

### Gaps

- `electron-overlay-window`'s README states Windows 7-10; it does not claim Windows 11 support, and I
  found no statement about Windows 11 or about per-monitor DPI awareness (v2) anywhere in the source
  excerpt or README I read. **Per-monitor DPI handling is genuinely unanswered by this research** —
  the native code uses `GetClientRect`/`ClientToScreen` (physical pixels), so the DPI mapping must
  happen in Electron/Chromium, but I did not verify this. This is the single largest technical gap.
- No source found on multi-monitor behaviour of these overlays (which monitor the overlay follows
  when the game moves, mixed-DPI setups).
- Found no primary documentation on how anti-cheat systems classify layered topmost windows; the
  brief says this is being researched separately, so I stopped here.
- Could not find a 2026 statement on whether `SetWindowDisplayAffinity` (used to hide windows from
  capture) is relevant or used by any of these tools.

---

## Q4. Distribution to non-technical Windows users without an 800 MB installer

### Takeaway

The author's `.bat`-builds-a-venv decision is **understandable but no longer the best available
option in 2026**; it is, however, closer to the modern answer than PyInstaller is. The modern answer
is the same shape — bootstrap a real Python environment on first run — but executed by a signed
native launcher (PyApp-style, uv-backed) or a proper installer (MSI/MSIX), not a batch file.
Separately and independently, the 800 MB problem is mostly a *dependency* problem, not a *packaging*
problem: three boosting libraries plus scikit-learn is the bulk, and replacing them with ONNX Runtime
at inference time collapses it (see Q5).

### Cited Findings

- The uv-bootstrap-launcher category is real and active, but **none of these are Astral products**:
  `ux-py` ("uv-based Python App Launcher — distribute Python apps as single executables", releases
  Jan-Feb 2026); `axe` (ships precompiled runtime stubs, `axe build` glues the app onto them, payload
  includes "a pinned CPython and a pinned uv" so users need "no Python, no uv, and no network");
  `PyCrucible` (extracts the package and uses uv to run the app in an ephemeral environment);
  `pyappdist` (installs the app into a real dedicated Python runtime "exactly the way pip would", and
  **v0.10.0 can produce an MSI installer for Windows**) —
  [ux-py](https://pypi.org/project/ux-py/); [axe](https://pypi.org/project/axe/);
  [PyCrucible](https://pypi.org/project/pycrucible/0.3.6/); [pyappdist](https://pypi.org/project/pyappdist/0.10.0/)
- `axe` published example build sizes: **52 MB for Linux amd64 with no dependency wheels** —
  [axe](https://pypi.org/project/axe/) (i.e. the embedded-CPython floor is tens of MB, before any of
  this project's dependencies)
- PyApp: a third-party handbook describes it as producing "a small native launcher, a few megabytes
  per platform", which "can also embed the Python distribution and the project into the launcher
  itself, which eliminates the first-run fetch at the cost of a much larger binary" —
  [Python Developer Tooling Handbook](https://pydevtools.com/handbook/explanation/how-do-i-ship-a-python-application-to-end-users/)
- PyApp's own runtime docs: "applications bootstrap themselves on the first run"; afterwards
  invocations only check whether the installation directory exists. By default the launcher fetches a
  `python-build-standalone` interpreter and installs the project with uv or pip into a user-local
  cache. The first launch takes seconds to download the interpreter and resolve dependencies —
  [PyApp runtime docs](https://docs.rs/crate/pyapp/0.29.0/source/docs/runtime.md)
- `py-app-standalone` uses uv to build "a standalone, relocatable Python installation with the given
  packages installed" — [py-app-standalone](https://pypi.org/project/py-app-standalone/)
- Handbook guidance on frozen bundlers: for a desktop-only app, "PyInstaller, Nuitka, or cx_Freeze is
  usually a better fit than Briefcase", and the bundle "has to include an interpreter and be
  code-signed to avoid antivirus warnings" —
  [Python Developer Tooling Handbook](https://pydevtools.com/handbook/explanation/how-do-i-ship-a-python-application-to-end-users/)
- Code signing suppresses the Windows SmartScreen "Unknown Publisher" warning —
  [Python Developer Tooling Handbook](https://pydevtools.com/handbook/explanation/how-do-i-ship-a-python-application-to-end-users/)
- PyInstaller vs Nuitka, the only *measured* comparison I found (single author, 20-line script,
  Linux, 2026-09-10): startup PyInstaller onefile ~150 ms, PyInstaller onedir ~36 ms, Nuitka onefile
  ~50 ms; size Nuitka onefile ~11 MiB vs PyInstaller ~36 MiB; build time 8 s (PyInstaller) vs 40 s
  (Nuitka) — [pistack.xyz packaging comparison](https://www.pistack.xyz/posts/2026-09-10-python-executable-packaging-pyinstaller-nuitka-cxfreeze-comparison/)
- Blog-level claims (treat as anecdote): Nuitka starts faster chiefly because PyInstaller onefile
  unpacks to a temp directory on every launch; one deep-learning project went from a 3 GB PyInstaller
  build to 7 MB with Nuitka; Nuitka builds took 5-15 minutes on a mid-sized project; one author
  reports Nuitka avoiding antivirus false positives where PyInstaller triggered them —
  [Nuitka vs PyInstaller 2026](https://beatsyncpro.ai/blog/nuitka-vs-pyinstaller);
  [Why Nuitka Beats PyInstaller](https://besthub.dev/articles/why-nuitka-beats-pyinstaller-tiny-7-mb-exes-vs-3-gb-bloat-492ca4dd4a94);
  [From PyInstaller to Nuitka](https://dev.to/weisshufer/from-pyinstaller-to-nuitka-convert-python-to-exe-without-false-positives-19jf)
- The size target to beat, again: a comparable, successful PoE overlay ships a **103.7 MB** Windows
  installer — [GitHub Releases API, awakened-poe-trade v3.30.102](https://api.github.com/repos/SnosMe/awakened-poe-trade/releases/latest)

### Inferences

- **The `.bat` decision was right about the mechanism and wrong about the wrapper.** Bootstrapping a
  real environment on first run (rather than freezing an 800 MB bundle) is exactly what PyApp, axe,
  PyCrucible and pyappdist all do. What the `.bat` lacks is: a signed executable (so no SmartScreen
  "Unknown Publisher" wall, and no antivirus heuristics on a script that downloads and runs code), a
  real uninstaller and Start-menu entry, pinned/locked dependency resolution, and a non-terrifying
  first-run experience. Swapping the `.bat` for a signed uv-backed launcher or an MSI from
  `pyappdist` keeps every advantage and removes every drawback — this is a small, cheap, high-value
  change that is independent of any UI decision.
- The 800 MB PyInstaller figure almost certainly comes from scikit-learn + CatBoost + LightGBM +
  XGBoost + their shared numpy/scipy/OpenMP baggage, not from Python or PyInstaller overhead. Any
  packaging strategy applied to the same dependency set will be large. **Fix the dependencies first,
  then re-evaluate packaging** — the ordering matters, and the author's "PyInstaller is broken for
  us" conclusion is really "our dependency set is too heavy to freeze".
- MSIX/WinGet: I found no usable 2026 material on MSIX for Python apps in this research pass. WinGet
  distribution of a signed MSI or exe is the pragmatic middle ground for discoverability without
  Store packaging friction, but I have no sources to support specifics.
- Realistic installed-size bands, based only on the figures above and flagged as composed rather than
  measured: a uv/PyApp launcher is single-digit MB on disk plus whatever it downloads; an embedded
  CPython floor is ~50 MB (axe's own example); the current ML stack is the ~700 MB variable; an
  ONNX-only inference stack puts the Python side in the tens of MB (see Q5). A Tauri shell adds
  single-digit to low-tens of MB; an Electron shell adds ~100 MB.

### Gaps

- **No measured size for this project's actual dependency set under any packager.** The 800 MB figure
  is the author's own; I found no published PyInstaller size for a scikit-learn + CatBoost +
  LightGBM + XGBoost bundle. The author should measure, because this number drives the whole decision.
- Found nothing citable on MSIX packaging of Python applications in 2026, or on WinGet manifest
  requirements for unsigned/self-signed indie tools.
- No current figure for the Python embeddable distribution's size or for whether it is still
  recommended; it did not appear in any 2026 source I reached.
- Code-signing cost/process for an individual developer in 2026 (EV certificate requirements,
  Azure Trusted Signing availability) — not researched, and it materially affects whether the
  "signed launcher" recommendation is actionable.

---

## Q5. ONNX export — how small, and what is lost

### Takeaway

Yes, and this is the highest-leverage single change available: all three boosting libraries plus
scikit-learn can be replaced at inference time by a **~13 MB `onnxruntime` wheel**, versus the
hundreds of MB the training stack occupies. The catch is specific and important: **CatBoost's ONNX
export supports numerical features only** — no categorical, no text, no embedding features — so if
the price model uses CatBoost's categorical handling (map name, tier, league as native categoricals,
which is CatBoost's main reason for existing) the export will not round-trip without re-engineering
the feature pipeline.

### Cited Findings

- `onnxruntime` CPU wheel, Windows x86-64, v1.26.0 for CPython 3.14: **~13.3 MB**; Windows ARM64 for
  CPython 3.13: ~12.8 MB. For contrast, `onnxruntime-gpu` v1.24.4 Windows x86-64 wheels run
  **~207-210 MB** (e.g. cp314 win_amd64 at 209.6 MB), and the Microsoft-distributed
  `onnxruntime-windowsml` wheels are ~25-26 MB —
  [onnxruntime on PyPI](https://pypi.org/project/onnxruntime);
  [onnxruntime-gpu 1.24.4](https://pypi.org/project/onnxruntime-gpu/1.24.4/);
  [onnxruntime-windowsml](https://pypi.org/project/onnxruntime-windowsml/)
- `onnxmltools` (latest listed 1.12.0) converts from TensorFlow, scikit-learn, Core ML, **XGBoost,
  LightGBM, CatBoost**, H2O and Spark ML; output is intended for an ONNX backend such as
  `onnxruntime` — [onnxmltools on PyPI](https://pypi.org/project/onnxmltools);
  [Microsoft Learn, ONNXMLTools](https://learn.microsoft.com/windows/ai/windows-ml/onnxmltools)
- IBM's deployment docs route LightGBM through `convert_lightgbm` and XGBoost through
  `convert_xgboost`, but for CatBoost they bypass onnxmltools and use CatBoost's own
  `save_model(..., format="onnx")`, i.e. CatBoost has a native export path —
  [IBM, Deploying models converted to ONNX](https://www.ibm.com/docs/en/DSXDOC/wsj/analyze-data/deploy-onnx-overview.html)
- CatBoost's own documentation on ONNX export (primary source, and the critical constraint): models
  can only be exported to the **ONNX-ML** variant because they are tree ensembles. Both task types
  are covered — classification outputs `label` and `probabilities`, **regression outputs
  `predictions`**. Limitations stated verbatim/near-verbatim: "Only numerical features are
  supported" (categorical support is an open issue; text and embedding features "will likely never be
  supported"); exported ONNX-ML models cannot currently be loaded back by CatBoost libraries or the
  executable and are intended only for external ML libraries; the native `cbm` format is usually
  faster on common x86-64 platforms; and the `label` output is inferred incorrectly for binary
  classification due to an onnxruntime bug, so it should be ignored in that case —
  [CatBoost: Apply ONNX-ML model](https://catboost.ai/docs/en/concepts/apply-onnx-ml)
- `onnxmltools` converts each operator individually, finding the opset version in which it was most
  recently updated, so the resulting opset may not match the requested target and should be checked —
  [onnxmltools docs](https://github.com/xadupre/onnxmltools)
- Source inconsistency worth noting: IBM examples take the data-type helper from `skl2onnx` in one
  place and from `onnxconverter_common` in another; the converter's own install notes say it is
  "tested with Python 3.7+", an old baseline —
  [IBM, Deploying models converted to ONNX](https://www.ibm.com/docs/SSYOK8/wsj/analyze-data/deploy-onnx-overview.html);
  [onnxmltools on PyPI](https://pypi.org/project/onnxmltools)

### Inferences

- The shipped inference stack could plausibly be `onnxruntime` (~13 MB) + `numpy` + `sqlite3` +
  whatever web framework, i.e. **tens of MB total instead of hundreds**. Training stays a developer
  concern, run offline on the author's machine with the full stack installed; only `.onnx` artefacts
  and the runtime ship. This single change is what makes a small installer possible under *any* of the
  architectures in Q2, and it should be decided before the UI question.
- Regression is supported by CatBoost's exporter, so a *price regression* model is in scope — the
  blocker is feature types, not task type.
- The CatBoost numerical-only restriction is the decision point. If categorical features are
  currently handed to CatBoost raw, the options are: pre-encode them in the pipeline (target/ordinal
  encoding done in the author's own code, which is pure-Python and already testable), drop CatBoost
  from the ensemble, or keep CatBoost's native `cbm` format and the `catboost` package for just that
  one model. Note the second-order effect: moving encoding into the author's own code *increases*
  the pure-logic surface, which this project's test suite already covers well.
- Mandatory validation step regardless of route: compare ONNX Runtime outputs against the original
  models on a held-out set, and assert equivalence in the test suite. Converter/opset drift is a
  documented hazard, and this project has the test infrastructure to make it a non-issue.
- Having all models behind one ONNX Runtime interface also makes the models callable from Rust, C#
  or Node — which means an ONNX migration *decouples the model from Python entirely* and quietly
  unlocks the "rewrite the shell in Tauri/native and keep nothing Python" option as a later choice
  rather than a now choice.

### Gaps

- No published reliability data (issue rates, round-trip accuracy studies) for onnxmltools'
  LightGBM/XGBoost converters. The search surfaced support matrices and usage docs, not test results.
- No measured inference-latency comparison between native LightGBM/XGBoost/CatBoost predict and ONNX
  Runtime for small tabular models. CatBoost states `cbm` is "usually faster" on x86-64 but gives no
  figures.
- Did not verify whether scikit-learn preprocessing pipelines in this project would convert cleanly
  via `skl2onnx`, which matters as much as the boosters.

---

## Q6. What comparable tools actually use

### Takeaway

The PoE companion-tool ecosystem has converged almost completely on **Electron + a JS framework +
native Windows window-tracking via a small C/N-API addon**, with clipboard/log-file parsing rather
than memory reading. Awakened PoE Trade is the live standard and is actively maintained on current
Electron (v40) as of five days before this research. Path of Building is the outlier — a custom
LuaJIT host (SimpleGraphic) — and that decision has aged surprisingly well for *testability* while
ageing badly for contributor onboarding. The clearest "aged badly" datapoint is PoE Overlay, which
went to Overwolf and then had a developer move back to standalone.

### Cited Findings

**Awakened PoE Trade (the current standard) — MIT, Windows + Linux.**
- Repo topics include `electron`, `vue`, `path-of-exile`, `pathofexile`; language TypeScript; MIT
  licensed; also tagged `no-ai`. Top-level folders are `main`, `renderer` and `ipc`. Acknowledges
  libuiohook, RePoE, poeprices.info and poe.ninja — [awakened-poe-trade](https://github.com/SnosMe/awakened-poe-trade)
- Current stack (primary source, package manifests): main process on `electron ^40.10.6`,
  `electron-builder ^26.9.0`, `electron-updater ^6.8.3`, `typescript ^6.0.2`, `esbuild ^0.28.0`, with
  only `electron-overlay-window 4.1.0` and `uiohook-napi 1.5.5` as runtime deps —
  [main/package.json](https://raw.githubusercontent.com/SnosMe/awakened-poe-trade/master/main/package.json)
- Renderer stack (primary source): `vue 3.2.37`, `vue-i18n ^10.0.0`, `tailwindcss 3.x.x`,
  `apexcharts ^4.0.0` + `vue3-apexcharts`, `@vueuse/core ^11.0.0`, `tippy.js ^6.2.7`,
  `vuedraggable 4.1.0`, `neverthrow ^8.0.0`, `fastest-levenshtein ^1.0.16`, `sockette ^2.0.6`,
  `dot-prop 9.x.x`, `luxon 3.x.x`, `object-hash ^3.0.0`, `fast-deep-equal ^3.1.3`, `animate.css`,
  `@fortawesome/fontawesome-free 6.x.x`, `@sindresorhus/fnv1a ^3.0.0` —
  [renderer/package.json](https://raw.githubusercontent.com/SnosMe/awakened-poe-trade/master/renderer/package.json)
- Release cadence and size: v3.30.102 published 2026-11-19; Windows installer 103.7 MB —
  [GitHub Releases API](https://api.github.com/repos/SnosMe/awakened-poe-trade/releases/latest)
- Data acquisition is clipboard text parsing, not memory reading —
  [quick start](https://snosme.github.io/awakened-poe-trade/quick-start)
- For PoE 2, the community points to **Exiled Exchange 2** as the unofficial successor —
  [u4gm guide](https://www.u4gm.com/poe/blog-mastering-awakened-poe-trade-in-path-of-exile-a-2026-guide-for-smarter-trading)
  (secondary source)

**PoE Overlay — the Overwolf round-trip, the clearest "aged badly" signal.**
- The original PoE Overlay was built with **Electron and Angular**. A community fork notes its
  "snapshot was taken before it was converted to utilizing Overwolf" —
  [PoE-Overlay-Community-Fork](https://github.com/wratho/PoE-Overlay-Community-Fork)
- Overwolf gives "built-in overlay support, hotkeys, and automatic updates" and the platform claims
  support for 1,500 games, but it installs the Overwolf Client alongside the app —
  [Overwolf app page](https://www.overwolf.com/app/Kyusung4698-PoE_Overlay);
  [Overwolf creators](https://overwolf.com/creators/build-an-app/)
- Overwolf's own Electron fork exposes an overlay API (`IOverwolfOverlayApi`) —
  [ow-electron overlay API](https://dev.overwolf.com/ow-electron/reference/Overwolf-electron-APIs/overlay/interfaces/IOverwolfOverlayApi/)
- A developer who moved from Overwolf to standalone stated the goal was to be "lighter, faster, and
  more flexible" (developer's own claim, no benchmark) —
  [th.gl: Why I built a companion app](https://www.th.gl/blog/why-i-built-companion-app)
- PoE Overlay is Windows-only, must run on the same machine as the game (so cloud gaming is out), and
  maintains separate release lines for PoE1 and PoE2. Its own site says it follows GGG's third-party
  tool policy while noting GGG does not endorse third-party tools. Note: the download page and the
  changelog I saw disagree on the current PoE1 version (2.27.0 vs 2.43.0), so treat version claims
  from that site cautiously — [poeoverlay.com FAQ](https://www.poeoverlay.com/faq);
  [poeoverlay.com changelog](https://www.poeoverlay.com/changelog/poe-overlay)

**Path of Building Community — the outlier.**
- PoB runs inside **SimpleGraphic**, a custom Lua host providing a small API, and targets **LuaJIT**
  (it accesses the `jit` library, so non-LuaJIT versions may not work). `src/_SimpleGraphic.def.lua`
  is a non-executed IDE hint file with basic implementations that let parts of PoB run outside
  SimpleGraphic. There is no build step for PoB itself: clone the `dev` branch and run
  `./runtime/Path of Building.exe`; running from the repo auto-enables Dev Mode and disables
  auto-updates. Headless testing goes through **`HeadlessWrapper.lua`** under LuaJIT directly. Some
  SimpleGraphic API parts (e.g. subscripts) are unimplemented, so some features are unusable outside
  the full environment — [PathOfBuilding CONTRIBUTING.md](https://github.com/PathOfBuildingCommunity/PathOfBuilding/blob/dev/CONTRIBUTING.md)
- Repo artefacts confirm the shape: a `runtime` folder and `runtime-win32.zip`, a `.busted` test
  config with `spec`/`tests` directories, `update_manifest.py` + `manifest.xml`/`manifest.cfg` driving
  a manifest-based updater, distribution as "the install wizard or portable zip", and the claim that
  updates take "a couple of seconds to apply". 9,553 commits, 817 open issues, 201 PRs, MIT —
  [PathOfBuilding](https://github.com/PathOfBuildingCommunity/PathOfBuilding)
- No statement of any plan to change this architecture appears in either source —
  [PathOfBuilding CONTRIBUTING.md](https://github.com/PathOfBuildingCommunity/PathOfBuilding/blob/dev/CONTRIBUTING.md)

**Adjacent precedent on abandoning a desktop shell.**
- Excalidraw publicly deprecated its Electron wrapper in favour of the web — a documented case of a
  team concluding the desktop shell was not worth maintaining —
  [web.dev case study](https://web.dev/case-studies/deprecating-excalidraw-electron.md.txt)

### Inferences

- **What has aged well:** (1) parsing the clipboard / log files instead of reading game memory — this
  is why these tools have survived a decade of client updates and anti-cheat changes, and it is the
  single most transferable decision; (2) isolating all native Windows work into a tiny addon with a
  narrow API (`electron-overlay-window` is two functions' worth of surface), so the UI framework can
  change without touching the overlay; (3) PoB's `HeadlessWrapper.lua` — a headless entry point that
  runs the whole calculation engine without the GUI, which is precisely the pattern this project's
  ~1030 tests already embody and should keep; (4) manifest-based delta updates (PoB) and
  `electron-updater` (APT) — auto-update is non-negotiable for a game tool that breaks every league.
- **What has aged badly:** (1) binding to a platform's overlay runtime (Overwolf) — it added a
  mandatory client install and the one developer statement I found about moving away cites weight and
  inflexibility; (2) Angular for a small overlay UI — the Angular-based PoE Overlay is the fork-ridden,
  version-confused one while the Vue-based Awakened PoE Trade is the live standard, though I cannot
  show the framework *caused* that; (3) a bespoke UI host (SimpleGraphic) — it still works, but
  "some SimpleGraphic API parts are not implemented" and the IDE-hint-file workaround are exactly the
  friction costs of owning your own runtime, and 817 open issues suggests contributor throughput is a
  constraint.
- The ecosystem's revealed preference is unambiguous: **Electron is the safe answer for a PoE overlay
  in 2026**, and the 103.7 MB installer has evidently not stopped adoption. Any recommendation to use
  Tauri instead must argue that the author can port the window-tracking native layer, because no
  equivalent off-the-shelf Rust crate surfaced in this research.
- Nobody in this ecosystem runs a browser-based UI as the primary surface. The overlay *is* the
  product; a separate web console is at best secondary. That is an argument for inverting the current
  project's architecture: the overlay should be the main app, and the Streamlit-style console an
  adjunct.

### Gaps

- **Exilence Next: I found no reliable sources at all.** Two searches returned nothing about the
  project — no repo, no releases, no discontinuation or rewrite announcement. I cannot confirm or
  deny the Electron/Angular attribution or its status. The report should say this is unverified
  rather than repeat it.
- No architecture decision records or rewrite post-mortems found for any PoE tool. The "aged badly"
  judgements above are inferred from repo state and one developer blog post, not from stated reasons.
- Exiled Exchange 2's stack is unverified (only a secondary guide mentions it).
- No installer size or stack details obtained for standalone PoE Overlay or PoE Planner.

---

## Q7. Testability and migration strategy

### Takeaway

A staged migration is clearly better than a rewrite, and the evidence points to a specific staging
order that happens to front-load all the cheap wins: **(1) upgrade Streamlit and retest the two
named frictions; (2) export models to ONNX and drop the training stack from the shipped app; (3)
extract the pure logic behind a FastAPI/Litestar localhost service with the existing tests pointed at
it; (4) replace the tkinter overlay with a proper tracked layered window; (5) only then decide
whether the console UI stays Streamlit or becomes a Tauri/Electron frontend.** Steps 1-3 preserve
essentially all ~1030 tests; steps 4-5 are where code gets discarded, and they are the smallest part
of the codebase.

### Cited Findings

- The "split into frontend and processing backend" pattern is the Streamlit community's own standard
  remedy for exactly this class of problem — [Streamlit forum](https://discuss.streamlit.io/t/run-long-running-tasks-like-embedding-of-large-docs-asynchronously-without-blocking-ui-and-notify-user-when-finished/51036)
- Tauri's sidecar model is explicitly designed for keeping an existing backend binary: declare under
  `externalBin`, allow it in the shell plugin, start on launch and stop on exit —
  [Tauri v2 sidecar docs](https://v2.tauri.app/ja/develop/sidecar/)
- Practitioners pair it with PyInstaller for the Python half and a localhost HTTP contract, sometimes
  with a per-launch auth token — [tauri-fastapi template](https://awesome.ecosyste.ms/projects/github.com%2Ffudanglp%2Ftauri-fastapi-full-stack-template);
  [Bundling a CLI binary as a Tauri v2 sidecar](https://dev.to/chenxxpro/bundling-a-cli-binary-as-a-tauri-v2-sidecar-architecture-and-lessons-from-building-moonproxy-21n3)
- Precedent for a headless entry point preserving a large test suite across a GUI boundary: PoB
  tests its engine via `HeadlessWrapper.lua` run under LuaJIT, entirely outside SimpleGraphic, with
  `busted` as the test runner — [PathOfBuilding CONTRIBUTING.md](https://github.com/PathOfBuildingCommunity/PathOfBuilding/blob/dev/CONTRIBUTING.md)
- Precedent for the overlay being a swappable, narrow native component: Awakened PoE Trade's entire
  Windows overlay dependency is one 4.1.0 npm package with a small API surface —
  [main/package.json](https://raw.githubusercontent.com/SnosMe/awakened-poe-trade/master/main/package.json)
- CatBoost's ONNX constraint ("Only numerical features are supported") is the one migration step with
  a real chance of forcing model/feature rework rather than mechanical refactoring —
  [CatBoost: Apply ONNX-ML model](https://catboost.ai/docs/en/concepts/apply-onnx-ml)

### Inferences

- A clean separation between pure logic and UI is the precondition for a staged migration, and the
  author already has it. In that situation a rewrite destroys value for no architectural gain: the
  ~1030 tests cover the part that is *hard* (pricing logic, data handling, model plumbing) and
  cheapest to keep, while the parts worth replacing (a tkinter overlay, a Streamlit page layer) are
  the thinnest and least-tested. Any rewrite proposal must explicitly price the loss of those tests;
  on this evidence there is no reason to pay it.
- Putting a FastAPI/Litestar HTTP boundary in front of the existing core converts the test suite from
  an internal-unit-test asset into an *API contract* asset: the same tests keep running in-process,
  and a thin layer of HTTP-level tests pins the boundary. That boundary is what makes the UI
  technology a reversible decision instead of a one-way door — which is the whole point of staging.
- Sequencing matters more than the endpoint choice. Steps 1 and 2 are days of work with no
  architectural commitment, and they may change the answer: if Streamlit 1.67 fixes the tab and state
  friction and ONNX cuts the install to tens of MB, the remaining complaint is only "the overlay is
  crude and the launcher is a .bat" — both fixable without touching the UI at all. Doing the UI
  decision first risks spending the whole budget on the part that may not have needed changing.
- The riskiest single item in any plan is the overlay's per-monitor DPI and multi-monitor behaviour
  (Q3 gap). It should be prototyped against the real game before any framework is committed to —
  a day spent on a throwaway spike here de-risks the largest unknown in the whole decision.
- If the author does eventually want to leave Python, the ONNX step is the enabler: once models are
  `.onnx`, the pricing core is portable to Rust/C#/Node and the Python service becomes optional
  rather than load-bearing. That makes the ONNX work valuable under *every* scenario, including
  "keep everything", which is why it belongs early.

### Gaps

- The project's own test suite structure (how much is pure-logic unit tests vs. Streamlit-coupled)
  was not inspected in this research pass; the migration-cost claim rests on the brief's statement of
  "a clear separation between pure logic and UI".
- No source found quantifying the effort of a Streamlit-to-SPA UI port, because no such migration
  report appears to be published.
- Whether `RegisterHotKey` suffices for the target UX (vs. needing a low-level input hook like
  libuiohook) depends on the specific hotkeys and on whether PoE runs elevated on the user's machine;
  not resolvable from sources.
