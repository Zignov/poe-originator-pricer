"""Collector interface.

Every collector, however it obtains data, yields MarketListing objects built on
the same NormalizedMap. The application is usable with only manual imports.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Iterable, Iterator

from src.schema import MarketListing


class MarketCollector(abc.ABC):
    #: Written into originator_listings.source.
    name: str = "base"

    @abc.abstractmethod
    def collect(self, **kwargs) -> Iterator[MarketListing]:
        """Yield normalized listings."""

    def available(self) -> tuple[bool, str]:
        """Whether this collector can run right now, and why not if it cannot."""
        return True, ""


def dedupe(listings: Iterable[MarketListing]) -> list[MarketListing]:
    """Drop repeats within a single collection pass."""
    seen: set[str] = set()
    out: list[MarketListing] = []
    for listing in listings:
        key = listing.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(listing)
    return out


@dataclass
class ProgressUpdate:
    """One progress tick from a collector.

    Collectors used to emit a bare string, which a terminal can print and a
    progress bar cannot. This carries the counts as well, and stringifies to the
    old message so `progress=lambda m: print(m)` still reads correctly.

    `collected` counts listings yielded this run, not rows added to the database
    -- a listing already stored comes back as an update, not an insert.
    """

    message: str
    collected: int = 0
    target: int = 0
    slice_index: int = 0
    slice_count: int = 0
    skipped_as_bulk: int = 0

    @property
    def fraction(self) -> float:
        """0.0-1.0 for a progress bar, whichever of the two axes is further on.

        Slices matter as well as listings: a run can exhaust its slices before
        reaching `limit`, and a bar frozen at 40% while the run finishes is
        worse than one that tracks the slower-but-real axis.
        """
        by_listings = self.collected / self.target if self.target else 0.0
        by_slices = self.slice_index / self.slice_count if self.slice_count else 0.0
        return max(0.0, min(1.0, max(by_listings, by_slices)))

    def __str__(self) -> str:
        return self.message
