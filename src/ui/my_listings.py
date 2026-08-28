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

from datetime import datetime, timezone

import streamlit as st

from src.stash.highlight_regex import HighlightBatches, build_highlight_batches
from src.tracking.reprice import LadderConfig, recommend_ladder
from src.ui.common import get_config, get_repo, hint, page_header, section, stat_rail


def _ladder_from_config(cfg, stale_after_hours: float) -> LadderConfig:
    raw = cfg.get("listing_tracker.ladder.steps") or []
    steps = tuple((float(h), float(m)) for h, m in raw) or LadderConfig().steps
    return LadderConfig(
        steps=steps,
        floor_ratio=float(cfg.get("listing_tracker.ladder.floor_ratio") or 0.75),
        stale_after_hours=stale_after_hours,
    )


def stale_rows(
    rows, now: datetime, stale_after_hours: float, ladder: LadderConfig | None = None
) -> list[dict]:
    """The listings that have sat at least `stale_after_hours`, longest first.

    Time runs from `priced_at`, not from when the map was first listed, so a map
    you marked down an hour ago is an hour old here however long it sat at its
    old price. That is the point: the question this page asks is whether the
    *current* price is working.
    """
    ladder = ladder or LadderConfig(stale_after_hours=stale_after_hours)
    out: list[dict] = []
    for row in rows:
        priced_at = row["priced_at"]
        if isinstance(priced_at, str):
            priced_at = datetime.fromisoformat(priced_at)
        if priced_at.tzinfo is None:
            priced_at = priced_at.replace(tzinfo=timezone.utc)
        hours = (now - priced_at).total_seconds() / 3600.0
        if hours < stale_after_hours:
            continue
        entry = dict(row)
        entry["hours_listed"] = hours
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


# ----------------------------------------------------------------------
def render() -> None:
    cfg = get_config()
    repo = get_repo()
    account = (cfg.get("account_name") or "").strip()
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

    run = repo.last_poll_run(account, league)
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

    default_stale = float(cfg.get("listing_tracker.stale_after_hours") or 24)
    section("Sitting", "Maps that have not sold at their current price.")
    hours = st.select_slider(
        "Listed for at least",
        options=[6, 12, 24, 48, 72, 168],
        value=int(default_stale) if int(default_stale) in (6, 12, 24, 48, 72, 168) else 24,
        format_func=lambda h: f"{h} h" if h < 48 else f"{h // 24} days",
    )

    now = datetime.now(timezone.utc)
    stale = stale_rows(live, now, float(hours), _ladder_from_config(cfg, float(hours)))

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
            "The overlay draws one grid, so export a tab at a time: "
            + ", ".join(f"{tab} ({len(rows)})" for tab, rows in sorted(groups.items()))
        )
