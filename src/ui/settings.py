"""Settings page."""
from __future__ import annotations

import streamlit as st

from src.ml import models as backends
from src.ml.train import NotEnoughData, train
from src.pricing.display import UNIT_LABELS, UNITS
from src.pricing.sell_strategy import STRATEGIES, STRATEGY_LABELS
from src.ui.common import get_config, get_repo, invalidate_model_cache


def render() -> None:
    cfg = get_config()
    repo = get_repo()

    st.title("Settings")
    st.caption(
        "Changes apply to this session immediately. Use **Save to config.yaml** to "
        "make them permanent."
    )

    league_tab, market_tab, model_tab, account_tab = st.tabs(
        ["League and pricing", "Market data", "Model", "Account"]
    )

    with league_tab:
        league = st.text_input("League", value=cfg.get("league"))
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

        st.markdown("**Divine / chaos rate**")
        stored = repo.get_divine_rate(cfg.get("league"))
        default_rate = stored[0] if stored else float(cfg.get("currency.divine_to_chaos"))
        rate = st.number_input("1 divine =", 1.0, 100000.0, float(default_rate), step=1.0)
        if st.button("Apply rate and recompute stored prices"):
            repo.set_divine_rate(league, rate, source="manual")
            repo.recompute_price_chaos(rate)
            st.session_state["divine_override"] = rate
            st.success("Rate applied and every stored price recomputed.")

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
                    st.success("Trained. Reload any open page to use the new model.")

    with account_tab:
        st.markdown(
            "**POESESSID** is your own browser session cookie for pathofexile.com. It is "
            "sent only to GGG's own site, to read your own stash. Treat it like a "
            "password: never share it, and it is not written to disk by this page."
        )
        st.session_state["poesessid"] = st.text_input(
            "POESESSID", value=st.session_state.get("poesessid", ""), type="password"
        )
        st.session_state["account_name"] = st.text_input(
            "Account name (including the #1234 discriminator)",
            value=st.session_state.get("account_name", ""),
        )
        st.caption(
            "To persist these, put POESESSID and POE_ACCOUNT_NAME in a .env file "
            "(see .env.example). .env is gitignored."
        )
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
