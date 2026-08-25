"""Shared UI state, styling and helpers."""
from __future__ import annotations

from typing import Any

import streamlit as st

from src.config import Config, load_config
from src.database.repository import Repository
from src.market.ingest import converter_for
from src.ml.affix_catalogue import CATALOGUE_NAME, AffixCatalogue
from src.ml.predict import ModelNotTrained, ModelSchemaMismatch, Predictor
from src.pricing.currency import CurrencyConverter
from src.pricing.display import PriceFormatter
from src.pricing.fair_value import PricingService

# Re-exported: the palette itself lives in src/ui/palette.py so the desktop
# overlay, which runs in a tkinter process with no Streamlit in it, can share
# the same colours without importing this module.
from src.ui.palette import VALUE_COLOURS  # noqa: F401

CSS = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1400px; }

.metric-row { display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: 1rem; }
.metric-card {
  flex: 1 1 150px; padding: .7rem .9rem; border-radius: 10px;
  border: 1px solid rgba(128,128,128,.28); background: rgba(128,128,128,.07);
}
.metric-card .label {
  font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; opacity: .7;
}
.metric-card .value { font-size: 1.45rem; font-weight: 650; line-height: 1.25; }
.metric-card .sub { font-size: .75rem; opacity: .65; }

/* The grid is sized in fixed pixels (12 columns x 46px is wider than half of a
   1400px page), so it must be allowed to scroll inside its column. Without the
   wrapper it overflowed to the right and slid underneath the table beside it,
   which read as the table floating over the stash layout. */
.stash-grid-wrap {
  max-width: 100%; overflow-x: auto; padding-bottom: .35rem;
}
.stash-grid {
  display: grid; gap: 2px; background: rgba(128,128,128,.18);
  padding: 4px; border-radius: 8px; width: max-content;
  border: 1px solid rgba(128,128,128,.3);
}
.stash-cell {
  width: 46px; height: 46px; border-radius: 4px;
  background: rgba(128,128,128,.10);
  display: flex; align-items: center; justify-content: center;
  font-size: .68rem; font-weight: 600; color: #f4f4f5;
  overflow: hidden; text-align: center; line-height: 1.05;
}
.stash-cell.filled { color: #fff; box-shadow: inset 0 0 0 1px rgba(255,255,255,.18); }
/* Exalt state. Cyan appears nowhere in VALUE_COLOURS, so the ring stays legible
   on all four value backgrounds; solid vs dashed carries the same meaning again
   for anyone who cannot separate the two colours. Drawn with outline rather than
   border so tile size and grid alignment are untouched. */
.stash-cell.exalt-worth {
  outline: 2px solid #39d0d8;
  outline-offset: -2px;
}
.stash-cell.exalt-skip {
  outline: 2px dashed rgba(220,220,225,.5);
  outline-offset: -2px;
}

.stash-cell.unsupported {
  background: repeating-linear-gradient(45deg,
    rgba(128,128,128,.22) 0 5px, rgba(128,128,128,.10) 5px 10px);
  color: rgba(255,255,255,.55);
}
.legend { display: flex; gap: 1rem; flex-wrap: wrap; font-size: .78rem; margin: .6rem 0 1rem; }
.legend span.swatch {
  display: inline-block; width: 12px; height: 12px; border-radius: 3px;
  margin-right: .35rem; vertical-align: -1px;
}
.note-box {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .82rem; padding: .5rem .7rem; border-radius: 6px;
  background: rgba(128,128,128,.12); border: 1px solid rgba(128,128,128,.25);
}
.exalt-yes, .exalt-no {
  padding: .55rem .8rem; border-radius: 8px; font-size: .88rem; margin: .3rem 0 .6rem;
}
.exalt-yes { border: 1px solid rgba(60,160,110,.5); background: rgba(60,160,110,.13); }
.exalt-no  { border: 1px solid rgba(128,128,128,.4); background: rgba(128,128,128,.10); }

.warn {
  padding: .6rem .8rem; border-radius: 8px; font-size: .85rem;
  border: 1px solid rgba(200,140,40,.45); background: rgba(200,140,40,.12);
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
        st.session_state["repo"] = Repository(get_config().db_path)
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


@st.cache_resource(show_spinner=False)
def _load_predictor(_stamp: int) -> tuple[Predictor | None, str]:
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
def _load_catalogue(_stamp: int) -> AffixCatalogue | None:
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


def get_service(require_model: bool = False) -> tuple[PricingService | None, str]:
    cfg = get_config()
    repo = get_repo()
    predictor, why = get_predictor()
    if predictor is None and require_model:
        return None, why
    converter = get_converter(cfg, repo)
    service = PricingService.build(cfg, repo, predictor, converter)
    # A unit chosen in this session overrides config without a restart.
    service.formatter = get_formatter(cfg, converter)
    return service, why


def metric_cards(cards: list[dict[str, Any]]) -> None:
    html = ['<div class="metric-row">']
    for card in cards:
        sub = f'<div class="sub">{card.get("sub", "")}</div>' if card.get("sub") else ""
        html.append(
            f'<div class="metric-card"><div class="label">{card["label"]}</div>'
            f'<div class="value">{card["value"]}</div>{sub}</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def sidebar_context() -> None:
    cfg = get_config()
    repo = get_repo()
    converter = get_converter(cfg, repo)
    predictor, _ = get_predictor()
    with st.sidebar:
        st.markdown("### Context")
        st.caption(f"League **{cfg.get('league')}**  |  T{cfg.get('scope.map_tier')} "
                   f"{'uncorrupted' if cfg.get('scope.require_uncorrupted') else 'any'}")
        st.caption(f"1 divine = **{converter.divine_to_chaos:.0f}** chaos")
        st.caption(f"Showing prices in **{get_formatter(cfg, converter).unit}**")
        counts = repo.count_listings(cfg.get("league"))
        st.caption(f"{counts['total']} listings ({counts['outliers']} flagged)")
        st.caption(
            f"Model **{predictor.version}**" if predictor else ":orange[No trained model]"
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
