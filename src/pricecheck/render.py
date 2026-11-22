"""What the popup says, decided before any window exists.

The same split `src/overlay/window.py` opens with, and for the same reason:
everything with a judgement in it -- which word, which colour, where the window
goes -- is a pure function of the service's reply, so it can be tested without
a display, and the tkinter module below is left with nothing to do but draw.

One decision here is worth stating outright, because it looks like an omission.

**A single item gets no value band.** The grid in the web app and the tiles in
the overlay colour a map by `percentile_bands`, which ranks a tab against
itself -- that is what makes a 400c map in a tab of 600c maps read as ordinary.
One copied item is not a distribution and has no percentile, and inventing one
from a sample of one would produce a colour that changes meaning depending on
what else happens to be in the stash. So the popup colours the *verdict*
instead, from the absolute thresholds in `src/ml/decision_bands.py` that the
model's training floor already makes real: under 30c the model is not fitted
and the answer is a word, not a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ml.decision_bands import MARGINAL, REROLL, SELL, verdict
from src.schema import FEATURE_SHORT_LABELS
from src.ui.palette import CONFIDENCE_COLOURS, VALUE_COLOURS, confidence_fill

#: The rolls, in the order the CLI's `price-tab` table prints them, with the
#: abbreviations it uses. A popup over a game has room for about this much, and
#: someone who has read one of these in a terminal should not have to learn a
#: second set of names to read it over the stash.
SHORT_LABELS: tuple[tuple[str, str], ...] = tuple(
    (name, FEATURE_SHORT_LABELS[name])
    for name in (
        "quantity", "rarity", "pack_size", "more_currency",
        "more_maps", "more_scarabs", "more_divination_cards",
    )
)


def verdict_colour(word: str) -> str:
    """The chip colour for Reroll / Marginal / Sell.

    Read out of `VALUE_COLOURS` at call time rather than frozen into a constant,
    which is the mechanism `src/ui/palette.py` documents: the dictionary is
    mutated in place when the Settings page re-colours a band, so a popup picks
    the user's colours up without this module knowing that happened.

    The three chosen are the bands whose jobs already match. `negligible` exists
    to recede and that is exactly what a rerollable map should do; `medium` is
    the green a worthwhile map is already drawn in elsewhere; `low` sits between
    them in the same ramp. Deliberately *not* `CONFIDENCE_COLOURS`, even though
    a traffic light would be the obvious thing: those answer "how much should
    you trust this number", and painting the verdict in them would make a
    confident price about a junk map look like a warning.
    """
    return {
        REROLL: VALUE_COLOURS["negligible"],
        MARGINAL: VALUE_COLOURS["low"],
        SELL: VALUE_COLOURS["medium"],
    }.get(word, VALUE_COLOURS["unknown"])


@dataclass(frozen=True)
class PriceCard:
    """Everything one popup draws, as strings and colours.

    A card always renders. There is no state in which the price checker has
    nothing to say: an item it cannot price and a service it cannot reach are
    both cards with `problem` set, because the alternative -- a hotkey that
    silently does nothing -- is the failure mode that makes people uninstall a
    tool rather than fix it.
    """

    headline: str = ""
    verdict: str = ""
    verdict_colour: str = ""
    range_text: str = ""
    fair_value_text: str = ""
    confidence: str = ""
    confidence_colour: str = ""
    confidence_fraction: float | None = None
    rolls: tuple[tuple[str, str], ...] = ()
    reasons: tuple[str, ...] = ()
    note: str = ""
    source: str = ""
    strategy: str = ""
    corrupted: bool = False
    #: Non-empty when this card is reporting a failure rather than a price. The
    #: popup draws it in place of the headline, so one widget tree serves both.
    problem: str = ""
    #: Reward names the parser did not recognise on this item. Shown in place
    #: of nothing, because a reward read as 0 looks exactly like a reward the
    #: map does not have -- which is how the Covens rename stayed invisible.
    unknown_rewards: tuple[str, ...] = ()
    #: True when the problem is the service rather than the item -- the popup
    #: shows those for longer, because they need an action rather than a glance.
    actionable: bool = False
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def priced(self) -> bool:
        return not self.problem


def error_card(message: str, actionable: bool = True) -> PriceCard:
    """A card that reports a failure. `message` is shown to the user verbatim."""
    return PriceCard(problem=message, actionable=actionable)


def card_from_response(payload: dict[str, Any]) -> PriceCard:
    """Turn `/price/text`'s JSON into the card the popup draws.

    Tolerant of a missing key throughout. The client and the service ship
    together so a shape mismatch should be impossible, but "impossible" here
    would mean a KeyError inside a Tk callback, which on Windows prints to a
    console that a double-clicked launcher does not have -- an invisible
    failure, in a tool whose entire job is to be visible.
    """
    if not isinstance(payload, dict):
        return error_card("the pricing service sent something unreadable")

    display = _mapping(payload.get("display"))
    chaos = _mapping(payload.get("chaos"))
    confidence = _mapping(payload.get("confidence"))
    item = _mapping(payload.get("item"))

    if not payload.get("supported", False):
        # An unsupported map is not an error in the service; it is an answer.
        # `note` carries the reason -- out of scope, corrupted, no comparables
        # -- and showing it is the whole value of the popup in that case.
        return PriceCard(
            problem=str(payload.get("note") or "this item is not one the pricer covers"),
            actionable=False,
            rolls=_rolls(item),
            corrupted=bool(item.get("corrupted")),
            unknown_rewards=_unknown(item),
        )

    suggested = chaos.get("suggested")
    word = verdict(_number(suggested))
    level = str(confidence.get("level") or "")
    fill = confidence_fill(level, _number(confidence.get("score")))

    fair = display.get("fair_value")
    unit = str(display.get("unit") or "")
    return PriceCard(
        headline=str(display.get("formatted") or ""),
        verdict=word,
        verdict_colour=verdict_colour(word),
        range_text=str(display.get("range") or ""),
        # The fair value is shown beside the headline only when the strategy has
        # actually moved it. At `market` the two are within a percent and
        # printing both invites the reader to look for a difference that is not
        # there.
        fair_value_text=(
            f"{_trim(fair)}{unit}"
            if fair is not None and not _close(fair, display.get("suggested"))
            else ""
        ),
        confidence=level,
        confidence_colour=fill[0] if fill else CONFIDENCE_COLOURS[""],
        confidence_fraction=fill[1] if fill else None,
        rolls=_rolls(item),
        reasons=tuple(str(r) for r in confidence.get("reasons") or ()),
        note=str(payload.get("note") or ""),
        source=str(payload.get("source") or ""),
        strategy=str(payload.get("strategy") or ""),
        corrupted=bool(item.get("corrupted")),
        unknown_rewards=_unknown(item),
        extra={"chaos": chaos},
    )


def popup_position(
    cursor: tuple[int, int],
    size: tuple[int, int],
    screen: tuple[int, int],
    offset: tuple[int, int] = (18, 18),
    margin: int = 8,
) -> tuple[int, int]:
    """Where to put a popup of `size` for a pointer at `cursor`.

    Below and right of the pointer by default, because that is where a cursor's
    own hotspot is not, and flipped to the other side of it rather than merely
    clamped when there is no room: a window shoved back under the pointer would
    sit on top of the item the user is still hovering, which is the one thing
    on screen it must not cover.
    """
    width, height = size
    screen_w, screen_h = screen
    dx, dy = offset

    x = cursor[0] + dx
    if x + width + margin > screen_w:
        x = cursor[0] - dx - width
    y = cursor[1] + dy
    if y + height + margin > screen_h:
        y = cursor[1] - dy - height

    # Only now clamp, for the case the flip could not solve: a popup wider than
    # the screen, or a pointer in a corner of a very small display. Off-screen
    # is strictly worse than overlapping.
    x = max(margin, min(x, max(margin, screen_w - width - margin)))
    y = max(margin, min(y, max(margin, screen_h - height - margin)))
    return int(x), int(y)


def status_caption(status: dict[str, Any] | None, problem: str = "") -> str:
    """One line describing what the checker is attached to.

    Shown on the control strip rather than in the popup. The league matters
    more than anything else on it: a model trained on one league pricing maps
    from another is the failure that looks like working software, and this is
    the only place the user can see which one is loaded.
    """
    if problem:
        return problem
    if not status:
        return "not connected to a pricing service"
    league = status.get("league") or "no league set"
    model = status.get("model_version") or "no model"
    comparables = status.get("comparables") or 0
    if status.get("model_error"):
        return f"{league} - {status['model_error']}"
    return f"{league} - model {model}, {int(comparables)} comparables"


# -- helpers ----------------------------------------------------------------
def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _close(a: Any, b: Any, tolerance: float = 0.01) -> bool:
    """Are two displayed amounts the same number to the eye?"""
    left, right = _number(a), _number(b)
    if left is None or right is None:
        return left is right
    if right == 0:
        return abs(left) < tolerance
    return abs(left - right) / abs(right) < tolerance


def _trim(value: Any) -> str:
    """A display amount without a pointless trailing zero."""
    number = _number(value)
    if number is None:
        return ""
    return f"{number:.0f}" if float(number).is_integer() else f"{number:.1f}"


def _unknown(item: dict[str, Any]) -> tuple[str, ...]:
    raw = item.get("unknown_rewards")
    return tuple(str(name) for name in raw) if isinstance(raw, list) else ()


def unknown_reward_warning(names: tuple[str, ...]) -> str:
    """The sentence shown when a reward name could not be placed."""
    if not names:
        return ""
    listed = ", ".join(sorted(set(names)))
    return f"unknown reward read as 0: {listed} -- this build needs updating"


def _rolls(item: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The item's rolls as (label, value) pairs, zeroes included.

    A zero is information: a zero thread roll is why a map is cheap, and dropping
    the zero rows would leave the reader unable to tell a roll that is absent
    from one this popup forgot to show.
    """
    out: list[tuple[str, str]] = []
    for name, label in SHORT_LABELS:
        if name in item:
            out.append((label, str(int(_number(item.get(name)) or 0))))
    return tuple(out)
