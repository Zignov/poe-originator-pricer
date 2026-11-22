"""Settings page."""
from __future__ import annotations

import streamlit as st

from src.config import ROOT, defaults_for, user_agent
from src.env_file import UnwritableValue, read_values, write_values
from src.market.currency_source import ExchangeRateError, fetch_leagues
from src.ml import models as backends
from src.ml.train import NotEnoughData, train
from src.overlay import appearance as overlay_appearance
from src.overlay.calibration import (
    MAX_OPACITY,
    MIN_CELL_PX,
    MIN_OPACITY,
    Calibration,
    save_to_config,
)
from src.overlay.profiles import PROFILES, PROFILE_KEYS
from src.pricing.display import UNIT_LABELS, UNITS
from src.pricing.sell_strategy import STRATEGIES, STRATEGY_LABELS
from src.ui import palette
from src.ui.common import (
    flash,
    get_config,
    get_exalt_cost,
    get_predictor,
    get_repo,
    invalidate_data,
    invalidate_model_cache,
    page_header,
    show_flash,
    tab_strip,
)


@st.cache_data(ttl=3600, show_spinner=False)
def league_options(agent: str) -> list[str]:
    """League ids GGG is serving, cached for an hour.

    Cached because it is the same answer all session and this page reruns on
    every widget touch; an uncached call would mean a request per keystroke
    against an endpoint that hands out five-minute bans.
    """
    return fetch_leagues(agent)


def _league_picker(cfg) -> str:
    """The League setting as a list of what GGG is actually serving.

    It was a free-text box, and a typo there does not fail loudly -- it produces
    an empty search and a rate that quietly stays on the config default. The
    live list makes the wrong league impossible to reach by accident, and falls
    back to the text box if the endpoint cannot be reached.
    """
    current = cfg.get("league")
    try:
        leagues = league_options(user_agent(cfg))
    except (ExchangeRateError, OSError) as exc:
        st.caption(f"Could not read GGG's league list ({exc}); type it instead.")
        return st.text_input("League", value=current)

    if current not in leagues:
        # Keep whatever is configured reachable, so opening this page cannot
        # silently move a working setup onto a different league.
        leagues = [current, *leagues]
    chosen = st.selectbox("League", leagues, index=leagues.index(current))
    st.caption(
        "Read live from GGG's league list. Everything else on this page — "
        "collection, the orb prices, the stash reader — follows this setting."
    )
    return chosen


ENV_PATH = ROOT / ".env"

#: Session-state key -> the name it is stored under in .env.
CREDENTIAL_KEYS = {"poesessid": "POESESSID", "account_name": "POE_ACCOUNT_NAME"}


def _stored_credential(session_key: str, env_key: str) -> str:
    """What the field should show: this session's value, else what was saved.

    Only the *first* render of the page falls through to .env. After that the
    widget has written session state on every rerun, and preferring that is
    what lets a field be edited -- or deliberately emptied -- without the saved
    value reappearing underneath the cursor.
    """
    if session_key in st.session_state:
        return st.session_state[session_key]
    return read_values(ENV_PATH).get(env_key, "")


def _credential_buttons() -> None:
    """Save the credentials to .env, or clear them from it.

    Saving writes the file *and* re-loads it with override, so the adapters
    that read os.getenv() at construction see the new value in this same
    session rather than only after a restart.
    """
    from dotenv import load_dotenv

    save, forget = st.columns(2)

    if save.button("Save to .env", width="stretch"):
        try:
            path = write_values(
                ENV_PATH,
                {env: st.session_state.get(key, "") for key, env in CREDENTIAL_KEYS.items()},
            )
        except UnwritableValue as exc:
            st.error(str(exc))
        else:
            load_dotenv(path, override=True)
            flash("success", f"Saved to {path.name}. It will be there next time you start.")
            st.rerun()

    if forget.button("Forget", width="stretch"):
        path = write_values(ENV_PATH, {env: "" for env in CREDENTIAL_KEYS.values()})
        load_dotenv(path, override=True)
        for key in CREDENTIAL_KEYS:
            st.session_state.pop(key, None)
        flash("success", f"Cleared from {path.name}.")
        st.rerun()

    st.caption(
        f"Saved in `{ENV_PATH.name}` beside the app, which is gitignored and never "
        "leaves this machine. It is a plain text file, so this makes the cookie "
        "persist -- it does not encrypt it. POESESSID expires on its own; when GGG "
        "starts answering 403, paste a fresh one."
    )


# ---------------------------------------------------------------------------
# Appearance
#
# Three stores, one page, and they are not interchangeable:
#
# - **config.yaml**, under `appearance:`. The one the app owns. Everything the
#   overlay reads and every palette override lives here, and the Apply button
#   writes the file rather than only the session, because the overlay is a
#   separate process that cannot see anything left in memory.
# - **.streamlit/config.toml**, under `[theme]`. Streamlit's own, read once at
#   startup. It is the only way to reach the widgets Streamlit draws for itself
#   -- the dataframe canvas, the sidebar ground, the focus colours -- and that
#   is precisely why it cannot be applied live. The page says so where it
#   matters rather than pretending the change landed.
# - **the live palette dictionaries** in src/ui/palette.py, which the web grid
#   and the overlay both subscript at draw time. Writing them is what makes a
#   re-coloured band show up on the Stash Pricer without a restart.
#
# Nothing here is a second copy of a setting: config.yaml is the record, the
# TOML is a projection of the `appearance.web` half of it, and the dictionaries
# are that record loaded.
# ---------------------------------------------------------------------------

#: Streamlit's own config file, beside the app. Its contents are generated in
#: src/ui/palette.py, next to the tokens they are a projection of.
STREAMLIT_CONFIG = ROOT / ".streamlit" / "config.toml"


def _write_streamlit_theme(theme: str, accent: str, font: str) -> None:
    STREAMLIT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    STREAMLIT_CONFIG.write_text(
        palette.streamlit_theme_toml(theme, accent, font), encoding="utf-8"
    )


def _web_appearance(cfg) -> dict:
    """The web half of the appearance, preferring this page's live widgets.

    Read before the widgets are drawn, so the stylesheet at the top of the page
    can be built from what the user has just picked rather than from what was
    last saved. Streamlit has already put the previous run's widget values in
    session state under these keys, so a picker moves and the very next rerun
    is already wearing the new colour -- with no Apply, and nothing written to
    disk until there is.
    """
    stored = cfg.get("appearance.web") or {}
    default = defaults_for("appearance.web") or {}

    def pick(key: str, allowed: tuple[str, ...] | None = None) -> str:
        """This run's widget, else the saved value, else the shipped one.

        `allowed` is not belt and braces: a hand-edited config.yaml is the one
        source here that nothing has validated, and a `theme: drak` would
        otherwise reach a selectbox index lookup and raise on a page whose whole
        job is to let you fix settings.
        """
        value = st.session_state.get(f"ap_{key}", stored.get(key, default[key]))
        if allowed is not None and value not in allowed:
            return default[key]
        return value

    accent = pick("accent")
    return {
        "theme": pick("theme", palette.THEME_KEYS),
        # Checked for the same reason, and more sharply: `st.color_picker`
        # raises on a value it cannot parse, so an unparseable accent would
        # take the page down rather than merely look wrong.
        "accent": accent if palette.is_hex_colour(accent) else default["accent"],
        "font": pick("font"),
        "font_scale": palette.clamp_font_scale(pick("font_scale")),
        "density": pick("density", palette.DENSITY_KEYS),
    }


def _swatch_row(colours: dict[str, str]) -> None:
    """A strip of the colours as they will actually be drawn.

    Streamlit's colour pickers are eight identical little squares in a row of
    labelled boxes; what the user is choosing is a *ramp*, and whether it reads
    as one can only be judged by seeing the bands next to each other at tile
    size. So the preview is drawn rather than described.
    """
    cells = "".join(
        f'<div style="flex:1;min-width:52px;background:{colour};color:'
        f'{palette.value_text_colour(name)};padding:.5rem .3rem;text-align:center;'
        f'font-size:.62rem;letter-spacing:.06em;border:1px solid #0e0f12;">'
        f"{name}</div>"
        for name, colour in colours.items()
    )
    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;border-radius:6px;'
        f'overflow:hidden;margin:.2rem 0 .6rem;">{cells}</div>',
        unsafe_allow_html=True,
    )


def _reset_appearance(cfg) -> None:
    """Put every appearance setting back to the shipped one, and save.

    Calibration is deliberately untouched apart from the opacity. Origin, cell
    pitch, grid and the resolution stamp are a measurement of the user's screen
    that took a minute with a keyboard to get right; wiping them because
    somebody wanted the default colours back would be the worst thing this
    button could do.
    """
    shipped = defaults_for("appearance") or {}
    cfg.set("appearance", shipped)
    palette.reset_palette()

    default_opacity = Calibration().opacity
    for key in PROFILE_KEYS:
        spec = PROFILES[key]
        calibration = Calibration.from_config(cfg, spec)
        cfg.set(spec.config_key, calibration.with_opacity(default_opacity).to_mapping())

    web = shipped.get("web", {})
    _write_streamlit_theme(web["theme"], web["accent"], web["font"])
    # The widgets read session state first, so leaving these behind would draw
    # the page back exactly as it was and make the button look inert.
    for key in list(st.session_state):
        if str(key).startswith("ap_"):
            del st.session_state[key]
    cfg.save()


def _appearance_tab(cfg) -> None:
    """The Appearance section: how both front ends look, and where that is kept."""
    web = _web_appearance(cfg)
    stored_overlay = overlay_appearance.OverlayAppearance.from_config(cfg)

    # -- the web app ------------------------------------------------------
    st.markdown("**This web app**")
    st.caption(
        "Previewed on this page as you pick, and saved to `.streamlit/"
        "config.toml` -- which Streamlit reads once, at startup, so the widgets "
        "it draws for itself (tables, the sidebar ground, focus rings) follow "
        "after a restart. Carrying the scheme onto the *other* pages needs "
        "`page_setup` in `src/ui/common.py` to emit `palette.appearance_css()` "
        "the way this page does; until it does, they keep the shipped dark "
        "styling. The value bands below have no such caveat -- they are live "
        "everywhere, immediately."
    )
    cols = st.columns(3)
    # No `in` guards on these indexes: `_web_appearance` has already resolved
    # anything a hand-edited config could hold back to a known key.
    cols[0].selectbox(
        "Colour scheme",
        palette.THEME_KEYS,
        index=palette.THEME_KEYS.index(web["theme"]),
        format_func=lambda key: palette.THEME_LABELS[key],
        key="ap_theme",
    )
    cols[1].color_picker("Accent colour", value=web["accent"], key="ap_accent")
    cols[2].selectbox(
        "Interface font",
        palette.WEB_FONTS,
        index=palette.WEB_FONTS.index(web["font"]) if web["font"] in palette.WEB_FONTS else 0,
        # The stack, not just the first name: what is shown is what is written
        # to both files, and the fallbacks are the reason a machine without the
        # first face still gets the right kind of one.
        format_func=lambda stack: stack.split(",")[0].strip("'\""),
        key="ap_font",
    )
    cols = st.columns(2)
    cols[0].slider(
        "Text size",
        palette.MIN_FONT_SCALE,
        palette.MAX_FONT_SCALE,
        float(web["font_scale"]),
        step=0.05,
        key="ap_font_scale",
    )
    cols[1].radio(
        "Density",
        palette.DENSITY_KEYS,
        index=palette.DENSITY_KEYS.index(web["density"]),
        format_func=lambda key: palette.DENSITY_LABELS[key],
        horizontal=True,
        key="ap_density",
    )
    st.caption(
        "Accent is structure, never data: it colours buttons, rules and the "
        "active nav marker, and is deliberately not one of the value-band hues "
        "so a button can never be mistaken for a price."
    )

    # -- the shared palette -----------------------------------------------
    st.divider()
    st.markdown("**Value bands**")
    st.caption(
        "Shared. These are the colours the Stash Pricer paints tiles with *and* "
        "the ones the desktop overlay draws over the game -- one definition, so "
        "a map that is amber in the browser is amber on the tab."
    )
    bands = {
        name: st.session_state.get(f"ap_band_{name}", palette.VALUE_COLOURS[name])
        for name in palette.VALUE_COLOUR_KEYS
    }
    _swatch_row(bands)
    for row_start in range(0, len(palette.VALUE_COLOUR_KEYS), 4):
        row = palette.VALUE_COLOUR_KEYS[row_start : row_start + 4]
        for column, name in zip(st.columns(4), row):
            column.color_picker(
                name, value=palette.VALUE_COLOURS[name], key=f"ap_band_{name}"
            )

    st.markdown("**Confidence meter**")
    st.caption(
        "The meter under each price, in both front ends. Traffic-light hues by "
        "default, because the one thing it answers is how much to trust the "
        "number above it."
    )
    for column, level in zip(st.columns(3), palette.CONFIDENCE_COLOUR_KEYS):
        column.color_picker(
            level, value=palette.CONFIDENCE_COLOURS[level], key=f"ap_conf_{level}"
        )

    # -- the overlay -------------------------------------------------------
    st.divider()
    st.markdown("**Desktop overlay**")
    st.caption(
        "The overlay runs as its own process and reads these when it opens, so "
        "close and reopen it to see a change. Nothing here can move a price onto "
        "a different map -- that is the calibration, and it is left alone."
    )

    profile_key = st.selectbox(
        "Surface",
        PROFILE_KEYS,
        format_func=lambda key: PROFILES[key].label,
        key="ap_profile",
    )
    spec = PROFILES[profile_key]
    calibration = Calibration.from_config(cfg, spec)

    cols = st.columns(4)
    opacity = cols[0].slider(
        "Opacity",
        MIN_OPACITY,
        MAX_OPACITY,
        float(calibration.opacity),
        step=0.05,
        key=f"ap_opacity_{profile_key}",
    )
    cell_px = cols[1].number_input(
        "Cell size (px)",
        MIN_CELL_PX,
        400.0,
        float(calibration.cell_px),
        step=0.1,
        format="%.2f",
        key=f"ap_cell_{profile_key}",
    )
    origin_x = cols[2].number_input(
        "Grid left (px)", value=int(calibration.origin[0]), step=1,
        key=f"ap_origin_x_{profile_key}",
    )
    origin_y = cols[3].number_input(
        "Grid top (px)", value=int(calibration.origin[1]), step=1,
        key=f"ap_origin_y_{profile_key}",
    )
    st.caption(
        f"Per surface -- these four are the **{spec.label}** overlay's own, and "
        "everything below is shared by both. Position and cell size are far "
        "easier to get right from the overlay itself "
        f"(`python -m src.cli overlay --profile {profile_key} --calibrate`), "
        "which shows the grid over the real one while you nudge it; they are "
        "here so a known-good number can be typed straight in."
    )

    cols = st.columns(4)
    font_family = cols[0].selectbox(
        "Label font",
        overlay_appearance.FONT_FAMILIES,
        index=overlay_appearance.FONT_FAMILIES.index(stored_overlay.font_family)
        if stored_overlay.font_family in overlay_appearance.FONT_FAMILIES
        else 0,
        key="ap_ov_font",
    )
    font_scale = cols[1].slider(
        "Label size",
        overlay_appearance.MIN_FONT_SCALE,
        overlay_appearance.MAX_FONT_SCALE,
        float(stored_overlay.font_scale),
        step=0.05,
        key="ap_ov_font_scale",
    )
    bold_labels = cols[2].checkbox(
        "Bold labels", value=bool(stored_overlay.bold_labels), key="ap_ov_bold"
    )
    outline_width = cols[3].number_input(
        "Tile outline width",
        overlay_appearance.MIN_OUTLINE_WIDTH,
        overlay_appearance.MAX_OUTLINE_WIDTH,
        int(stored_overlay.outline_width),
        step=1,
        key="ap_ov_outline_width",
    )
    st.caption(
        "Label size multiplies the size the overlay already derives from your "
        "cell pitch, so it stays right across resolutions. Outline width 0 "
        "removes the hairline between tiles -- readable on a dark tab, but it "
        "is also what makes the alignment with the stash cells checkable."
    )

    cols = st.columns(4)
    ring_worth = cols[0].color_picker(
        "Exalt ring", value=stored_overlay.ring_worth, key="ap_ov_ring_worth"
    )
    ring_skip = cols[1].color_picker(
        "Not-worth-it ring", value=stored_overlay.ring_skip, key="ap_ov_ring_skip"
    )
    outline = cols[2].color_picker(
        "Tile outline", value=stored_overlay.outline, key="ap_ov_outline"
    )
    dots_fill = cols[3].color_picker(
        "Free-affix dots", value=stored_overlay.dots_fill, key="ap_ov_dots"
    )
    st.caption(
        "The exalt ring also colours the control strip's label and the "
        "calibration screen's alignment lines, so the whole overlay reads as one "
        "thing."
    )

    cols = st.columns([1, 1, 2])
    show_meter = cols[0].checkbox(
        "Confidence meter", value=bool(stored_overlay.show_meter), key="ap_ov_meter"
    )
    show_dots = cols[1].checkbox(
        "Free-affix dots", value=bool(stored_overlay.show_dots), key="ap_ov_show_dots"
    )
    tinted = cols[2].checkbox(
        "Tint the whole grid, not just the priced cells",
        value=bool(stored_overlay.tint),
        key="ap_ov_tinted",
    )
    tint = ""
    if tinted:
        tint = st.color_picker(
            "Grid tint",
            value=stored_overlay.tint or "#101317",
            key="ap_ov_tint",
        )
        st.warning(
            "A tint fills the empty cells too, so the maps under them stop "
            "showing through. Lower the opacity above to compensate."
        )
    else:
        st.caption(
            "Off by default: the game showing through every cell with no price "
            "is the difference between an overlay and a screenshot."
        )

    with st.expander("Overlay control strip and unsupported tiles"):
        cols = st.columns(2)
        strip_anchor = cols[0].radio(
            "Control strip sits",
            overlay_appearance.STRIP_ANCHORS,
            index=overlay_appearance.STRIP_ANCHORS.index(stored_overlay.strip_anchor),
            format_func=lambda side: f"{side} the grid",
            horizontal=True,
            key="ap_ov_strip_anchor",
        )
        strip_width = cols[1].number_input(
            "Control strip width (px)",
            overlay_appearance.MIN_STRIP_WIDTH,
            overlay_appearance.MAX_STRIP_WIDTH,
            int(stored_overlay.strip_width),
            step=10,
            key="ap_ov_strip_width",
        )
        st.caption(
            "'Above' still drops below the grid when there is no room over it: "
            "a strip at a negative y is the one control surface the overlay has, "
            "placed where nobody can click it."
        )
        cols = st.columns(2)
        muted_text = cols[0].color_picker(
            "Unsupported label", value=stored_overlay.muted_text, key="ap_ov_muted_text"
        )
        meter_track = cols[1].color_picker(
            "Meter track", value=stored_overlay.meter_track, key="ap_ov_track"
        )
        st.caption(
            "An unsupported map still gets a tile showing a dash. Drawing "
            "nothing would read as 'the overlay has not loaded' rather than "
            "'this one cannot be priced'."
        )

    # -- apply -------------------------------------------------------------
    st.divider()
    apply_col, reset_col = st.columns([1, 1])
    if apply_col.button("Apply appearance", type="primary", key="apply_appearance"):
        # Built from what is stored rather than from a fresh default, so a key
        # this page does not offer -- `muted_fill`, set by hand in config.yaml
        # -- survives an Apply instead of being quietly reset to the default.
        chosen = stored_overlay.with_values(
            font_family=font_family,
            font_scale=float(font_scale),
            bold_labels=bool(bold_labels),
            ring_worth=ring_worth,
            ring_skip=ring_skip,
            outline=outline,
            outline_width=int(outline_width),
            tint=tint,
            muted_text=muted_text,
            meter_track=meter_track,
            dots_fill=dots_fill,
            show_meter=bool(show_meter),
            show_dots=bool(show_dots),
            strip_anchor=strip_anchor,
            strip_width=int(strip_width),
        )
        overlay_appearance.save_to_config(cfg, chosen)

        # Re-read rather than reusing the `web` from the top of the function:
        # the widgets have been drawn since, so this is what is on screen now
        # rather than what was on screen when the page started.
        chosen_web = _web_appearance(cfg)
        cfg.set("appearance.web", chosen_web)

        # Written into the live dictionaries first and read back out second, so
        # what is stored is what was accepted: an unparseable hex from a hand
        # edit is dropped by `apply_palette_overrides` rather than saved for the
        # overlay to choke on.
        palette.apply_palette_overrides(
            {
                "value_colours": {
                    name: st.session_state[f"ap_band_{name}"]
                    for name in palette.VALUE_COLOUR_KEYS
                },
                "confidence_colours": {
                    level: st.session_state[f"ap_conf_{level}"]
                    for level in palette.CONFIDENCE_COLOUR_KEYS
                },
            }
        )
        cfg.set("appearance.palette", palette.palette_overrides_only())

        updated = (
            calibration.with_opacity(float(opacity))
            .with_cell_px(float(cell_px))
            .with_origin(int(origin_x), int(origin_y))
        )
        # This writes config.yaml -- with everything set above in it, since the
        # whole config is dumped -- so the overlay process finds all of it.
        path = save_to_config(cfg, updated, profile=spec)

        _write_streamlit_theme(
            chosen_web["theme"], chosen_web["accent"], chosen_web["font"]
        )
        flash(
            "success",
            f"Appearance saved to {path.name} and {STREAMLIT_CONFIG.name}. "
            "Restart the app for Streamlit's own widgets, and reopen the overlay "
            "for the tiles.",
        )
        st.rerun()

    if reset_col.button("Reset appearance to defaults", key="reset_appearance"):
        _reset_appearance(cfg)
        flash(
            "success",
            "Appearance back to the shipped values. Your overlay calibration "
            "was not touched.",
        )
        st.rerun()


def _calibration_status(cfg) -> str:
    """What the personal factor currently reads, refusals included."""
    import os
    from datetime import datetime, timezone

    from src.tracking.calibration import CalibrationConfig
    from src.tracking.history import calibration_for_account
    from src.ui.my_listings import calibration_note

    predictor, _ = get_predictor()
    if predictor is None:
        return "No model is trained, so there is nothing to calibrate against yet."
    try:
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
    except Exception as exc:  # noqa: BLE001 - a settings caption must never blank the page
        return f"Could not read your listing history: {exc}"
    return calibration_note(calibration)


def render() -> None:
    cfg = get_config()
    repo = get_repo()

    # The appearance stylesheet, before anything is drawn. Emitted after
    # `page_setup` has already put src/ui/common.py's sheet on the page, which
    # is what lets a same-specificity `:root` block win, and built from the
    # widgets below rather than from the saved values -- so a colour picked
    # here is worn by the next rerun without waiting for Apply.
    #
    # Only this page: `page_setup` is where a sheet reaches every page, and it
    # is not this module's to change. The Streamlit theme written by Apply is
    # the part that is app-wide.
    web = _web_appearance(cfg)
    st.markdown(
        palette.appearance_css(
            theme=web["theme"],
            accent=web["accent"],
            font=web["font"],
            font_scale=web["font_scale"],
            density=web["density"],
        ),
        unsafe_allow_html=True,
    )

    # This page reruns after applying a league, a display unit and a retrain.
    # Without this every message queued for those reruns is discarded, which is
    # how retraining came to look like it had done nothing at all.
    show_flash()

    page_header(
        "Tool",
        "Settings",
        "Changes apply to this session straight away. Save to config.yaml to "
        "keep them after a restart.",
    )

    open_tab = tab_strip(
        "settings_tab",
        ["League and pricing", "Market data", "Model", "Appearance", "Account"],
    )

    if open_tab == "League and pricing":
        league = _league_picker(cfg)
        col1, col2 = st.columns(2)
        tier = col1.number_input(
            "Map tier to price", 1, 17, int(cfg.get("scope.map_tier", 16))
        )
        uncorrupted = col2.checkbox(
            "Uncorrupted only", value=bool(cfg.get("scope.require_uncorrupted", True))
        )
        st.caption(
            "Note: on the live trade site the highest-value T16 Originator Maps are "
            "corrupted 8-mod maps. With 'Uncorrupted only' on, those are shown but "
            "marked Unsupported rather than priced."
        )

        st.markdown("**Display currency**")
        current_unit = st.session_state.get("display_currency") or cfg.get(
            "display.currency", "chaos"
        )
        unit = st.radio(
            "Show prices in",
            UNITS,
            index=UNITS.index(current_unit) if current_unit in UNITS else 0,
            format_func=lambda u: f"{u} ({UNIT_LABELS[u]})" if u == "auto" else u,
            horizontal=True,
        )
        st.caption(
            "Everything is stored and modelled in chaos regardless; this only "
            "changes what you read. Uncorrupted T16 Originator Maps mostly trade "
            "between about 50 and 200 chaos, which is why chaos is the default."
        )
        if unit != current_unit:
            st.session_state["display_currency"] = unit
            cfg.set("display.currency", unit)
            st.rerun()

        st.markdown("**Orb prices in chaos**")
        st.caption(
            "Type over either one to override what poe.ninja reported. "
            "Refresh them from the live market on the **Market Data** page."
        )
        stored = repo.get_divine_rate(cfg.get("league"))
        default_rate = stored[0] if stored else float(cfg.get("currency.divine_to_chaos"))
        orb_cols = st.columns(2)
        rate = orb_cols[0].number_input(
            "1 Divine Orb =", 1.0, 100000.0, float(default_rate), step=1.0
        )
        # A fine step and two decimals because the orb is *cheap* -- it sits
        # near 1.25 chaos, where a step of 1 is most of the value.
        exalt_cost = orb_cols[1].number_input(
            "1 Exalted Orb =", 0.01, 100000.0, float(get_exalt_cost()),
            step=0.05, format="%.2f",
        )
        st.caption(
            "Divine is what every stored listing price is normalised through. "
            "Exalted is what an exalt costs, which is the whole right-hand side "
            "of the exalt-worthy verdict on the Stash Pricer."
        )
        if st.button("Apply orb prices and recompute stored listings"):
            repo.set_divine_rate(league, rate, source="manual")
            # Re-flags outliers too -- see src/market/ingest.py.
            from src.market.ingest import restate_prices

            restate_prices(cfg, repo, rate)
            invalidate_data()  # every price_chaos in the table just moved
            st.session_state["divine_override"] = rate
            # Stored rather than session-scoped, for the same reason the fetched
            # price is: a hand-set orb cost that vanished on restart would leave
            # the exalt verdict silently back on the config default.
            repo.set_setting("exalted_to_chaos", float(exalt_cost))
            st.success(
                f"Divine = {rate:.1f}c, Exalted = {exalt_cost:.1f}c. "
                "Every stored price recomputed."
            )

        st.markdown("**Selling strategy multipliers**")
        strategies = dict(cfg.get("pricing.strategies", {}))
        cols = st.columns(3)
        for index, name in enumerate(STRATEGIES):
            strategies[name] = cols[index].number_input(
                STRATEGY_LABELS[name], 0.1, 3.0, float(strategies.get(name, 1.0)), step=0.01
            )
        st.caption(
            "These are the starting values. Once enough listings carry both a model "
            "estimate and a real ask, `src/pricing/sell_strategy.py` re-derives them "
            "from the observed spread instead."
        )

        if st.button("Apply", type="primary", key="apply_league"):
            cfg.set("league", league)
            cfg.set("scope.map_tier", int(tier))
            cfg.set("scope.require_uncorrupted", bool(uncorrupted))
            cfg.set("pricing.strategies", strategies)
            cfg.set("currency.divine_to_chaos", float(rate))
            cfg.set("currency.exalted_to_chaos", float(exalt_cost))
            cfg.set("display.currency", unit)
            st.success("Applied for this session.")

    if open_tab == "Market data":
        max_age = st.number_input(
            "Market-data age limit (days)", 0.5, 60.0, float(cfg.get("market.max_age_days", 7)), step=0.5
        )
        st.caption("Listings older than this are excluded from training and comparables.")
        interval = st.number_input(
            "Minimum seconds between API requests",
            0.5,
            30.0,
            float(cfg.get("http.min_request_interval_seconds", 2.0)),
            step=0.5,
        )
        st.caption(
            "A floor only. The client always also obeys whatever rate-limit policy "
            "GGG returns in its response headers."
        )
        contact = st.text_input(
            "Contact address sent in the User-Agent",
            value=cfg.get("http.user_agent_contact", ""),
        )
        st.caption("GGG asks automated clients to identify themselves with a real address.")

        st.divider()
        track = st.checkbox(
            "Watch my own listings while the app is open",
            value=bool(cfg.get("listing_tracker.enabled", False)),
        )
        st.caption(
            "One trade search for your own account every 5-15 minutes, so My "
            "Listings can show what has not sold. Needs your account name above."
        )
        calibrate = st.checkbox(
            "Let my own sales adjust suggested prices",
            value=bool(cfg.get("listing_tracker.calibration.enabled", True)),
        )
        # The number this switch controls is the only one in the app that moves
        # prices for reasons not present in the market data, so the current
        # reading is printed next to it -- including the refusals. Turning it off
        # should be an informed choice, not a guess about what it is doing.
        st.caption(_calibration_status(cfg))

        if st.button("Apply", key="apply_market"):
            cfg.set("market.max_age_days", float(max_age))
            cfg.set("http.min_request_interval_seconds", float(interval))
            cfg.set("http.user_agent_contact", contact)
            cfg.set("listing_tracker.enabled", bool(track))
            cfg.set("listing_tracker.calibration.enabled", bool(calibrate))
            st.success("Applied for this session.")

    if open_tab == "Model":
        available = backends.available_backends()
        st.caption(f"Backends available in this environment: {', '.join(available)}")
        chosen = st.multiselect(
            "Backends to try (best on validation wins)",
            list(backends.BACKENDS),
            default=[b for b in cfg.get("model.backends", []) if b in available] or available[:1],
        )
        target = st.selectbox(
            "Target transform",
            ["log1p", "raw"],
            index=0 if cfg.get("model.target") == "log1p" else 1,
            help="log1p handles the long tail of listing prices; predictions are transformed back.",
        )
        optional = st.multiselect(
            "Optional context features",
            [
                "item_level",
                "quality",
                "hour_of_day",
                "day_of_week",
                "days_since_first_observation",
                "divine_to_chaos",
            ],
            default=[],
            help="Include only if the validation metrics improve.",
        )
        tune = st.checkbox(
            "Search recency-weight schedules",
            help="Tries several age-weighting schedules and keeps the best on validation.",
        )
        if st.button("Retrain model", type="primary"):
            cfg.set("model.backends", chosen)
            cfg.set("model.target", target)
            log_area = st.empty()
            lines: list[str] = []

            def log(message: str) -> None:
                lines.append(str(message))
                log_area.code("\n".join(lines), language="text")

            with st.spinner("Training..."):
                try:
                    train(cfg, optional_features=optional, tune_weights=tune, log=log)
                except NotEnoughData as exc:
                    st.error(str(exc))
                else:
                    invalidate_model_cache()
                    # Rerun rather than print. The page above this line was
                    # drawn from the model that has just been replaced, so
                    # without a rerun the only thing that changes on screen is
                    # this message -- which is why the button read as broken.
                    flash("success", "Trained. Every page now prices with the new model.")
                    st.rerun()

    if open_tab == "Appearance":
        _appearance_tab(cfg)

    if open_tab == "Account":
        st.markdown(
            "**POESESSID** is your own browser session cookie for pathofexile.com. It is "
            "sent only to GGG's own site, to read your own stash. Treat it like a "
            "password: never share it."
        )
        st.session_state["poesessid"] = st.text_input(
            "POESESSID", value=_stored_credential("poesessid", "POESESSID"), type="password"
        )
        st.session_state["account_name"] = st.text_input(
            "Account name (including the #1234 discriminator)",
            value=_stored_credential("account_name", "POE_ACCOUNT_NAME"),
        )
        _credential_buttons()
        st.markdown("---")
        st.markdown(
            "**Official OAuth** is the supported route, but GGG registers OAuth clients "
            "by hand -- there is no self-service signup. `src/auth/poe_oauth.py` "
            "implements the flow and is wired into `OAuthStashSource`, but it has not "
            "been verified against a real client. See `docs/data-access.md`."
        )

    st.markdown("---")
    if st.button("Save to config.yaml"):
        path = cfg.save()
        st.success(f"Wrote {path}")
