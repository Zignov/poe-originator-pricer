"""Collect market data continuously until stopped.

    python scripts/collect_loop.py                 # default cadence
    python scripts/collect_loop.py --limit 1500 --rest 20
    python scripts/collect_loop.py --retrain-at 2000

Why a loop rather than one big `--limit`: the collector stratifies its search
into a fixed set of slices and stops when they are exhausted. Asking for 2500
listings in one run returned 1067, not because of a budget but because that was
all the *new* listings the slices could see. More data therefore comes from
asking again later, once the trade site has indexed more, rather than from
asking for more at once.

Everything about rate limiting is the collector's own: it reads GGG's published
policy out of the response headers and sleeps accordingly, and the rest between
cycles here is on top of that, not instead of it.

The loop stops on Ctrl-C, on `--cycles`, or when the stop file appears:

    data/collect_loop.stop

It writes one line per cycle to stdout and keeps a JSON summary beside the
database, so a run left going for hours can be read back without scrolling a
terminal.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from src.config import load_config, user_agent
from src.database.repository import Repository
from src.market.cancel import CancelToken
from src.market.ingest import collect_from_trade_api, restate_prices

STOP_FILE = "collect_loop.stop"
LOG_FILE = "collect_loop.json"

#: Between cycles. Long enough that the trade site has indexed new listings
#: worth coming back for, and short enough to make real progress overnight.
DEFAULT_REST_MINUTES = 20.0

#: How often to re-fetch the divine rate. It multiplies into every stored chaos
#: price, and a long run would otherwise normalise an evening's collection
#: against a morning's rate.
RATE_EVERY_CYCLES = 6


#: The smallest number of new listings worth refitting a model for. Below this
#: the fit moves by less than its own noise, and training is a minute of CPU.
MIN_GROWTH_TO_RETRAIN = 250


def should_retrain(total: int, retrain_at: int, retrained_at: int) -> bool:
    """Has the dataset grown enough since the last retrain to justify another?

    Two conditions, and the second is the one that is easy to leave out: a
    plain `total > retrained_at` is true on any cycle that added a single
    listing, so once the threshold was passed the loop would refit the model
    every twenty minutes for twelve new rows.
    """
    if not retrain_at or total < retrain_at:
        return False
    # A tenth of the threshold, floored, so a big target does not retrain on
    # what is proportionally a rounding error.
    step = max(MIN_GROWTH_TO_RETRAIN, retrain_at // 10)
    return total >= retrained_at + step


def _watch_stop_file(stop_path, token, interval: float = 2.0) -> None:
    """Set `token` as soon as the stop file appears.

    The collection itself spends most of its time inside the collector, so
    polling the file from the main loop only works between cycles. This watches
    it on its own thread and cancels the token the moment it shows up, which
    the collector notices inside its next request or sleep.
    """
    while not token.is_set():
        if stop_path.exists():
            token.cancel()
            return
        time.sleep(interval)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _say(message: str) -> None:
    print(f"[{_now()}] {message}", flush=True)


def refresh_rate(cfg, repo) -> None:
    from src.market.currency_source import ExchangeRateError, fetch_rate_quote

    try:
        quote = fetch_rate_quote(cfg.get("league"), user_agent(cfg), "divine")
    except ExchangeRateError as exc:
        _say(f"rate refresh skipped: {exc}")
        return
    repo.set_divine_rate(cfg.get("league"), quote.chaos, source=quote.source)
    # Restates prices *and* re-flags outliers: a new rate moves every
    # divine-denominated listing, and a flag decided at the old rate is a
    # judgement about a number that no longer exists.
    flags = restate_prices(cfg, repo, quote.chaos)
    _say(f"divine -> chaos = {quote.chaos:.1f} (from {quote.source}); prices recomputed, "
         f"{flags['flagged']} of {flags['evaluated']} flagged")


def retrain(cfg) -> str:
    """Retrain, keeping the previous artifacts so nothing is lost.

    A backup rather than a straight overwrite: `train` replaces the model in
    place, and a model fitted on a market that has since moved is still the
    only record of what the app was saying yesterday.
    """
    import shutil

    from src.ml.train import NotEnoughData, train

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = Path(cfg.model_dir) / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("originator_model.pkl", "originator_model.json",
                 "originator_model.npz", "originator_model.onnx"):
        source = Path(cfg.model_dir) / name
        if source.exists():
            shutil.copy2(source, backup / name)

    try:
        train(cfg)
    except NotEnoughData as exc:
        return f"retrain skipped: {exc}"
    return f"retrained; previous model kept in {backup}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--limit", type=int, default=1500,
                        help="listings to ask for per cycle")
    parser.add_argument("--rest", type=float, default=DEFAULT_REST_MINUTES,
                        help="minutes to wait between cycles")
    parser.add_argument("--cycles", type=int, default=0,
                        help="stop after this many cycles (0 = until stopped)")
    parser.add_argument("--retrain-at", type=int, default=0,
                        help="retrain once the league passes this many listings "
                             "(0 = never; the previous model is backed up first)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    repo = Repository(cfg.db_path)
    league = cfg.get("league")
    stop_path = Path(cfg.db_path).parent / STOP_FILE
    log_path = Path(cfg.db_path).parent / LOG_FILE
    stop_path.unlink(missing_ok=True)

    _say(f"collecting {league} every {args.rest:g} min; stop with Ctrl-C or by "
         f"creating {stop_path}")

    history: list[dict] = []
    retrained_at = 0
    cycle = 0
    try:
        while True:
            if stop_path.exists():
                _say("stop file found; finishing")
                break
            cycle += 1

            if cycle == 1 or cycle % RATE_EVERY_CYCLES == 0:
                refresh_rate(cfg, repo)

            before = repo.count_listings(league)["total"]
            # A token, so the stop file is honoured *during* a cycle and not
            # only between them. Without it a stop landed only at the next
            # cycle boundary -- which, with a cycle that can run for many
            # minutes under a rate limit, meant "stop" visibly did nothing and
            # the API stayed busy long after it was asked to stop.
            token = CancelToken()
            watcher = threading.Thread(
                target=_watch_stop_file, args=(stop_path, token), daemon=True
            )
            watcher.start()
            try:
                stats = collect_from_trade_api(cfg, repo, limit=args.limit, cancel=token)
            except Exception as exc:  # a long run must survive one bad cycle
                _say(f"cycle {cycle}: collection failed: {type(exc).__name__}: {exc}")
                stats = {"error": str(exc)}
            finally:
                token.cancel()  # retire the watcher thread
            after = repo.count_listings(league)["total"]

            gained = after - before
            _say(
                f"cycle {cycle}: +{gained} listings (total {after:,})"
                + (f"  |  {stats['error']}" if stats.get("error") else "")
                + (f"  |  fresh ratio {stats['fresh_ratio']:.2f}"
                   if stats.get("fresh_ratio") is not None else "")
            )
            if stats.get("unknown_rewards"):
                _say(f"  WARNING unrecognised rewards: {stats['unknown_rewards']}")

            history.append({"cycle": cycle, "at": _now(), "gained": gained,
                            "total": after, "stats": {k: v for k, v in stats.items()
                                                      if k != "unknown_rewards"}})
            log_path.write_text(json.dumps(history, indent=1), encoding="utf-8")

            if should_retrain(after, args.retrain_at, retrained_at):
                _say(retrain(cfg))
                retrained_at = after

            if args.cycles and cycle >= args.cycles:
                _say(f"reached {args.cycles} cycles; finishing")
                break

            # Checked in short steps so a stop file is noticed promptly rather
            # than up to `rest` minutes later.
            deadline = time.monotonic() + args.rest * 60
            while time.monotonic() < deadline:
                if stop_path.exists():
                    break
                time.sleep(min(10.0, max(0.0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        _say("interrupted; finishing")
    finally:
        total = repo.count_listings(league)["total"]
        _say(f"stopped after {cycle} cycle(s); {league} now has {total:,} listings")
        repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
