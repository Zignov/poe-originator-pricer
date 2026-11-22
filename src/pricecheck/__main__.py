"""Run the clipboard price checker.

    python -m src.pricecheck                  # hotkey + clipboard watching
    python -m src.pricecheck --once           # price the clipboard, print, exit
    python -m src.pricecheck --no-watch       # hotkey only
    python -m src.pricecheck --no-spawn       # use a service already running
    python -m src.pricecheck --hotkey ctrl+alt+p --strategy patient

In the game: hover an item and press **Ctrl+C**, which is Path of Exile's own
copy binding. Nothing here sends a key to the game -- see
src/pricecheck/clipboard.py for why that is the whole design.

`--once` is the diagnostic path: no window, no hotkey, no event loop, so a
machine where the popup will not draw can still prove that the clipboard, the
service and the model all work.
"""
from __future__ import annotations

import argparse
import sys

from src.config import load_config
from src.pricing.sell_strategy import STRATEGIES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.pricecheck", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--strategy", choices=STRATEGIES, default=None)
    parser.add_argument("--hotkey", default=None, help="e.g. ctrl+alt+d")
    parser.add_argument(
        "--no-watch", action="store_true",
        help="do not price on Ctrl+C; wait for the hotkey instead",
    )
    parser.add_argument(
        "--no-spawn", action="store_true",
        help="require a pricing service to be running already",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="price what is on the clipboard now, print it, and exit",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.once:
        return _once(cfg, args)

    from src.pricecheck.app import PriceCheckUnavailable, run_price_check

    try:
        return run_price_check(
            cfg,
            port=args.port,
            strategy=args.strategy,
            watch=False if args.no_watch else None,
            hotkey=args.hotkey,
            spawn=not args.no_spawn,
        )
    except PriceCheckUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _once(cfg, args) -> int:
    """One check, printed. No window and no hotkey.

    Prints the price and the rolls, and never the clipboard's contents. The
    whole point of the gate in `src/pricecheck/clipboard.py` is that text which
    is not item text does not travel, and echoing a clipboard into a terminal
    -- which on Windows may be scrolled back through later -- would be the one
    place this package leaked it.
    """
    from src.pricecheck import clipboard
    from src.pricecheck.app import PriceCheckSettings, spawn_service
    from src.pricecheck.client import ServiceError, client_from_config
    from src.pricecheck.render import (
        card_from_response,
        status_caption,
        unknown_reward_warning,
    )

    ok, why = clipboard.available()
    if not ok:
        print(f"error: {why}", file=sys.stderr)
        return 2

    text = clipboard.read_item_text()
    if not text:
        print(
            "Nothing on the clipboard looks like a copied item. In game, hover "
            "an item and press Ctrl+C.",
            file=sys.stderr,
        )
        return 1

    settings = PriceCheckSettings.from_config(cfg)
    port = args.port or settings.port
    client = client_from_config(cfg, port=port)
    child = None
    if not client.health():
        if args.no_spawn:
            print(
                f"error: nothing is listening on 127.0.0.1:{port}. Start it with "
                "`python -m src.service`.",
                file=sys.stderr,
            )
            return 2
        child = spawn_service(port, cfg)
        if child is None:
            print("error: could not start the pricing service", file=sys.stderr)
            return 2
        client = client_from_config(cfg, port=port)

    # Every request this function makes has to happen before the service it
    # started is torn down -- which is not as obvious as it sounds. The first
    # version of this put the price in a try/finally that terminated the child
    # and *then* asked for the status, and got a ConnectionResetError out of a
    # socket that had been shut from under it.
    caption = ""
    try:
        card = card_from_response(
            client.price_text(text, args.strategy or settings.strategy)
        )
        try:
            caption = status_caption(client.status())
        except ServiceError:
            pass
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if child is not None:
            child.terminate()

    if caption:
        print(caption)
    print()
    if card.problem:
        print(f"no price: {card.problem}")
    else:
        head = f"{card.headline}  [{card.verdict}]"
        if card.range_text:
            head += f"   range {card.range_text}"
        print(head)
        if card.confidence:
            print(f"confidence: {card.confidence}")
    if card.rolls:
        print("  ".join(f"{label} {value}" for label, value in card.rolls))
    warning = unknown_reward_warning(card.unknown_rewards)
    if warning:
        print(f"\nWARNING: {warning}", file=sys.stderr)
    for reason in card.reasons:
        print(f"  - {reason}")
    if card.note and not card.problem:
        print(f"\nnote to paste: {card.note}")
    return 0 if not card.problem else 1


if __name__ == "__main__":
    raise SystemExit(main())
