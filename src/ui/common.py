"""Shared UI state, styling and helpers."""
from __future__ import annotations

from typing import Any

import streamlit as st

from src.config import Config, load_config
from src.database.repository import PRICING_COLUMNS, Repository
from src.market.ingest import converter_for
from src.market.seed import seed_bundled_sample
from src.ml.affix_catalogue import CATALOGUE_NAME, AffixCatalogue
from src.ml.predict import ModelNotTrained, ModelSchemaMismatch, Predictor
from src.pricing.currency import CurrencyConverter
from src.pricing.display import PriceFormatter
from src.pricing.fair_value import PricingService, observed_multipliers
from src.pricing.sell_strategy import multipliers_from_config

# Re-exported: the palette itself lives in src/ui/palette.py so the desktop
# overlay, which runs in a tkinter process with no Streamlit in it, can share
# the same colours without importing this module.
from src.ui.palette import VALUE_COLOURS, confidence_fill  # noqa: F401

#: The colour for a chart that carries one series and therefore no bands.
#:
#: Deliberately none of the band colours. A histogram of every listing is not a
#: percentile band, and painting it in the "high" amber or the "exceptional"
#: magenta would say it was one. It is a lighter, bluer grey than the "low"
#: slate for the same reason. Streamlit's default chart blue was worse still: it
#: is the one hue on the page that means nothing at all, and it sat next to
#: several that mean something specific.
CHART_SERIES = "#6d8fb0"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Oswald:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

/* ==========================================================================
   The one rule this design follows: COLOUR IS DATA.
   Every hue on screen belongs to a value band, a confidence level or an exalt
   verdict. Everything structural -- rules, labels, nav, borders, buttons -- is
   slate and brass. Streamlit ships a bright red primary, and against four
   percentile colours that already mean something it read as a fifth signal;
   demoting the accent to brass is what lets the value bands be seen at all.
   ========================================================================== */
:root {
  --ground:      #0d1014;   /* the panel behind everything */
  --panel:       #151a21;   /* raised surface */
  --panel-lift:  #1b2129;   /* inputs, table headers, hovered rows */
  --rule:        #262d38;   /* hairlines */
  --rule-soft:   #1e242d;
  --brass:       #b3894e;   /* structure accent, never data */
  --brass-dim:   rgba(179,137,78,.32);
  --ink:         #e7eaf0;
  --ink-dim:     #8d97a8;
  --ink-faint:   #656e7d;
  --cyan:        #39d0d8;   /* the exalt ring, shared with the overlay */
  --radius:      8px;

  --font-ui:      'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-display: 'Oswald', 'Inter', system-ui, sans-serif;
  --font-mono:    'IBM Plex Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace;
}

html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  background: var(--ground);
  font-family: var(--font-ui);
  color: var(--ink);
}
[data-testid="stMainBlockContainer"] { padding-top: 2.6rem; max-width: 1560px; }
[data-testid="stHeader"] { background: transparent; }

/* Every figure on the page is set in the mono face with tabular figures, so a
   column of prices lines up on the decimal and a four-digit total cannot be
   mistaken for a three-digit one at a glance. */
.mono, .stat-rail .v, .metric-card .value, [data-testid="stMetricValue"] {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1;
}

/* -- headings ------------------------------------------------------------- */
h1, h2, h3, h4, [data-testid="stHeading"] h1, [data-testid="stHeading"] h2,
[data-testid="stHeading"] h3 {
  font-family: var(--font-display);
  font-weight: 500;
  letter-spacing: .012em;
  color: var(--ink);
}
h1, [data-testid="stHeading"] h1 { font-size: 2.1rem; line-height: 1.15; }
h2, [data-testid="stHeading"] h2 { font-size: 1.28rem; }
h3, [data-testid="stHeading"] h3 { font-size: 1.06rem; }

/* The page header: an eyebrow saying which of the app's jobs this is, the
   title, and a brass hairline that closes it. Repeated identically on every
   page so that a glance at the top-left always answers "where am I". */
.page-head { margin: 0 0 1.15rem; }
.page-head .eyebrow {
  font-family: var(--font-display); font-size: .72rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .22em; color: var(--brass);
  margin-bottom: .28rem;
}
.page-head h1 {
  font-size: 2.05rem; margin: 0 0 .3rem; font-family: var(--font-display);
  font-weight: 500; letter-spacing: .01em;
}
.page-head .lede { color: var(--ink-dim); font-size: .9rem; max-width: 72ch; margin: 0; }
.page-head .rule {
  height: 1px; margin-top: .95rem;
  background: linear-gradient(90deg, var(--brass-dim) 0%, var(--rule) 38%, transparent 100%);
}

/* -- the stat rail -------------------------------------------------------- */
/* The signature component, and the one that does the most de-cluttering. It
   replaces every pipe-separated grey caption in the app ("26 maps found | 25
   priceable | 1 unsupported | ...") with a readout: labels small and quiet,
   figures large and mono, brass hairlines between them. */
.stat-rail {
  display: flex; flex-wrap: wrap; align-items: stretch;
  border: 1px solid var(--rule); border-radius: var(--radius);
  background: var(--panel); margin: 0 0 1.1rem; overflow: hidden;
}
.stat-rail .stat {
  padding: .62rem 1.05rem; min-width: 8.5rem; flex: 0 1 auto;
  border-left: 1px solid var(--rule-soft);
}
.stat-rail .stat:first-child { border-left: 0; box-shadow: inset 2px 0 0 var(--brass); }
.stat-rail .k {
  display: block; font-size: .66rem; text-transform: uppercase;
  letter-spacing: .13em; color: var(--ink-faint); margin-bottom: .18rem;
  font-weight: 500;
}
.stat-rail .v { font-size: 1.02rem; font-weight: 500; color: var(--ink); line-height: 1.2; }
.stat-rail .v.dim { color: var(--ink-dim); }
.stat-rail .v em { font-style: normal; color: var(--ink-faint); font-size: .82em; }

/* -- panels --------------------------------------------------------------- */
.panel {
  border: 1px solid var(--rule); border-radius: var(--radius);
  background: var(--panel); padding: .9rem 1rem 1rem; margin-bottom: 1rem;
}
.panel-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 1rem; margin-bottom: .55rem;
}
.eyebrow {
  font-family: var(--font-display); font-size: .7rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .18em; color: var(--brass);
}
.panel-head .note { font-size: .78rem; color: var(--ink-faint); }
.hint { font-size: .82rem; color: var(--ink-dim); margin: .2rem 0 .7rem; }

/* -- metric cards --------------------------------------------------------- */
.metric-row { display: flex; gap: .6rem; flex-wrap: wrap; margin-bottom: 1.15rem; }
.metric-card {
  flex: 1 1 165px; padding: .72rem .9rem; border-radius: var(--radius);
  border: 1px solid var(--rule); background: var(--panel);
  display: flex; flex-direction: column; justify-content: flex-start;
  min-height: 5.1rem;
}
.metric-card .label {
  font-size: .66rem; text-transform: uppercase; letter-spacing: .13em;
  color: var(--ink-faint); font-weight: 500;
}
.metric-card .value {
  font-size: 1.4rem; font-weight: 500; line-height: 1.25; margin-top: .2rem;
  /* Long values -- a model id like 20260825-201820 -- shrink instead of
     wrapping onto a second line and making one card taller than its row. */
  overflow-wrap: anywhere; font-size: clamp(1rem, 1.4rem, 1.4rem);
}
.metric-card .value.long { font-size: 1.02rem; letter-spacing: -.01em; }
.metric-card .sub { font-size: .73rem; color: var(--ink-faint); margin-top: auto; padding-top: .3rem; }

/* -- sidebar: navigation, not a form ------------------------------------- */
[data-testid="stSidebar"] { background: #0a0d11; border-right: 1px solid var(--rule); }
[data-testid="stSidebarContent"] { padding-top: .4rem; }
.brandmark { padding: .35rem .2rem 1rem; }
.brandmark .name {
  font-family: var(--font-display); font-size: 1.06rem; font-weight: 600;
  letter-spacing: .13em; text-transform: uppercase; color: var(--ink);
}
.brandmark .sub {
  font-size: .68rem; letter-spacing: .16em; text-transform: uppercase;
  color: var(--brass); margin-top: .12rem;
}
.nav-group {
  font-family: var(--font-display); font-size: .64rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .2em; color: var(--ink-faint);
  margin: .9rem 0 .3rem .1rem;
}
/* The page picker is one radio, because that is exactly what it was before and
   a radio is the only Streamlit control that remembers which page you are on.
   The group headings are drawn by CSS on the first option of each group rather
   than by splitting it into three radios, which would have meant three widgets
   arguing over one selection. Keyed on position, so the order in app.py's PAGES
   and these three selectors have to move together. */
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(1),
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(5),
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(7) {
  position: relative; margin-top: 1.45rem;
}
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(1) { margin-top: .35rem; }
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(1)::before { content: "Price"; }
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(5)::before { content: "Market"; }
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(7)::before { content: "Tool"; }
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(1)::before,
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(5)::before,
[data-testid="stSidebar"] [role="radiogroup"] > *:nth-child(7)::before {
  position: absolute; top: -1.02rem; left: .62rem;
  font-family: var(--font-display); font-size: .62rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .2em; color: var(--ink-faint);
  pointer-events: none;
}
/* The page picker is an st.radio because that is what drives the app. It is
   dressed as a nav list: the dot is removed, the row becomes the target, and
   the current page is marked with a brass edge rather than a filled circle. */
[data-testid="stSidebar"] [role="radiogroup"] { gap: 1px; }
/* The dot goes, and the row carries the state instead: a brass left edge and a
   lifted background. The input itself stays in the DOM and stays focusable --
   only its rendering is dropped -- so keyboard use and screen readers are
   unaffected. */
/* The dot BaseWeb paints is the div immediately before the label text, nested
   two levels inside the option. Matched by that relationship rather than by
   depth, so a change in Streamlit's wrapper markup does not silently bring it
   back. The `span` holding the real input is hidden too -- it is 0x0 and
   invisible either way, but leaving it displayed adds a stray flex child. */
[data-testid="stSidebar"] [data-testid="stRadioOption"] > span:first-child,
[data-testid="stSidebar"] [data-testid="stRadioOption"] div:has(> [data-testid="stMarkdownContainer"]) > div:first-child {
  display: none;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {
  width: 100%; margin: 0; padding: .42rem .6rem; border-radius: 6px;
  border-left: 2px solid transparent; cursor: pointer;
  transition: background .12s ease, color .12s ease;
}
[data-testid="stSidebar"] [data-testid="stRadioOption"] div { font-size: .88rem; color: var(--ink-dim); }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover { background: rgba(255,255,255,.04); }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover div { color: var(--ink); }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:checked) {
  background: rgba(179,137,78,.10); border-left-color: var(--brass);
}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:has(input:checked) div {
  color: var(--ink); font-weight: 500;
}

/* Context: a key/value block, not five identical grey sentences. */
.ctx { border-top: 1px solid var(--rule); margin-top: 1rem; padding-top: .7rem; }
.ctx .row {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: .6rem; padding: .2rem 0; font-size: .78rem;
}
.ctx .row .k { color: var(--ink-faint); letter-spacing: .04em; }
.ctx .row .v { color: var(--ink); font-family: var(--font-mono); font-size: .76rem; text-align: right; }
.ctx .row .v.warn-text { color: #d8a657; }
.ctx .row .v.ok { color: #6fbf8f; }

/* -- horizontal radios become a segmented control ------------------------- */
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] {
  gap: 0; border: 1px solid var(--rule); border-radius: var(--radius);
  background: var(--panel); padding: 3px; width: max-content; max-width: 100%;
  flex-wrap: wrap;
}
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"] > span:first-child,
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"] div:has(> [data-testid="stMarkdownContainer"]) > div:first-child {
  display: none;
}
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"] {
  margin: 0; padding: .34rem .8rem; border-radius: 6px; cursor: pointer;
}
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"] div {
  font-size: .84rem; color: var(--ink-dim);
}
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]:hover div { color: var(--ink); }
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]:has(input:checked) {
  background: var(--panel-lift); box-shadow: inset 0 0 0 1px var(--brass-dim);
}
[data-testid="stMain"] [role="radiogroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]:has(input:checked) div {
  color: var(--ink); font-weight: 500;
}

/* -- inputs --------------------------------------------------------------- */
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stNumberInputContainer"],
[data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stTextArea"] div[data-baseweb="textarea"] {
  background: var(--panel-lift); border-color: var(--rule); border-radius: 6px;
}
[data-testid="stWidgetLabel"] p, label p {
  font-size: .72rem !important; text-transform: uppercase; letter-spacing: .1em;
  color: var(--ink-faint) !important; font-weight: 500;
}
/* Checkbox and radio option labels are content, not field names, so they keep
   sentence case and their normal size. */
[data-testid="stCheckbox"] label p, [data-testid="stRadioOption"] p {
  text-transform: none !important; letter-spacing: 0 !important;
  font-size: .84rem !important; color: var(--ink-dim) !important;
}
/* A checkbox has no label above it and a select does, so a checkbox sitting in
   a row of selects floats a label's height too high. Only the first element of
   a column is nudged: a checkbox under another checkbox is already aligned. */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]
  > div > [data-testid="stElementContainer"]:first-child [data-testid="stCheckbox"] {
  margin-top: 1.55rem;
}

/* A label for a group of controls that Streamlit gives no label of its own. */
.filter-label {
  font-size: .72rem; text-transform: uppercase; letter-spacing: .1em;
  color: var(--ink-faint); font-weight: 500; margin: 0 0 .45rem;
}
input:focus-visible, select:focus-visible, textarea:focus-visible,
[data-baseweb="select"]:focus-within {
  outline: 2px solid var(--brass) !important; outline-offset: 1px;
}

/* -- buttons -------------------------------------------------------------- */
[data-testid="stBaseButton-primary"] {
  background: var(--brass); border: 1px solid var(--brass); color: #14171c;
  font-weight: 600; letter-spacing: .02em; border-radius: 6px;
}
[data-testid="stBaseButton-primary"]:hover { background: #c79b5d; border-color: #c79b5d; color: #14171c; }
[data-testid="stBaseButton-secondary"] {
  background: transparent; border: 1px solid var(--rule); color: var(--ink);
  border-radius: 6px;
}
[data-testid="stBaseButton-secondary"]:hover { border-color: var(--brass); color: var(--brass); background: rgba(179,137,78,.07); }
button:focus-visible { outline: 2px solid var(--brass) !important; outline-offset: 2px; }

/* -- tabs: what used to be a six-panel scroll ----------------------------- */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: .15rem; border-bottom: 1px solid var(--rule); background: transparent;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
  font-family: var(--font-display); font-size: .82rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--ink-faint); padding: .5rem .95rem;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover { color: var(--ink); }
[data-testid="stTabs"] [aria-selected="true"] { color: var(--brass); }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background: var(--brass); }

/* -- expanders ------------------------------------------------------------ */
[data-testid="stExpander"] details {
  border: 1px solid var(--rule); border-radius: var(--radius); background: var(--panel);
}
[data-testid="stExpander"] summary:hover { color: var(--brass); }

/* -- dataframe ------------------------------------------------------------ */
[data-testid="stDataFrame"] { border: 1px solid var(--rule); border-radius: var(--radius); }

/* -- the stash grid ------------------------------------------------------- */
/* Sized in fixed pixels (12 columns x 46px is wider than half of the page), so
   it must scroll inside its own column. Without the wrapper it overflowed to
   the right and slid under the table beside it, which read as the table
   floating over the stash layout. */
.stash-grid-wrap { max-width: 100%; overflow-x: auto; padding-bottom: .35rem; }
.stash-grid {
  display: grid; gap: 2px; background: var(--ground);
  padding: 5px; border-radius: 6px; width: max-content;
  border: 1px solid var(--rule);
}
.stash-cell {
  width: 46px; height: 46px; border-radius: 3px;
  background: rgba(255,255,255,.035);
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: .68rem; font-weight: 500; color: #f4f4f5;
  overflow: hidden; text-align: center; line-height: 1.05;
}
.stash-cell.filled {
  color: #fff; box-shadow: inset 0 0 0 1px rgba(255,255,255,.16);
  position: relative;
}
/* Below the negligible threshold. The near-black fill alone is not enough --
   a white price on it is the brightest thing in the tab -- so the label drops
   to a dim grey and the inset hairline almost off, leaving a tile the eye can
   still find when it looks for it and skips when it does not. Not hidden
   outright: the map is there, and a hole in the grid reads as a bug. */
.stash-cell.filled.negligible {
  color: #5b626d; box-shadow: inset 0 0 0 1px rgba(255,255,255,.05);
}
/* The confidence meter. Absolutely positioned -- hence the positioning context
   on .filled above -- so it takes no part in the flex centring: the price stays
   in the middle of the cell whether or not there is a meter under it, which
   keeps a tab of mixed-confidence maps from looking like the numbers are
   jittering up and down. The track is opaque rather than a translucent white so
   that the same score looks the same length and the same weight on every
   value band. Inset 4px to clear the exalt outline. */
.conf-meter {
  position: absolute; left: 4px; right: 4px; bottom: 4px; height: 4px;
  border-radius: 2px; background: #15181d; overflow: hidden;
}
.conf-meter > span { display: block; height: 100%; border-radius: 2px; }
.legend .conf-meter.legend-meter {
  position: static; display: inline-block; width: 26px; height: 5px;
  margin-right: .35rem; vertical-align: 1px;
}
/* Exalt state. Cyan appears nowhere in VALUE_COLOURS, so the ring stays legible
   on every value background; solid vs dashed carries the same meaning again
   for anyone who cannot separate the two colours. Drawn with outline rather than
   border so tile size and grid alignment are untouched. */
.stash-cell.exalt-worth { outline: 2px solid var(--cyan); outline-offset: -2px; }
.stash-cell.exalt-skip { outline: 2px dashed rgba(220,220,225,.5); outline-offset: -2px; }
.stash-cell.unsupported {
  background: repeating-linear-gradient(45deg,
    rgba(255,255,255,.06) 0 5px, rgba(255,255,255,.02) 5px 10px);
  color: rgba(255,255,255,.5);
}
.legend {
  display: flex; gap: .9rem; flex-wrap: wrap; font-size: .74rem;
  margin: .65rem 0 .2rem; color: var(--ink-dim);
}
/* The same meter at reading size, for the detail panels, where there is room to
   spell out what the bar is and what it scored. */
.conf-row {
  display: flex; align-items: center; gap: .5rem;
  font-size: .88rem; margin: .15rem 0 .35rem; color: var(--ink-dim);
}
.conf-row .conf-bar {
  flex: 0 0 120px; height: 8px; border-radius: 4px;
  background: #15181d; overflow: hidden;
  border: 1px solid rgba(255,255,255,.14);
}
.conf-row .conf-bar > span { display: block; height: 100%; }
.conf-row .conf-value { opacity: .8; font-variant-numeric: tabular-nums; }
.legend span.swatch {
  display: inline-block; width: 11px; height: 11px; border-radius: 2px;
  margin-right: .35rem; vertical-align: -1px;
}

/* -- the readout ---------------------------------------------------------- */
/* One map's numbers, set to be read against each other. Label left, figure
   right, hairline between: fair value, the price to list at, the range it might
   really be worth and how much to trust any of it. As four bold sentences these
   were four things to read; as a column they are one thing to check. */
.readout { border-top: 1px solid var(--rule); margin: .2rem 0 .8rem; }
.readout .row {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: 1rem; padding: .42rem .1rem; border-bottom: 1px solid var(--rule-soft);
}
.readout .row .k {
  font-size: .72rem; text-transform: uppercase; letter-spacing: .1em;
  color: var(--ink-faint); font-weight: 500;
}
.readout .row .v {
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: 1rem; color: var(--ink);
}
.readout .row .v em { font-style: normal; color: var(--ink-faint); font-size: .78em; }
.reason {
  font-size: .78rem; color: var(--ink-dim); padding: .12rem 0 .12rem .85rem;
  position: relative;
}
.reason::before { content: "·"; position: absolute; left: .2rem; color: var(--brass); }

/* -- messages ------------------------------------------------------------- */
/* One shape for everything the app says about itself: a left edge carrying the
   meaning, a quiet body. Colour still only ever encodes state. */
.note-box {
  font-family: var(--font-mono); font-size: .8rem; padding: .55rem .75rem;
  border-radius: 6px; background: var(--panel-lift); border: 1px solid var(--rule);
  color: var(--ink);
}
.exalt-yes, .exalt-no, .exalt-unknown, .warn, .callout {
  padding: .6rem .85rem; border-radius: 6px; font-size: .85rem;
  border: 1px solid var(--rule); border-left-width: 3px; background: var(--panel);
  margin: .3rem 0 .7rem; color: var(--ink);
}
.exalt-yes { border-left-color: #4a9d74; }
.exalt-no  { border-left-color: var(--ink-faint); color: var(--ink-dim); }
/* A third answer, not a quieter "no": the model declined to judge this map at
   all. Brass rather than either verdict colour, so it cannot be misread as a
   recommendation in either direction. */
.exalt-unknown { border-left-color: var(--brass); color: var(--ink-dim); }
.warn      { border-left-color: var(--brass); }
.callout   { border-left-color: var(--cyan); }

/* Streamlit's own st.info / st.warning / st.error / st.success. They ship as
   filled pastel blocks -- an olive slab for a warning, a bright green one for a
   success -- which on this ground are the loudest thing on the page and mean
   less than any tile in the grid. Given the same shape as the app's own
   messages: quiet body, one coloured edge carrying the state. */
[data-testid="stAlertContainer"] {
  background: var(--panel) !important;
  border: 1px solid var(--rule) !important;
  border-left: 3px solid var(--ink-faint) !important;
  border-radius: 6px !important;
  color: var(--ink) !important;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { border-left-color: var(--brass) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) { border-left-color: #c0645f !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { border-left-color: #4a9d74 !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) { border-left-color: var(--cyan) !important; }
[data-testid="stAlertContainer"] p { color: var(--ink) !important; font-size: .86rem; }

/* -- quality floor -------------------------------------------------------- */
@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
  .stat-rail .stat { min-width: 6.5rem; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
</style>
"""


def page_setup(title: str) -> None:
    st.set_page_config(page_title=f"{title} - Originator Pricer", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_config() -> Config:
    return load_config()


def get_repo() -> Repository:
    """One repository per Streamlit session. SQLite connections are not thread-safe."""
    if "repo" not in st.session_state:
        cfg = get_config()
        repo = Repository(cfg.db_path)
        # First run of a download, and again after an upgrade ships a newer
        # sample: load the market the bundled model was trained on, so that
        # collecting adds to a dataset rather than starting one. Every other
        # session finds the fingerprint already stored and returns immediately.
        # See src/market/seed.py.
        seed_bundled_sample(cfg, repo)
        st.session_state["repo"] = repo
    return st.session_state["repo"]


def get_converter(cfg: Config, repo: Repository) -> CurrencyConverter:
    override = st.session_state.get("divine_override")
    if override:
        return CurrencyConverter(float(override))
    return converter_for(cfg, repo)


def get_formatter(cfg: Config | None = None, converter: CurrencyConverter | None = None) -> PriceFormatter:
    """The one place the display unit is decided. Session override wins."""
    cfg = cfg or get_config()
    converter = converter or get_converter(cfg, get_repo())
    unit = st.session_state.get("display_currency") or cfg.get("display.currency", "chaos")
    return PriceFormatter(unit=unit, divine_to_chaos=converter.divine_to_chaos)


def get_predictor() -> tuple[Predictor | None, str]:
    """Load the active model, or explain why there is not one."""
    stamp = st.session_state.get("model_reload", 0)
    return _load_predictor(stamp)


#: The stamp must NOT be named with a leading underscore. Streamlit reads that
#: prefix as "this argument is unhashable, leave it out of the cache key", which
#: is exactly the opposite of what it is for here: with `_stamp` every value
#: collided on one entry, so bumping the counter in invalidate_model_cache()
#: returned the same stale model and only the .clear() beside it did any work.
@st.cache_resource(show_spinner=False)
def _load_predictor(stamp: int) -> tuple[Predictor | None, str]:
    try:
        return Predictor.load(get_config()), ""
    except ModelNotTrained as exc:
        return None, str(exc)
    except ModelSchemaMismatch as exc:
        return None, f"Model rejected: {exc}"
    except Exception as exc:  # a corrupt artifact must not take the app down
        return None, f"Could not load the model: {exc}"


def get_catalogue() -> AffixCatalogue | None:
    """The affix contribution table, refitted whenever the model is."""
    return _load_catalogue(st.session_state.get("model_reload", 0))


@st.cache_resource(show_spinner=False)
def _load_catalogue(stamp: int) -> AffixCatalogue | None:  # see _load_predictor
    try:
        return AffixCatalogue.load(get_config().model_dir / CATALOGUE_NAME)
    except Exception:  # a corrupt sidecar must not take the page down
        return None


def get_exalt_cost() -> float:
    """What an Exalted Orb costs in chaos.

    A refreshed price is stored in the database, like the divine rate it sits
    beside -- it is a fetched fact, not a preference, so it has to survive a
    restart. Config supplies the starting value only.
    """
    stored = get_repo().get_setting("exalted_to_chaos")
    if stored:
        return float(stored)
    return float(get_config().get("currency.exalted_to_chaos", 1.25) or 1.25)


def flash(kind: str, message: str) -> None:
    """Queue a message that survives the st.rerun() which follows it.

    st.success() immediately before st.rerun() is discarded before it ever
    renders, so an action that worked looks like it did nothing.
    """
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    """Render and clear anything queued by flash(). Call early on a page."""
    queued = st.session_state.pop("_flash", None)
    if not queued:
        return
    kind, message = queued
    {"success": st.success, "error": st.error, "warning": st.warning}.get(
        kind, st.info
    )(message)


def invalidate_model_cache() -> None:
    st.session_state["model_reload"] = st.session_state.get("model_reload", 0) + 1
    _load_predictor.clear()
    _load_catalogue.clear()


# ---------------------------------------------------------------------------
# Cached reads.
#
# Streamlit reruns the whole script on every widget touch, so an uncached query
# here is paid again on every checkbox, tab and button -- including the ones
# that touch no data at all. The measured costs are not small: listing_rows()
# is ~150ms over a 5k-row table and observed_multipliers() ~185ms, and the
# Market Data page was paying both twice per rerun.
#
# The cache key is a version counter rather than a ttl, because the data only
# changes when this app changes it. Anything that writes to the listings table
# must call invalidate_data(); the alternative -- a short ttl -- would both
# re-query when nothing had changed and serve stale counts right after a
# collect, which is the one moment the number is being watched.
# ---------------------------------------------------------------------------


def data_version() -> int:
    """Bumped by every write to the listings table. Part of each cache key."""
    return st.session_state.get("data_version", 0)


def invalidate_data() -> None:
    """Call after collecting, importing, re-flagging or repricing."""
    st.session_state["data_version"] = data_version() + 1


#: As with _load_predictor, the version argument must NOT start with an
#: underscore -- Streamlit reads that prefix as "leave this out of the cache
#: key", which would pin every caller to the first snapshot ever read.
@st.cache_data(show_spinner=False, max_entries=4)
def listing_records(league: str | None, include_outliers: bool, version: int) -> list[dict]:
    """listing_rows() as plain dicts.

    Narrowed to PRICING_COLUMNS: no reader of this list looks at `raw_data`,
    and fetching it anyway was most of the query -- ~1.5s of JSON against 19ms
    for the same rows without it.

    sqlite3.Row cannot be pickled and therefore cannot be a cache_data return
    value. dict() is also what makes the rows safe to hand to pandas and to
    listing_rows_to_csv, both of which only ever subscript them by column name.
    """
    return [dict(row) for row in get_repo().listing_rows(
        league=league, include_outliers=include_outliers, columns=PRICING_COLUMNS
    )]


@st.cache_data(show_spinner=False, max_entries=4, ttl=60)
def listing_counts(league: str | None, version: int) -> dict[str, int]:
    """Counts for the sidebar and the stat rails.

    Carries a ttl as well as the version, because "last 24h" is a moving
    window: it goes stale on the clock, not on a write.
    """
    return get_repo().count_listings(league)


@st.cache_data(show_spinner=False, max_entries=4)
def usable_price_list(league: str | None, version: int) -> list[float]:
    return get_repo().usable_prices(league)


@st.cache_data(show_spinner=False, max_entries=4)
def latest_capture(version: int):
    return get_repo().latest_capture()


@st.cache_data(show_spinner=False, max_entries=8)
def _multipliers(
    data_ver: int, model_stamp: int, divine_to_chaos: float, tracking_ver: int
):
    """The expensive half of PricingService.build, on its own cache key.

    observed_multipliers() scores every recent in-scope listing with the model
    to see where real asks sit around fair value. That depends on the listings
    (data_ver), the model (model_stamp) and the chaos/divine rate that both are
    expressed in -- and on nothing else the user can touch, so it does not need
    recomputing when they open a tab.

    `tracking_ver` is the fourth key because the personal calibration is applied
    here, and it moves when the poller records something. The poller runs on a
    background thread and must not touch session state, so unlike data_ver that
    marker is read from the database.

    This is the one place strategy multipliers are built for the app, which is
    why the calibration is attached here: every page reaches prices through
    get_service(), so there is no surface where the factor could apply on one
    screen and not another.
    """
    import os
    from datetime import datetime, timezone

    from src.tracking.calibration import CalibrationConfig, apply_calibration
    from src.tracking.history import calibration_for_account

    cfg = get_config()
    base = multipliers_from_config(cfg.get("pricing.strategies", {}))
    predictor, _ = get_predictor()
    if predictor is None:
        return base
    observed = observed_multipliers(cfg, get_repo(), predictor, base)

    calibration = calibration_for_account(
        get_repo(),
        os.getenv("POE_ACCOUNT_NAME", "").strip(),
        cfg.get("league"),
        predictor.version,
        CalibrationConfig(
            min_sold=int(cfg.get("listing_tracker.calibration.min_sold") or 20),
            clamp=tuple(cfg.get("listing_tracker.calibration.clamp") or (0.8, 1.3)),
        ),
        datetime.now(timezone.utc),
    )
    return apply_calibration(
        observed,
        calibration,
        enabled=bool(cfg.get("listing_tracker.calibration.enabled", True)),
        active_model_version=predictor.version,
    )


def get_service(require_model: bool = False) -> tuple[PricingService | None, str]:
    cfg = get_config()
    repo = get_repo()
    predictor, why = get_predictor()
    if predictor is None and require_model:
        return None, why
    converter = get_converter(cfg, repo)
    # Constructed directly rather than through PricingService.build() so the
    # multipliers can come from the cache; the rest of build() is field
    # assignment and costs nothing.
    service = PricingService(
        cfg,
        repo,
        predictor,
        converter,
        _multipliers(
            data_version(),
            st.session_state.get("model_reload", 0),
            converter.divine_to_chaos,
            repo.tracking_version(),
        ),
        get_formatter(cfg, converter),
    )
    return service, why


def metric_cards(cards: list[dict[str, Any]]) -> None:
    html = ['<div class="metric-row">']
    for card in cards:
        sub = f'<div class="sub">{card.get("sub", "")}</div>' if card.get("sub") else ""
        # A model id is fifteen characters where a price is four. Without the
        # smaller class it wrapped onto a second line and made one card in the
        # row taller than the rest.
        value = str(card["value"])
        long = " long" if len(value) > 11 else ""
        html.append(
            f'<div class="metric-card"><div class="label">{card["label"]}</div>'
            f'<div class="value{long}">{value}</div>{sub}</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_confidence(level: str, score: float | None = None) -> None:
    """The confidence meter at reading size, for a detail panel.

    The same bar the grid tiles and the desktop overlay draw, from the same
    palette function, with the label and the score spelled out beside it. It
    replaced a line of text that said the same thing, because "Medium (0.51)"
    and "Medium (0.68)" are the same sentence at a glance and visibly different
    bars -- and the difference between them is most of what the number is for.
    """
    filling = confidence_fill(level, score)
    if filling is None:
        st.caption("Confidence: not scored")
        return
    colour, fraction = filling
    value = level if score is None else f"{level} &middot; {score:.2f}"
    st.markdown(
        '<div class="conf-row"><span>Confidence</span>'
        f'<span class="conf-bar"><span style="width:{fraction * 100:.0f}%;'
        f'background:{colour}"></span></span>'
        f'<span class="conf-value">{value}</span></div>',
        unsafe_allow_html=True,
    )


def page_header(eyebrow: str, title: str, lede: str = "") -> None:
    """The same three lines at the top of every page.

    An eyebrow naming which of the app's jobs this is, the title, and a brass
    hairline under it. Repeated identically everywhere so that a glance at the
    top-left always answers "where am I" -- which `st.title` alone never did,
    because a title on its own says what the page is called and nothing about
    where it sits.
    """
    body = f'<div class="lede">{lede}</div>' if lede else ""
    st.markdown(
        f'<div class="page-head"><div class="eyebrow">{eyebrow}</div>'
        f"<h1>{title}</h1>{body}<div class=\"rule\"></div></div>",
        unsafe_allow_html=True,
    )


def stat_rail(stats: list[tuple[str, Any]] | list[tuple[str, Any, str]]) -> None:
    """A row of labelled figures: the readout that replaces pipe-separated text.

    The app used to say things like "26 maps found | 25 priceable | 1
    unsupported | strategy multipliers from observed-spread" in one grey line.
    Every figure in it was worth reading and none of them could be found. Here
    each is a labelled cell with the number set in the mono face, so the row can
    be scanned rather than parsed.

    Each entry is `(label, value)` or `(label, value, note)`, where the note is
    the quiet half of the cell -- a unit, an age, a qualifier.
    """
    if not stats:
        return
    html = ['<div class="stat-rail">']
    for entry in stats:
        label, value = entry[0], entry[1]
        note = entry[2] if len(entry) > 2 else ""
        tail = f" <em>{note}</em>" if note else ""
        html.append(
            f'<div class="stat"><span class="k">{label}</span>'
            f'<span class="v">{value}{tail}</span></div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def section(eyebrow: str, note: str = "") -> None:
    """A section label in the same voice as the page eyebrow.

    Used where `st.subheader` was doing the job: a subheader is the same weight
    as a page title one level down, which made "Stash layout" and "Maps"
    compete with "Stash Pricer" for the eye.
    """
    tail = f'<span class="note">{note}</span>' if note else ""
    st.markdown(
        f'<div class="panel-head"><span class="eyebrow">{eyebrow}</span>{tail}</div>',
        unsafe_allow_html=True,
    )


def hint(text: str) -> None:
    """A line of explanation under a control or a section heading."""
    st.markdown(f'<div class="hint">{text}</div>', unsafe_allow_html=True)
def sidebar_context() -> None:
    """What every price on screen is relative to, as a key/value block.

    This was five grey captions of the same size and colour, which is a wall
    rather than a reference: the league, the rate, the unit, the sample and the
    model all read as one paragraph. Aligning the labels left and the values
    right, in the mono face, turns it into something that can be checked at a
    glance -- which is all it is ever used for.
    """
    cfg = get_config()
    repo = get_repo()
    converter = get_converter(cfg, repo)
    predictor, _ = get_predictor()
    counts = listing_counts(cfg.get("league"), data_version())
    scope = "uncorrupted" if cfg.get("scope.require_uncorrupted") else "any"
    model = (
        f'<span class="v ok">{predictor.version}</span>'
        if predictor
        else '<span class="v warn-text">none trained</span>'
    )
    rows = [
        ("League", f'<span class="v">{cfg.get("league")}</span>'),
        ("Scope", f'<span class="v">T{cfg.get("scope.map_tier")} {scope}</span>'),
        ("Divine", f'<span class="v">{converter.divine_to_chaos:.0f}c</span>'),
        ("Showing", f'<span class="v">{get_formatter(cfg, converter).unit}</span>'),
        ("Listings", f'<span class="v">{counts["total"]:,} <em></em></span>'),
        ("Flagged", f'<span class="v">{counts["outliers"]:,}</span>'),
        ("Model", model),
    ]
    # Only shown when it is actually in force. A suggested price that has been
    # scaled by your own sale history must say so wherever it appears, and this
    # block is already the answer to "relative to what?" -- but a permanent
    # "x1.00" row would be noise on every page for everyone who has no history.
    personal = _multipliers(
        data_version(),
        st.session_state.get("model_reload", 0),
        converter.divine_to_chaos,
        repo.tracking_version(),
    ).personal
    if abs(personal - 1.0) > 1e-9:
        rows.append(
            ("Your prices", f'<span class="v ok">&times;{personal:.2f}</span>')
        )
    body = "".join(
        f'<div class="row"><span class="k">{k}</span>{v}</div>' for k, v in rows
    )
    with st.sidebar:
        st.markdown(
            f'<div class="ctx"><div class="nav-group">Context</div>{body}</div>',
            unsafe_allow_html=True,
        )


def typical_focus(cfg, fmt) -> tuple[float, float] | None:
    """market.typical_price_chaos converted into whatever unit is on display."""
    bounds = cfg.get("market.typical_price_chaos")
    if not bounds or len(bounds) != 2:
        return None
    low, high = fmt.value(float(bounds[0])), fmt.value(float(bounds[1]))
    if low is None or high is None or high <= low:
        return None
    return (low, high)


def price_histogram(values, bins: int = 24, focus: tuple[float, float] | None = None):
    """Binned counts with string bucket labels.

    Streamlit charts through Altair, which cannot serialize a pandas Interval
    index, so the bucket edges are rendered as text here rather than at each
    call site.

    `focus` is an expected (low, high) trading range in the same unit as
    `values`. Without it a single 5000c ask stretches the axis so far that the
    30c-to-3-divine band where nearly every map actually sits collapses into the
    first two bars. With it, the bins span the focus range and everything
    outside is gathered into one bucket at each end -- so the tail is still
    visible and still counted, it just stops setting the scale.
    """
    import numpy as np
    import pandas as pd

    series = pd.Series([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if series.empty:
        return pd.DataFrame({"bucket": [], "listings": []})

    if focus:
        low, high = float(focus[0]), float(focus[1])
        under = int((series < low).sum())
        over = int((series > high).sum())
        inside = series[(series >= low) & (series <= high)]
        if not inside.empty and (under or over):
            counts = pd.cut(inside, bins=bins).value_counts().sort_index()
            span = high - low
            step = span / max(bins, 1)
            digits = 0 if step >= 1 else (1 if step >= 0.1 else 2)
            buckets = [f"{interval.right:.{digits}f}" for interval in counts.index]
            listings = list(counts.to_numpy())
            if under:
                buckets.insert(0, f"<{low:.{digits}f}")
                listings.insert(0, under)
            if over:
                buckets.append(f">{high:.{digits}f}")
                listings.append(over)
            return pd.DataFrame({"bucket": buckets, "listings": listings}).set_index("bucket")

    counts = pd.cut(series, bins=bins).value_counts().sort_index()
    # Adaptive precision: with narrow buckets, rounding to whole divine collapses
    # several labels onto the same string and the axis reads as 0,1,1,2,2.
    span = float(series.max() - series.min())
    step = span / max(bins, 1)
    digits = 0 if step >= 1 else (1 if step >= 0.1 else 2)
    return pd.DataFrame(
        {
            "bucket": [f"{interval.right:.{digits}f}" for interval in counts.index],
            "listings": counts.to_numpy(),
        }
    ).set_index("bucket")
