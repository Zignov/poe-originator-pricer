"""Settings page."""
from __future__ import annotations

import streamlit as st

from src.config import ROOT, user_agent
from src.env_file import UnwritableValue, read_values, write_values
from src.market.currency_source import ExchangeRateError, fetch_leagues
from src.ml import models as backends
from src.ml.train import NotEnoughData, train
from src.pricing.display import UNIT_LABELS, UNITS
from src.pricing.sell_strategy import STRATEGIES, STRATEGY_LABELS
from src.ui.common import (
    flash,
    get_config,
    get_exalt_cost,
    get_repo,
    invalidate_data,
    invalidate_model_cache,
    page_header,
    show_flash,
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

    if save.button("Save to .env", use_container_width=True):
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

    if forget.button("Forget", use_container_width=True):
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


def render() -> None:
    cfg = get_config()
    repo = get_repo()

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

    league_tab, market_tab, model_tab, account_tab = st.tabs(
        ["League and pricing", "Market data", "Model", "Account"]
    )

    with league_tab:
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
            repo.recompute_price_chaos(rate)
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

    with market_tab:
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
        if st.button("Apply", key="apply_market"):
            cfg.set("market.max_age_days", float(max_age))
            cfg.set("http.min_request_interval_seconds", float(interval))
            cfg.set("http.user_agent_contact", contact)
            st.success("Applied for this session.")

    with model_tab:
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
        retrain_hours = st.number_input(
            "Retraining interval (hours)", 1, 240, int(cfg.get("model.retrain_interval_hours", 24))
        )

        if st.button("Retrain model", type="primary"):
            cfg.set("model.backends", chosen)
            cfg.set("model.target", target)
            cfg.set("model.retrain_interval_hours", int(retrain_hours))
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

    with account_tab:
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
