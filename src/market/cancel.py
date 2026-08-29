"""One off switch, shared by everything that can be told to stop.

A collection run spends most of its wall-clock asleep rather than on the wire.
GGG's published search policy allows thirty searches per five minutes and
carries an 1800-second penalty on the rule above that, so the thing a cancel
usually has to interrupt is not an HTTP request -- it is a wait. A flag that is
only read between requests is therefore not a cancel: press it during a penalty
and nothing happens for half an hour, which reads as a broken button.

So the token is not just a flag. It is a flag *and* the way to wait on it:

* `is_set()` for the loops that poll between units of work. Deliberately the
  same shape `MarketRunJob` already hands its pricer as `stop=self._cancel.is_set`,
  so the two can be unified without either caller changing.
* `sleep(seconds)` for every pause that used to be `time.sleep`. It is
  `Event.wait` underneath, so a cancelled token returns from a thirty-minute
  cooldown the instant it is set rather than at the end of it.

`__bool__` is here so a token reads naturally in a condition (`if cancel:`),
and because `None` -- no token at all -- has to be a legal argument everywhere
one is accepted. Nothing that takes a token may require one.
"""
from __future__ import annotations

import threading


class CancelToken:
    """A cancel flag you can also wait on."""

    __slots__ = ("_event",)

    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event or threading.Event()

    def cancel(self) -> None:
        """Ask everything holding this token to stop at its next check."""
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def __bool__(self) -> bool:
        return self._event.is_set()

    def sleep(self, seconds: float) -> None:
        """Wait, but no longer than it takes for the cancel to arrive.

        Returns immediately once cancelled, which is the whole reason this
        exists in place of `time.sleep`: the waits on this path are measured in
        minutes, and a cancel that lands at the end of one is indistinguishable
        from a cancel that was never read.
        """
        if seconds and seconds > 0:
            self._event.wait(seconds)


def is_cancelled(cancel: "CancelToken | None") -> bool:
    """True only if a token was supplied and it has been set.

    Every caller on this path accepts `cancel=None` for "not cancellable", and
    writing that check inline at each of the half-dozen sites is how one of them
    ends up spelled `if cancel.is_set()` and raises AttributeError in the one
    code path nobody exercises.
    """
    return cancel is not None and cancel.is_set()
