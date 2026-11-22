"""What you have listed, what has not sold, and what to do about it.

The page answers one question -- which of my maps are sitting -- and then makes
the answer actionable in the two places it is useful: a stash search that lights
the stale maps up in game, and an overlay that puts the new price onto them
where they physically are.

The stale set is the well-measured half of this feature. A disappearance is only
ever bracketed, because polling stops when the app closes and a map that sold
overnight is known only to have sold "sometime in nine hours". But a map still
listed after sixty-two hours has been listed for sixty-two hours whatever the
gaps, so every number on this page is exact.

The decisions live in `stale_rows`, `regex_for_stale` and `overlay_groups`
rather than inside `render`, so they are tested without a browser.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from src.stash.highlight_regex import HighlightBatches, build_highlight_batches
from src.tracking.reprice import LadderConfig, recommend, recommend_ladder
from src.ui.common import get_config, get_repo, hint, page_header, section, stat_rail


def _ladder_from_config(cfg, stale_after_hours: float) -> LadderConfig:
    raw = cfg.get("listing_tracker.ladder.steps") or []
    steps = tuple((float(h), float(m)) for h, m in raw) or LadderConfig().steps
    return LadderConfig(
        steps=steps,
        floor_ratio=float(cfg.get("listing_tracker.ladder.floor_ratio") or 0.75),
        stale_after_hours=stale_after_hours,
    )


def _field(row, name):
    """One column, whether the row is a dict or a `sqlite3.Row`, or absent.

    Both shapes reach this page -- the repository hands back `sqlite3.Row`, the
    tests and `stale_rows` hand back dicts -- and `sqlite3.Row` has no `.get`
    while a dict has no tolerance for a missing key.
    """
    if hasattr(row, "get"):
        return row.get(name)
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _as_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _priced_at(row) -> datetime:
    value = row["priced_at"]
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def listed_at(row) -> datetime:
    """When GGG says this ask went up -- the trade site's "listed X ago".

    `indexed_at` is `listing.indexed` straight from the trade API, so it is
    known in full on the *first* poll after the app starts and does not depend
    on having watched the listing for any length of time. That is the point:
    ages used to be inferred from our own scan history, so every map read as
    newly listed after a restart and nothing was ever stale until the app had
    been open for a day.

    Falls back to `priced_at` when the API gave no timestamp -- an imported row,
    or one written before this was carried. `priced_at` is itself taken from
    `indexed` where there was one, so the two agree wherever both exist.
    """
    return _as_datetime(_field(row, "indexed_at")) or _priced_at(row)


def hours_listed(row, now: datetime) -> float:
    """How long the *current* price has been up, by GGG's clock.

    Not "how long the map has been in your tab": GGG re-indexes a listing when
    its note changes, so `indexed` restarts at a markdown, which is exactly the
    question the page asks -- is *this* price working. A map you marked down an
    hour ago is an hour old here however long it sat at its old price.
    """
    return (now - listed_at(row)).total_seconds() / 3600.0


def stale_rows(
    rows,
    now: datetime,
    stale_after_hours: float,
    ladder: LadderConfig | None = None,
    fit=None,
    target_window_hours: float = 24.0,
    target_probability: float = 0.6,
) -> list[dict]:
    """The listings that have sat at least `stale_after_hours`, longest first.

    Each carries a recommended price. With a fit, that price is the dearest one
    your own history shows clearing inside the target window; without one it is
    the fallback ladder, and the recommendation says which it is.
    """
    ladder = ladder or LadderConfig(stale_after_hours=stale_after_hours)
    out: list[dict] = []
    for row in rows:
        hours = hours_listed(row, now)
        if hours < stale_after_hours:
            continue
        entry = dict(row)
        entry["hours_listed"] = hours
        if fit is not None and getattr(fit, "ok", False):
            entry["recommendation"] = recommend(
                price_chaos=row["price_chaos"],
                hours_listed=hours,
                predicted_chaos=row["predicted_chaos"],
                interval_low_chaos=row["interval_low_chaos"],
                fit=fit,
                target_window_hours=target_window_hours,
                target_probability=target_probability,
                cfg=ladder,
            )
        else:
            entry["recommendation"] = recommend_ladder(
                price_chaos=row["price_chaos"],
                hours_listed=hours,
                predicted_chaos=row["predicted_chaos"],
                interval_low_chaos=row["interval_low_chaos"],
                cfg=ladder,
            )
        out.append(entry)
    out.sort(key=lambda entry: entry["hours_listed"], reverse=True)
    return out


def alert_rows(live, gone, now: datetime) -> list[dict]:
    """Live and sold listings, shaped for `alerts_for`.

    The sold ones carry their bracket rather than a time, because that is all we
    have: `last_seen` is the last moment it was there and `gone_first_missing`
    the first moment it was not.
    """
    rows: list[dict] = []
    for row in live:
        entry = dict(row)
        entry["hours_listed"] = hours_listed(row, now)
        entry["sold_lo_hours"] = entry["sold_hi_hours"] = None
        rows.append(entry)
    for row in gone:
        entry = dict(row)
        start = listed_at(row)
        last_seen = row["last_seen"]
        missing = row["gone_first_missing"]
        if isinstance(last_seen, str):
            last_seen = datetime.fromisoformat(last_seen)
        if isinstance(missing, str):
            missing = datetime.fromisoformat(missing)
        entry["sold_lo_hours"] = max((last_seen - start).total_seconds() / 3600.0, 0.0)
        entry["sold_hi_hours"] = max((missing - start).total_seconds() / 3600.0, 0.0)
        entry["hours_listed"] = entry["sold_hi_hours"]
        rows.append(entry)
    return rows


def calibration_note(calibration) -> str:
    """One line that always says whether the factor is in force, and why.

    A personal factor that moves prices without announcing itself is the failure
    mode this whole feature has to avoid, so the refusals read as plainly as the
    measurements.
    """
    if not calibration.applied:
        return f"Personal price calibration: not applied — {calibration.source}."
    return (
        f"Personal price calibration: ×{calibration.factor:.2f}, "
        f"{calibration.source}. Suggested listing prices are scaled by it; fair "
        f"value is not."
    )


def regex_for_stale(stale, all_rows) -> HighlightBatches:
    """A stash search that lights the stale maps and nothing else in the tab.

    `build_highlight_regex`'s contract is that the exclusion corpus is the full
    item text of every map you do *not* want lit, not merely their names -- so a
    fragment that happens to sit inside a line every map prints, like
    "Map (Tier 16)", cannot light the whole tab. The fresh listings are that
    corpus here.
    """
    stale_names = [row["name"] for row in stale if row["name"]]
    stale_ids = {row["id"] for row in stale}
    excluded = [
        "\n".join(
            part
            for part in (row["name"], row["base_type"], "Map (Tier 16)")
            if part
        )
        for row in all_rows
        if row["id"] not in stale_ids
    ]
    return build_highlight_batches(stale_names, excluded)


def overlay_state_for_tab(rows, tab: str, fmt, league: str, grid=(12, 12)):
    """One stash tab's stale maps, projected onto the overlay contract.

    Built straight from `OverlayTile` rather than through `state_from_priced`,
    because that helper takes `PricedMap` objects and the thing being drawn here
    is not a priced tab -- it is a set of listings with a *recommended* price.
    Fabricating PricedMaps to reach the same tiles would be more code and one
    more place for the two prices to diverge.

    The tile shows the recommended price, not the current ask: the point of
    putting it on screen is to see what to change the note to.
    """
    from src.overlay.state import (
        OVERLAY_STATE_VERSION,  # noqa: F401  (imported for the module's contract)
        OverlaySource,
        OverlayState,
        OverlayTile,
        SOURCE_MANUAL,
    )

    tiles = []
    for row in rows:
        recommendation = row.get("recommendation")
        price = recommendation.price_chaos if recommendation else row["price_chaos"]
        tiles.append(
            OverlayTile(
                x=int(row["stash_x"]),
                y=int(row["stash_y"]),
                name=row["name"] or "",
                supported=True,
                price_chaos=price,
                label=fmt.format(price),
            )
        )
    return OverlayState(
        tiles=tuple(tiles),
        grid=tuple(grid),
        league=league,
        tab_names=(tab,),
        unit=fmt.unit,
        generated_at=datetime.now(timezone.utc).isoformat(),
        # "manual", not "stash": the overlay's Analyze button re-reads a stash
        # tab and re-prices it, which is a different thing from these
        # recommendations and would silently replace them with fair values.
        source=OverlaySource(kind=SOURCE_MANUAL, league=league, names=(tab,)),
    )


def overlay_groups(stale) -> dict[str, list[dict]]:
    """Stale listings by stash tab, since the overlay draws one grid at a time.

    Position comes from GGG with the listing (`listing.stash`), so the tiles land
    where the maps actually are -- no stash read, no fingerprint matching, and no
    ambiguity between two similar maps. A listing with no coordinates is left out
    rather than drawn in a guessed cell; the table still lists it.
    """
    groups: dict[str, list[dict]] = {}
    for row in stale:
        tab = row.get("stash_name")
        if not tab or row.get("stash_x") is None or row.get("stash_y") is None:
            continue
        groups.setdefault(tab, []).append(row)
    return groups


def _poller(cfg, account: str, league: str):
    """The poller watching this account, started if it is not already going.

    It gets a `Repository` of its own. `src/database/models.py:connect` opens
    with `check_same_thread=False` on the stated grounds that Streamlit
    serialises reruns so the connection only ever moves between threads -- and a
    background poller is exactly what breaks that. Two connections to the same
    file is what SQLite is for.

    Ownership is the process registry in `src/tracking/poller.py`, not
    `st.session_state`. Session state dies with the browser tab, so a refresh
    used to orphan the running thread -- still polling, no longer cancellable --
    and start a second one beside it, each with a rate limiter that knew nothing
    of the other. The session-state entry is kept only as this session's handle
    for the off switch. The registry decides whether anything is running, and
    restarts a poller whose thread has died rather than handing it back dead.
    """
    from src.config import user_agent
    from src.database.repository import Repository
    from src.market.collectors.official_trade_api import OfficialTradeApiCollector
    from src.market.ingest import converter_for
    from src.market.my_listings import fetch_my_listings
    from src.tracking.poller import ListingPoller, poller_for

    def build():
        own_repo = Repository(cfg.db_path)
        collector = OfficialTradeApiCollector(
            league=league,
            user_agent=user_agent(cfg),
            converter=converter_for(cfg, own_repo),
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
        return ListingPoller(
            repo=own_repo,
            source=lambda name: fetch_my_listings(collector, account=name),
            account=account,
            league=league,
            cfg=_poller_config(cfg),
        )

    poller = poller_for(account, league, build)
    st.session_state["listing_poller"] = poller
    return poller


def _poller_config(cfg):
    from src.tracking.poller import PollerConfig

    return PollerConfig(
        interval_minutes=float(cfg.get("listing_tracker.poll_interval_minutes") or 15),
        young_listing_minutes=float(cfg.get("listing_tracker.young_listing_minutes") or 60),
        young_poll_interval_minutes=float(
            cfg.get("listing_tracker.young_poll_interval_minutes") or 5
        ),
        reappearance_window_hours=float(
            cfg.get("listing_tracker.reappearance_window_hours") or 6
        ),
    )


def _fill_predictions(repo, rows, account: str, league: str) -> None:
    """Give every tracked listing the model's number, once.

    Without this the page has an ask and no estimate to compare it against, and
    the reprice floor -- the bottom of the model's interval -- does not exist,
    so the ladder would have nothing to stop it. Only rows missing a prediction
    are computed, so this costs nothing on a rerun.
    """
    from src.ui.common import get_predictor

    pending = [row for row in rows if row["predicted_chaos"] is None]
    if not pending:
        return
    predictor, _why = get_predictor()
    if predictor is None:
        return

    from src.schema import NormalizedMap

    items, ids = [], []
    for row in pending:
        items.append(
            NormalizedMap(
                name=row["name"] or "",
                base_type=row["base_type"] or "",
                map_tier=row["map_tier"],
                item_level=row["item_level"],
                corrupted=bool(row["corrupted"]),
                is_originator=True,
                supported=True,
                quantity=row["quantity"],
                rarity=row["rarity"],
                pack_size=row["pack_size"],
                more_currency=row["more_currency"],
                more_maps=row["more_maps"],
                more_scarabs=row["more_scarabs"],
                more_divination_cards=row["more_divination_cards"],
            )
        )
        ids.append(row["id"])

    payload = []
    for listing_id, item in zip(ids, items):
        estimate = predictor.estimate(item)
        payload.append(
            {
                "id": listing_id,
                "predicted_chaos": float(estimate.fair_value_chaos),
                "suggested_chaos": float(estimate.fair_value_chaos),
                "interval_low_chaos": float(estimate.interval_low_chaos),
                "interval_high_chaos": float(estimate.interval_high_chaos),
                "model_version": estimate.model_version or predictor.version,
            }
        )
    repo.save_tracked_predictions(payload)



def _tracker_toggle(cfg, account: str, league: str) -> None:
    """Start and stop the poller from the page it reports on.

    It used to be `listing_tracker.enabled` in config.yaml and nothing else,
    which meant the one control for the feature lived in a file the app never
    asks you to open, and turning it on took a restart to notice.

    Off is off, not merely un-flagged: the poller owns a thread and a database
    connection, so the switch cancels the running one rather than leaving it
    polling behind a false flag. Turning it back on starts a fresh one.

    The choice is written to config.yaml because that is where the rest of the
    tracker's settings already live, and one source of truth beats a flag here
    and a value there.
    """
    enabled = bool(cfg.get("listing_tracker.enabled"))
    chosen = st.toggle(
        "Check my listings while the app is open",
        value=enabled,
        key="listing_tracker_enabled",
        help=(
            "Polls the trade search for your own account -- about twenty "
            "requests an hour with forty maps listed, and more often during a "
            "listing's first hour, which is where the fast-sale signal is. "
            "It stops when you close the app."
        ),
    )

    if chosen != enabled:
        cfg.set("listing_tracker.enabled", chosen)
        cfg.save()
        if not chosen:
            poller = st.session_state.pop("listing_poller", None)
            if poller is not None:
                poller.cancel()
            # And out of the process registry, or the next rerun finds it there
            # and starts it again -- the switch would look stuck on.
            from src.tracking.poller import stop_poller

            stop_poller(account, league)
        st.rerun()

    if chosen:
        poller = _poller(cfg, account, league)
        st.caption(
            f"Watching **{account}** in {league}. Young listings every "
            f"{cfg.get('listing_tracker.young_poll_interval_minutes') or 5} min, "
            f"the rest every {cfg.get('listing_tracker.poll_interval_minutes') or 15} min."
        )
        _watch_status(poller)
    else:
        st.caption(
            "Not checking. Nothing new is recorded while this is off, so the "
            "gap becomes part of the bracket on anything that sells meanwhile. "
            "Whatever was recorded before is still shown below."
        )


#: How often the watcher's status line redraws itself. Streamlit runs the script
#: only when a widget is touched, so without this the page you left open all
#: evening still says "last checked: never" however many passes the poller made
#: behind it -- which is indistinguishable from a switch that does nothing, and
#: was read as exactly that.
#:
#: Twenty seconds against the poller's five-minute floor: a fragment redraw is a
#: caption and a button, and the point is only that the page stops lying about
#: when it last looked.
WATCH_REFRESH_SECONDS = 20


@st.fragment(run_every=WATCH_REFRESH_SECONDS)
def _watch_status(poller) -> None:
    """What the watcher is actually doing, kept live and never silent.

    Three things the page had no way to say before, all of which looked
    identical from the outside -- "nothing has happened yet":

    * a pass has landed since you opened the page (the fragment redraws);
    * the last pass failed (the reason, rather than a stale success);
    * the thread has stopped (with the switch still saying it was watching).

    A fragment rather than the whole page: redrawing `render()` every twenty
    seconds would re-run the model over every untracked listing and rebuild the
    highlight regex for a caption.
    """
    state = poller.state
    if state.last_run_at is None:
        st.caption("First check is running now.")
    else:
        stamp = state.last_run_at.astimezone().strftime("%H:%M:%S")
        st.caption(
            f"Last checked {stamp} — {state.polls} pass(es) this session, "
            f"{state.listings_live} listing(s) live."
        )

    if state.last_error:
        hint(
            f"The last check did not finish: {state.last_error}. Nothing was "
            "marked sold from it — a search that never returned is not evidence "
            "that your tab emptied."
        )
    if not poller.running:
        hint(
            # `hint` emits raw HTML, so Markdown never runs inside it -- tags,
            # not asterisks, or the user reads the literal `**Check now**`.
            "The watcher has stopped. Nothing is being recorded. Press "
            "<b>Check now</b> to run a pass and restart it."
        )

    if st.button("Check now", key="listing_poll_now"):
        _poll_now(poller)

    outcome = st.session_state.get("listing_poll_result")
    if outcome:
        getattr(st, outcome[0])(outcome[1])


def _poll_now(poller) -> None:
    """Cut the cooldown short and wait for the pass, so the button can answer.

    The pass runs on the poller's thread, never here. The poller's `Repository`
    belongs to that thread, and running a pass through it from the script thread
    would put two threads on one SQLite connection -- which
    `check_same_thread=False` permits and does not make safe. So this pokes and
    waits, which is also what makes the button work on a poller that has just
    been restarted from the dead.

    Waiting rather than firing and forgetting because the button exists to
    answer "is this thing working", and a button that returns before there is an
    answer is the thing being complained about.
    """
    before = poller.passes
    poller.start()  # a no-op while it is alive; revives it when it is not
    poller.poke()
    with st.spinner("Checking your listings…"):
        landed = poller.wait_for_pass(before)

    # Through session state rather than straight onto the screen, because the
    # rerun below throws this render away -- a button that reports its result
    # and then wipes it is the same silence it was meant to fix.
    if not landed:
        st.session_state["listing_poll_result"] = (
            "warning",
            "The check is still running — a rate limit will make a pass wait. "
            "The line above updates by itself when it lands.",
        )
    elif poller.state.last_error:
        st.session_state["listing_poll_result"] = ("error", poller.state.last_error)
    else:
        st.session_state["listing_poll_result"] = (
            "success",
            f"Checked. {poller.state.listings_live} listing(s) live"
            + ("" if poller.state.last_complete else ", partial pass")
            + ".",
        )
    if landed:
        # The whole app, not this fragment: the pass has just changed the table,
        # the stat rail and the highlight regex, all of which live outside it.
        st.rerun(scope="app")
    else:
        st.rerun(scope="fragment")


# ----------------------------------------------------------------------
def render() -> None:
    cfg = get_config()
    repo = get_repo()
    # The account name is not a config key -- it is a credential, and Settings
    # persists it to the environment / .env alongside POESESSID. Read it the way
    # src/stash/official_api.py does rather than inventing a second home for it.
    account = os.getenv("POE_ACCOUNT_NAME", "").strip()
    league = cfg.get("league")

    page_header(
        "Price",
        "My Listings",
        "What you have listed for instant buyout, how long each has sat, and "
        "what the price should be if it is not moving.",
    )

    if not account:
        hint(
            "Set your account name in Settings first. The tracker reads your own "
            "listings from the trade search by seller account, so without a name "
            "there is nothing to look for."
        )
        return

    _tracker_toggle(cfg, account, league)

    run = repo.last_poll_run(account, league)
    live = repo.tracked_rows_for_page(account, league)
    _fill_predictions(repo, live, account, league)
    live = repo.tracked_rows_for_page(account, league)

    stat_rail(
        [
            ("Listed", len(live)),
            ("Last checked", run["started_at"][11:16] if run else "never"),
            ("Last pass", "complete" if run and run["complete"] else "partial"),
        ]
    )

    if run and run["error"]:
        hint(
            f"The last check did not finish: {run['error']}. Nothing was marked "
            "sold from it -- a search that never returned is not evidence that "
            "your tab emptied."
        )
    if run and not run["complete"] and not run["error"]:
        hint(
            f"The last check saw {run['listings_seen']} of {run['total_reported']} "
            "listings. A trade search returns at most 100 results, so "
            "disappearances were not acted on this pass."
        )

    # --- what your own history says --------------------------------------
    from datetime import timedelta

    from src.tracking.alerts import alerts_for, observed_thresholds
    from src.tracking.calibration import CalibrationConfig
    from src.tracking.history import calibration_for_account, observations_for_account
    from src.tracking.survival import fit_hazard
    from src.ui.common import get_predictor

    now = datetime.now(timezone.utc)
    # One path from rows to observations, shared with the pricing seam in
    # src/ui/common.py. If this page assembled its own, it could report a factor
    # of 1.05 while prices moved by something else, and nothing on screen would
    # reveal the disagreement.
    observations = observations_for_account(repo, account, league, now)
    fit = fit_hazard(observations)
    thresholds = observed_thresholds(
        observations,
        fallback_fast=float(cfg.get("listing_tracker.fast_sale_hours") or 1),
        fallback_slow=float(cfg.get("listing_tracker.stale_after_hours") or 24),
    )
    predictor, _why = get_predictor()
    calibration = calibration_for_account(
        repo,
        account,
        league,
        predictor.version if predictor else "",
        CalibrationConfig(
            min_sold=int(cfg.get("listing_tracker.calibration.min_sold") or 20),
            clamp=tuple(cfg.get("listing_tracker.calibration.clamp") or (0.8, 1.3)),
        ),
        now,
    )

    gone_page_rows = repo.tracked_rows_for_page(account, league, status="gone")
    alerts = alerts_for(alert_rows(live, gone_page_rows, now), thresholds)
    if alerts:
        section("Mispriced", "Only where the evidence is decisive.")
        for alert in alerts:
            st.markdown(f"**{alert.headline}** — {alert.evidence}")

    with st.expander("What your maps actually clear at"):
        st.caption(fit.reason)
        st.caption(
            f"Fast is under {thresholds.fast_hours:.1f} h and slow is over "
            f"{thresholds.slow_hours:.0f} h ({thresholds.source})."
        )
        st.caption(calibration_note(calibration))
        st.caption(
            "A sale seen only across a polling gap is kept as the interval it "
            "might have happened in, so it counts for what it is worth without "
            "pretending to a precision it does not have."
        )

    default_stale = float(cfg.get("listing_tracker.stale_after_hours") or 24)
    section("Sitting", "Maps that have not sold at their current price.")
    st.caption(
        "Ages come from the listing's own timestamp in the trade API — the "
        "same \"listed X ago\" the trade site prints — not from how long this "
        "app has been watching. A map listed three days ago reads as three days "
        "old on the first check after you open the app."
    )
    hours = st.select_slider(
        "Listed for at least",
        options=[6, 12, 24, 48, 72, 168],
        value=int(default_stale) if int(default_stale) in (6, 12, 24, 48, 72, 168) else 24,
        format_func=lambda h: f"{h} h" if h < 48 else f"{h // 24} days",
    )

    stale = stale_rows(
        live,
        now,
        float(hours),
        _ladder_from_config(cfg, float(hours)),
        fit=fit,
        target_window_hours=float(cfg.get("listing_tracker.target_window_hours") or 24),
        target_probability=float(
            cfg.get("listing_tracker.target_sell_probability") or 0.6
        ),
    )

    if not stale:
        hint(f"Nothing has been sitting for {hours} hours. ")
        return

    st.dataframe(
        [
            {
                "Map": row["name"],
                "Tab": row["stash_name"] or "-",
                "Asking": row["price_chaos"],
                "Model": row["predicted_chaos"],
                "Listed": f"{row['hours_listed']:.0f} h",
                "Suggested": (
                    row["recommendation"].price_chaos if row["recommendation"] else None
                ),
                "Why": row["recommendation"].reason if row["recommendation"] else "",
            }
            for row in stale
        ],
        width="stretch",
        hide_index=True,
    )

    section("Find them in game", "A stash search that lights exactly these maps.")
    batches = regex_for_stale(stale, live)
    if not batches.ok:
        hint(
            "No pattern covers these maps without also lighting the ones that are "
            "selling fine. " + batches.explain()
        )
    else:
        for index, page in enumerate(batches.pages, start=1):
            st.code(page.pattern, language="text")
            st.caption(
                f"Search {index} of {batches.count} — lights {len(page.covered)} map(s)"
            )

    groups = overlay_groups(stale)
    if groups:
        section("Draw on the tab", "Positions come from the listing itself.")
        st.caption(
            "The overlay draws one grid, so a tab is exported at a time. Tiles "
            "show the recommended price, not what you are asking now."
        )
        tab = st.selectbox(
            "Tab",
            sorted(groups),
            format_func=lambda name: f"{name} ({len(groups[name])} maps)",
        )
        if st.button("Send to overlay", type="primary"):
            from src.overlay.state import write_state
            from src.ui.common import get_formatter

            state = overlay_state_for_tab(
                groups[tab],
                tab,
                get_formatter(cfg),
                league,
                grid=tuple(cfg.get("overlay.grid") or (12, 12)),
            )
            path = write_state(state)
            st.success(f"Written to {path}. {state.summary()}")

    placed = {row["id"] for rows in groups.values() for row in rows}
    unplaced = [row for row in stale if row["id"] not in placed]
    if unplaced:
        st.caption(
            f"{len(unplaced)} of these have no stash position in their listing, so "
            "they are listed but not drawn. A guessed cell would put a price on "
            "the wrong map."
        )

    section("Leave one out", "A map you pulled rather than sold.")
    hint(
        "A listing that disappears is recorded as sold, because the trade API "
        "cannot tell a sale from a delist. Tick a map you know you removed and "
        "it stays off the learning."
    )
    for row in stale:
        if st.checkbox(
            f"{row['name']} — not a sale",
            value=bool(row["exclude_from_learning"]),
            key=f"exclude-{row['id']}",
        ) != bool(row["exclude_from_learning"]):
            repo.set_exclude_from_learning(row["id"], not row["exclude_from_learning"])
            st.rerun()
