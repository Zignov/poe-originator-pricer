"""Command-line interface, so nothing depends on Streamlit being available.

    python -m src.cli collect-market --limit 2000
    python -m src.cli update-rate
    python -m src.cli train
    python -m src.cli price-item item.txt
    python -m src.cli price-tab --tab "Originator Maps"
    python -m src.cli price-tab-market --tab "Originator Maps" --budget 150
    python -m src.cli inspect-stash
    python -m src.cli import-market listings.json
    python -m src.cli overlay --calibrate
    python -m src.cli overlay
    python -m src.cli status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from src.config import load_config, user_agent
from src.database.repository import Repository
from src.market.comparables import format_age
from src.market.ingest import collect_from_trade_api, converter_for, store
from src.ml.predict import ModelNotTrained, ModelSchemaMismatch, Predictor
from src.ml.decision_bands import verdict
from src.overlay.state import DEFAULT_STATE_PATH, state_from_priced, write_state
from src.pricing.display import PriceFormatter
from src.pricing.fair_value import PricedMap, PricingService
from src.pricing.sell_strategy import STRATEGIES


def _service(cfg, repo, require_model: bool = True) -> PricingService:
    predictor = None
    try:
        predictor = Predictor.load(cfg)
    except (ModelNotTrained, ModelSchemaMismatch) as exc:
        if require_model:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(2)
        print(f"warning: {exc}", file=sys.stderr)
    return PricingService.build(cfg, repo, predictor, converter_for(cfg, repo))


def _print_priced(
    priced: Sequence[PricedMap], formatter: PriceFormatter, show_comparables: bool = False
) -> None:
    header = (
        f"{'Pos':<5}{'Cur':>5}{'Maps':>6}{'Scar':>6}{'Div':>5}{'Pack':>6}"
        f"{'Qty':>6}{'Rar':>6}{'Fair':>10}{'Sugg':>10}{'Range':>18}  {'Conf':<7}Action"
    )
    print(header)
    print("-" * len(header))
    for entry in priced:
        item = entry.item
        stats = (
            f"{item.position_label():<5}{item.more_currency:>5}{item.more_maps:>6}"
            f"{item.more_scarabs:>6}{item.more_divination_cards:>5}{item.pack_size:>6}"
            f"{item.quantity:>6}{item.rarity:>6}"
        )
        if not entry.supported:
            # "UNSUPPORTED" is wider than the Fair column, so it is spaced
            # rather than right-aligned into it. A map the scope allows but the
            # market mode could not price carries its reason in `note` instead --
            # "no live listings matched" is not the same statement as "this map
            # is out of scope", and printing an empty reason hid the difference.
            reason = item.unsupported_reason or entry.note
            print(f"{stats}  {'UNSUPPORTED' if item.unsupported_reason else 'NO PRICE   '}  {reason}")
            continue
        est = entry.estimate
        print(
            f"{stats}"
            f"{formatter.format(est.fair_value_chaos):>10}"
            f"{formatter.format(entry.suggested_chaos):>10}"
            f"{formatter.range(est.interval_low_chaos, est.interval_high_chaos):>18}"
            f"  {est.confidence:<7}{verdict(est.fair_value_chaos)}"
        )
        if show_comparables and not entry.comparables.empty:
            for _, row in entry.comparables.head(5).iterrows():
                print(
                    f"      cmp  cur {int(row['more_currency']):>3} maps {int(row['more_maps']):>3} "
                    f"scar {int(row['more_scarabs']):>3} pack {int(row['pack_size']):>3} "
                    f"qty {int(row['quantity']):>3}  ->  {formatter.format(row['price_chaos']):>8} "
                    f"({format_age(row['age_hours'])} old)"
                )


# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    cfg = load_config(args.config)
    with Repository(cfg.db_path) as repo:
        counts = repo.count_listings(cfg.get("league"))
        rate = repo.get_divine_rate(cfg.get("league"))
        run = repo.active_model_run()
        print(f"league:            {cfg.get('league')}")
        print(f"database:          {cfg.db_path}")
        print(f"listings:          {counts['total']} ({counts['outliers']} flagged as outliers)")
        print(f"collected last 24h:{counts['last_24h']:>4}")
        print(f"latest capture:    {repo.latest_capture()}")
        if rate:
            print(f"divine -> chaos:   {rate[0]:.1f}  (as of {rate[1]})")
        else:
            print(f"divine -> chaos:   {cfg.get('currency.divine_to_chaos')} (from config)")
        print(f"display currency:  {cfg.get('display.currency', 'chaos')}")
        if run:
            metrics = json.loads(run["metrics"]).get("test", {})
            print(f"active model:      {run['model_version']} ({run['backend']})")
            print(f"  trained:         {run['trained_at']} on {run['dataset_size']} listings")
            print(f"  test MAE:        {metrics.get('mae', float('nan')):.0f} chaos")
            print(f"  test median AE:  {metrics.get('median_ae', float('nan')):.0f} chaos")
            print(f"  test R2:         {metrics.get('r2', float('nan')):.3f}")
        else:
            print("active model:      none -- run `python -m src.cli train`")
    return 0


def cmd_update_rate(args) -> int:
    from src.market.currency_source import ExchangeRateError, fetch_divine_to_chaos

    cfg = load_config(args.config)
    with Repository(cfg.db_path) as repo:
        if args.rate is not None:
            repo.set_divine_rate(cfg.get("league"), args.rate, source="manual")
            repo.recompute_price_chaos(args.rate)
            print(f"divine -> chaos set to {args.rate} (manual) and prices recomputed")
            return 0
        try:
            rate = fetch_divine_to_chaos(cfg.get("league"), user_agent(cfg))
        except ExchangeRateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        repo.set_divine_rate(cfg.get("league"), rate, source="official_exchange_api")
        repo.recompute_price_chaos(rate)
        print(f"divine -> chaos = {rate:.1f} (official exchange endpoint); prices recomputed")
    return 0


def cmd_collect_market(args) -> int:
    cfg = load_config(args.config)
    with Repository(cfg.db_path) as repo:
        stats = collect_from_trade_api(
            cfg, repo, limit=args.limit, progress=lambda m: print(m, flush=True)
        )
        print(stats)
        if stats.get("error"):
            return 1
    return 0


def cmd_import_market(args) -> int:
    from src.market.collectors.manual_import import (
        JsonExportCollector,
        ManualCsvCollector,
        csv_template,
    )

    cfg = load_config(args.config)
    if args.template:
        print(csv_template(), end="")
        return 0

    path = Path(args.path)
    with Repository(cfg.db_path) as repo:
        converter = converter_for(cfg, repo)
        if path.suffix.lower() == ".csv":
            collector = ManualCsvCollector(converter, cfg.get("league"), cfg.get("scope.map_tier"))
            listings = list(collector.collect(path=path))
        else:
            collector = JsonExportCollector(
                converter,
                cfg.get("league"),
                cfg.get("scope.map_tier"),
                cfg.get("scope.require_uncorrupted"),
            )
            listings = list(collector.collect(path=path))
        print(store(cfg, repo, listings))
    return 0


def cmd_train(args) -> int:
    from src.ml.train import NotEnoughData, train

    cfg = load_config(args.config)
    try:
        train(
            cfg,
            optional_features=args.optional_features,
            tune_weights=args.tune_weights,
            min_rows=args.min_rows,
        )
    except NotEnoughData as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_price_item(args) -> int:
    from src.parsing.item_parser import classify, parse_item_text

    cfg = load_config(args.config)
    text = Path(args.path).read_text(encoding="utf-8") if args.path != "-" else sys.stdin.read()
    item = classify(
        parse_item_text(text),
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    with Repository(cfg.db_path) as repo:
        service = _service(cfg, repo)
        priced = service.price(item, strategy=args.strategy)
        _print_priced([priced], service.formatter, show_comparables=True)
        if priced.supported:
            print()
            print(f"note to paste: {priced.note}")
            for reason in priced.estimate.confidence_reasons:
                print(f"  - {reason}")
    return 0


def cmd_inspect_stash(args) -> int:
    cfg = load_config(args.config)
    source = _stash_source(cfg, args)
    ok, why = source.available()
    if not ok:
        print(f"stash source unavailable: {why}", file=sys.stderr)
        print(source.setup_hint, file=sys.stderr)
        return 2
    tabs = source.list_tabs(cfg.get("league"))
    print(f"{len(tabs)} tabs in {cfg.get('league')}:")
    for tab in tabs:
        print(f"  [{tab.index:>3}] {tab.name}  (id={tab.id}, type={tab.type or '-'})")
    return 0


def cmd_price_tab(args) -> int:
    cfg = load_config(args.config)
    source = _stash_source(cfg, args)
    ok, why = source.available()
    if not ok:
        print(f"stash source unavailable: {why}", file=sys.stderr)
        print(source.setup_hint, file=sys.stderr)
        return 2

    tabs = source.list_tabs(cfg.get("league"))
    wanted = [t for t in tabs if args.tab is None or t.name == args.tab]
    if not wanted:
        print(f"no tab named {args.tab!r}. Available: {[t.name for t in tabs]}", file=sys.stderr)
        return 2

    items = source.fetch_tabs(cfg.get("league"), wanted)
    with Repository(cfg.db_path) as repo:
        service = _service(cfg, repo)
        priced = service.price_many(items, strategy=args.strategy)
        priced.sort(
            key=lambda p: (p.estimate.fair_value_chaos if p.estimate else -1), reverse=True
        )
        supported = [p for p in priced if p.supported]
        print(
            f"{len(items)} items in {[t.name for t in wanted]}: "
            f"{len(supported)} priceable, {len(items) - len(supported)} unsupported"
        )
        _print_priced(priced, service.formatter, show_comparables=args.comparables)

        if args.save_snapshot:
            snapshot_id = repo.save_snapshot(
                cfg.get("league"), items, source=source.name
            )
            repo.save_predictions(
                snapshot_id,
                [
                    {
                        "item_fingerprint": p.item.fingerprint(),
                        "features": p.item.features(),
                        "predicted_chaos": p.estimate.fair_value_chaos,
                        "suggested_chaos": p.suggested_chaos,
                        "interval_low_chaos": p.estimate.interval_low_chaos,
                        "interval_high_chaos": p.estimate.interval_high_chaos,
                        "confidence": p.estimate.confidence,
                        "model_version": p.estimate.model_version,
                    }
                    for p in supported
                ],
            )
            print(f"\nsaved snapshot {snapshot_id}")

        if args.overlay:
            # Positions come from the stash payload, so this is only meaningful
            # for a real tab -- the clipboard source numbers maps in paste
            # order, and those entries carry no coordinates to draw at.
            path = write_state(
                state_from_priced(
                    priced,
                    service.formatter,
                    strategy=args.strategy,
                    league=cfg.get("league"),
                    tab_names=[t.name for t in wanted],
                )
            )
            print(f"\noverlay state written to {path}")
            print("run `python -m src.cli overlay` to draw it over the game")

        if args.copy_all:
            print("\ncopy-all block:")
            for entry in supported:
                print(
                    f"{entry.item.position_label()} -> "
                    f"{service.formatter.format(entry.suggested_chaos)}"
                )
    return 0


def cmd_overlay(args) -> int:
    """Open the desktop overlay, or the calibration screen.

    Deliberately a separate process from the Streamlit app: tkinter wants the
    main thread and a blocking mainloop, and Streamlit re-runs its script on
    every widget touch. The two meet at data/overlay_state.json instead -- see
    src/overlay/state.py for the contract.
    """
    from src.overlay.calibration import Calibration, resolution_warning
    from src.overlay.window import OverlayUnavailable, run_overlay

    cfg = load_config(args.config)
    calibration = Calibration.from_config(cfg)
    if not args.calibrate:
        # The screen size is not known until Tk is up, so this only catches the
        # never-calibrated case here; a resolution change is reported in the
        # overlay's own status strip.
        warning = resolution_warning(calibration, None)
        if warning:
            print(f"note: {warning}", file=sys.stderr)
    try:
        return run_overlay(cfg, calibrate=args.calibrate, state_path=args.state)
    except OverlayUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_price_tab_market(args) -> int:
    """Price a tab from live listings instead of from the model.

    Headless twin of the Stash Pricer's Market comparison mode. It is slow by
    construction -- GGG's search policy allows thirty searches per five minutes
    sustained -- so it prints progress as it goes, stops cleanly at the budget,
    and can be re-run to continue: the bucket cache on disk means a second run
    pays nothing for the maps the first one already answered.
    """
    from src.market.live_comparables import format_duration
    from src.pricing.market_comparison import MarketComparisonPricer, compare_modes

    cfg = load_config(args.config)
    source = _stash_source(cfg, args)
    ok, why = source.available()
    if not ok:
        print(f"stash source unavailable: {why}", file=sys.stderr)
        print(source.setup_hint, file=sys.stderr)
        return 2

    tabs = source.list_tabs(cfg.get("league"))
    wanted = [t for t in tabs if args.tab is None or t.name == args.tab]
    if not wanted:
        print(f"no tab named {args.tab!r}. Available: {[t.name for t in tabs]}", file=sys.stderr)
        return 2

    items = source.fetch_tabs(cfg.get("league"), wanted)
    with Repository(cfg.db_path) as repo:
        # The model is optional here: this mode does not need it, but when one is
        # loaded both numbers can be printed side by side, which is the most
        # useful thing about running the slow mode at all.
        service = _service(cfg, repo, require_model=False)
        pricer = MarketComparisonPricer.build(
            cfg,
            repo,
            service,
            budget=args.budget,
            tolerance_schedule=args.tolerance,
        )
        supported = [i for i in items if i.supported]
        plan = pricer.plan(len(supported))
        print(
            f"{len(items)} items, {len(supported)} priceable. Budget "
            f"{pricer.budget.limit} requests; {plan.caption()}."
        )
        state = pricer.run(items, progress=lambda p: print(p.caption(), flush=True))
        priced = pricer.priced(items, state, strategy=args.strategy)
        priced.sort(key=lambda p: (p.estimate.fair_value_chaos if p.estimate else -1), reverse=True)
        _print_priced(priced, service.formatter, show_comparables=args.comparables)

        print(
            f"\n{state.priced_count} priced from live listings  |  "
            f"{state.requests_used} requests used  |  {state.cache_hits} bucket cache hits"
        )
        if state.stopped_reason:
            print(state.stopped_reason)

        if service.predictor is not None:
            model_priced = service.price_many(items, strategy=args.strategy)
            rows = [r for r in compare_modes(model_priced, priced) if r.gap_ratio]
            if rows:
                print("\nWhere the two modes disagree most:")
                for row in rows[:10]:
                    print(
                        f"  {row.item.position_label():<5}"
                        f"model {service.formatter.format(row.model_chaos):>10}   "
                        f"market {service.formatter.format(row.market_chaos):>10}   "
                        f"({row.gap_ratio:.2f}x)"
                    )
    return 0


def _stash_source(cfg, args):
    from src.stash.clipboard_import import ClipboardStashSource
    from src.stash.json_import import JsonStashSource
    from src.stash.official_api import SessionStashSource

    tier = cfg.get("scope.map_tier")
    uncorrupted = cfg.get("scope.require_uncorrupted")
    if getattr(args, "json", None):
        return JsonStashSource(path=args.json, map_tier=tier, require_uncorrupted=uncorrupted)
    if getattr(args, "item_text", None):
        text = Path(args.item_text).read_text(encoding="utf-8")
        return ClipboardStashSource(text, map_tier=tier, require_uncorrupted=uncorrupted)
    return SessionStashSource(
        user_agent=user_agent(cfg),
        realm=cfg.get("realm", "pc"),
        map_tier=tier,
        require_uncorrupted=uncorrupted,
    )


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__)
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="dataset, rate and model summary").set_defaults(func=cmd_status)

    rate = sub.add_parser("update-rate", help="refresh the divine/chaos rate")
    rate.add_argument("--rate", type=float, default=None, help="set manually instead of fetching")
    rate.set_defaults(func=cmd_update_rate)

    collect = sub.add_parser("collect-market", help="collect listings from the official trade API")
    collect.add_argument("--limit", type=int, default=500)
    collect.set_defaults(func=cmd_collect_market)

    imp = sub.add_parser("import-market", help="import listings from CSV or a saved JSON export")
    imp.add_argument("path", nargs="?", default="")
    imp.add_argument("--template", action="store_true", help="print a CSV template and exit")
    imp.set_defaults(func=cmd_import_market)

    train = sub.add_parser("train", help="train the price model")
    train.add_argument("--optional-features", nargs="*", default=[])
    train.add_argument("--tune-weights", action="store_true")
    train.add_argument("--min-rows", type=int, default=60)
    train.set_defaults(func=cmd_train)

    price = sub.add_parser("price-item", help="price one copied item ('-' reads stdin)")
    price.add_argument("path")
    price.add_argument("--strategy", choices=STRATEGIES, default="market")
    price.set_defaults(func=cmd_price_item)

    inspect = sub.add_parser("inspect-stash", help="list stash tabs")
    _add_stash_args(inspect)
    inspect.set_defaults(func=cmd_inspect_stash)

    tab = sub.add_parser("price-tab", help="price every Originator Map in a tab")
    _add_stash_args(tab)
    tab.add_argument("--tab", default=None, help="tab name; omit to use every tab")
    tab.add_argument("--strategy", choices=STRATEGIES, default="market")
    tab.add_argument("--comparables", action="store_true", help="also print closest listings")
    tab.add_argument("--copy-all", action="store_true", help="print a copy-all price block")
    tab.add_argument("--save-snapshot", action="store_true")
    tab.add_argument(
        "--overlay",
        action="store_true",
        help="also write data/overlay_state.json for the desktop overlay",
    )
    tab.set_defaults(func=cmd_price_tab)

    overlay = sub.add_parser("overlay", help="draw the priced tab over the game window")
    overlay.add_argument(
        "--calibrate", action="store_true", help="line the grid up with the stash tab"
    )
    overlay.add_argument(
        "--state", default=None, help=f"state file to watch (default {DEFAULT_STATE_PATH})"
    )
    overlay.set_defaults(func=cmd_overlay)

    market = sub.add_parser(
        "price-tab-market",
        help="price a tab from live trade listings instead of the model (slow, API-heavy)",
    )
    _add_stash_args(market)
    market.add_argument("--tab", default=None, help="tab name; omit to use every tab")
    market.add_argument("--strategy", choices=STRATEGIES, default="market")
    market.add_argument(
        "--budget",
        type=int,
        default=None,
        help="ceiling on total API requests for this run (default: config)",
    )
    market.add_argument(
        "--tolerance",
        type=float,
        nargs="*",
        default=None,
        metavar="MULT",
        help="tolerance multipliers to try in order, e.g. --tolerance 1 2 4",
    )
    market.add_argument("--comparables", action="store_true", help="print the live comparables")
    market.set_defaults(func=cmd_price_tab_market)

    return parser


def _add_stash_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", default=None, help="read a stash JSON export instead of the API")
    parser.add_argument("--item-text", default=None, help="read a file of copied item text")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
