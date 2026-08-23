"""What each individual affix contributes to a map's reward properties.

A map's "Item Quantity: +84%" is not an independent roll. It is the sum of what
every rolled affix contributes, which means the contribution table can be
recovered exactly from listings that show both their affixes and their totals --
a non-negative least squares problem with one unknown per affix.

On the real 3.29 dataset (1838 listings, 108 affixes) an 80/20 split recovers
held-out quantity at R2 0.9990 with a mean absolute error of 0.03. That is not a
regression that happens to fit well; it is inverting a system that is genuinely
additive, which is what makes simulating an exalt arithmetic rather than
guesswork.

**The gate is the important part.** Not every reward behaves this way. On the
same data `more_divination_cards` scored a held-out R2 of -0.019 -- worse than
predicting the mean -- because it comes from the Originator memory rather than
from a rolled affix. A catalogue that modelled it would manufacture value on
every exalt estimate. So each feature earns its place by predicting held-out
data, and the ones that fail are left untouched by `apply`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from src.parsing.affix_text import normalise, resolve_affixes
from src.parsing.affixes import PREFIX, SUFFIX, AffixSet, extract_affixes
from src.schema import FEATURE_NAMES

CATALOGUE_NAME = "affix_catalogue.json"

#: A feature is only modelled if affixes predict it this well on held-out
#: listings. Deliberately strict: the additive ones clear 0.99, so anything
#: scraping past a loose threshold is not really additive.
ADDITIVITY_R2_GATE = 0.95

#: Below this many usable listings the fit is not worth trusting and no
#: catalogue is produced at all, rather than a bad one being shipped.
MIN_LISTINGS = 100

#: Share of listings held back to score each feature.
HOLDOUT_FRACTION = 0.2


@dataclass(frozen=True)
class AffixEntry:
    name: str
    tier: str
    slot: str
    frequency: int
    contributions: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.tier)

    def label(self) -> str:
        return f"{self.name} ({self.tier})"


@dataclass
class AffixCatalogue:
    entries: tuple[AffixEntry, ...]
    modelled_features: tuple[str, ...]
    fit_quality: dict[str, dict[str, float]]
    sample_size: int
    league: str = ""
    map_tier: int = 0
    fitted_at: str = ""
    #: Normalised modifier line -> affixes that can produce it, and the reverse.
    #: These are what make a stash map readable at all: its payload carries
    #: modifier text and nothing else.
    templates: dict[str, list[str]] = field(default_factory=dict)
    affix_templates: dict[str, list[str]] = field(default_factory=dict)

    # -- use ------------------------------------------------------------
    def candidates(self, affixes: AffixSet) -> tuple[AffixEntry, ...]:
        """Affixes an exalt could actually add to this map.

        Two filters, both load-bearing. An affix already on the item cannot roll
        again, and a slot that is full blocks its whole kind -- three prefixes
        rolled means an exalt can only produce a suffix, which changes the
        expected value materially because prefixes and suffixes do not
        contribute equally.
        """
        if not affixes.can_be_exalted:
            return ()
        present = affixes.keys
        allowed = set()
        if affixes.open_prefixes > 0:
            allowed.add(PREFIX)
        if affixes.open_suffixes > 0:
            allowed.add(SUFFIX)
        return tuple(
            e for e in self.entries if e.slot in allowed and e.key not in present
        )

    def apply(self, features: dict[str, float], entry: AffixEntry) -> dict[str, float]:
        """Features as they would be with `entry` added. Never mutates the input."""
        out = dict(features)
        for name in self.modelled_features:
            out[name] = float(out.get(name, 0.0)) + float(entry.contributions.get(name, 0.0))
        return out

    def slots_by_name(self) -> dict[str, str]:
        return {e.name: e.slot for e in self.entries}

    def quantity_by_name(self) -> dict[str, float]:
        """Quantity each affix contributes, averaged over its tiers.

        The resolver only needs enough precision to tell one grouping of lines
        from another, and tiers of one affix sit close together.
        """
        buckets: dict[str, list[float]] = {}
        for entry in self.entries:
            buckets.setdefault(entry.name, []).append(
                float(entry.contributions.get("quantity", 0.0))
            )
        return {name: sum(v) / len(v) for name, v in buckets.items()}

    def read_affixes(self, item: dict[str, Any]) -> AffixSet:
        """Affixes of a map, however much structure its source provided.

        Trade payloads name each affix and its tier outright. Stash payloads do
        not, so the modifier text is resolved against the learned lookup and
        checked against the map's reported Item Quantity.
        """
        structured = extract_affixes(item)
        if structured.known or not self.templates:
            return structured

        descriptions = [
            m if isinstance(m, str) else (m or {}).get("description", "")
            for m in (item.get("explicitMods") or [])
        ]
        return resolve_affixes(
            descriptions,
            _reported_quantity(item),
            self.templates,
            self.affix_templates,
            self.slots_by_name(),
            self.quantity_by_name(),
            corrupted=bool(item.get("corrupted", False)),
        )

    def refused_features(self) -> tuple[str, ...]:
        return tuple(n for n in FEATURE_NAMES if n not in self.modelled_features)

    # -- persistence ----------------------------------------------------
    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "league": self.league,
            "map_tier": self.map_tier,
            "fitted_at": self.fitted_at,
            "sample_size": self.sample_size,
            "modelled_features": list(self.modelled_features),
            "templates": {k: sorted(v) for k, v in self.templates.items()},
            "affix_templates": {k: sorted(v) for k, v in self.affix_templates.items()},
            "fit_quality": self.fit_quality,
            "entries": [
                {
                    "name": e.name,
                    "tier": e.tier,
                    "slot": e.slot,
                    "frequency": e.frequency,
                    "contributions": e.contributions,
                }
                for e in self.entries
            ],
        }
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "AffixCatalogue | None":
        path = Path(path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return cls(
            entries=tuple(
                AffixEntry(
                    name=e["name"],
                    tier=e["tier"],
                    slot=e["slot"],
                    frequency=int(e.get("frequency", 0)),
                    contributions={k: float(v) for k, v in (e.get("contributions") or {}).items()},
                )
                for e in payload.get("entries", [])
            ),
            modelled_features=tuple(payload.get("modelled_features", [])),
            templates={k: list(v) for k, v in (payload.get("templates") or {}).items()},
            affix_templates={
                k: list(v) for k, v in (payload.get("affix_templates") or {}).items()
            },
            fit_quality=payload.get("fit_quality", {}),
            sample_size=int(payload.get("sample_size", 0)),
            league=payload.get("league", ""),
            map_tier=int(payload.get("map_tier", 0)),
            fitted_at=payload.get("fitted_at", ""),
        )


# ---------------------------------------------------------------------------
def fit_catalogue(
    samples: Iterable[tuple[AffixSet, dict[str, float]]],
    league: str = "",
    map_tier: int = 0,
    seed: int = 7,
    payloads: Sequence[dict[str, Any]] | None = None,
) -> AffixCatalogue | None:
    """Recover per-affix contributions from (affix set, reward totals) pairs.

    `payloads` are the raw items the samples came from. They are what the
    modifier-text lookup is harvested from, and without them a stash map -- whose
    payload carries text and nothing else -- cannot be read at all.
    """
    usable = [(a, f) for a, f in samples if a.known and a.affixes]
    if len(usable) < MIN_LISTINGS:
        return None

    keys = sorted({affix.key for a, _ in usable for affix in a.affixes})
    slots = {affix.key: affix.slot for a, _ in usable for affix in a.affixes}
    index = {k: i for i, k in enumerate(keys)}

    matrix = np.zeros((len(usable), len(keys)))
    for row, (affixes, _) in enumerate(usable):
        for affix in affixes.affixes:
            matrix[row, index[affix.key]] = 1.0

    rng = np.random.default_rng(seed)
    order = rng.permutation(len(usable))
    cut = max(1, int(len(usable) * (1 - HOLDOUT_FRACTION)))
    train, test = order[:cut], order[cut:]

    modelled: list[str] = []
    quality: dict[str, dict[str, float]] = {}
    weights: dict[str, np.ndarray] = {}

    for name in FEATURE_NAMES:
        values = np.array([float(f.get(name, 0.0) or 0.0) for _, f in usable])
        fitted = _nnls(matrix[train], values[train])
        held = values[test]
        predicted = matrix[test] @ fitted
        spread = ((held - held.mean()) ** 2).sum()
        r2 = float(1 - ((held - predicted) ** 2).sum() / spread) if spread > 0 else 0.0
        mae = float(np.abs(held - predicted).mean()) if len(held) else 0.0
        quality[name] = {"r2": round(r2, 4), "mae": round(mae, 3), "modelled": r2 >= ADDITIVITY_R2_GATE}
        if r2 >= ADDITIVITY_R2_GATE:
            modelled.append(name)
            # Refit on everything once the feature has earned its place.
            weights[name] = _nnls(matrix, values)

    frequencies = matrix.sum(axis=0).astype(int)
    entries = tuple(
        AffixEntry(
            name=key[0],
            tier=key[1],
            slot=slots[key],
            frequency=int(frequencies[index[key]]),
            contributions={
                name: round(float(weights[name][index[key]]), 4) for name in modelled
            },
        )
        for key in keys
    )

    templates, affix_templates = _harvest_templates(payloads or [])

    return AffixCatalogue(
        entries=entries,
        templates=templates,
        affix_templates=affix_templates,
        modelled_features=tuple(modelled),
        fit_quality=quality,
        sample_size=len(usable),
        league=league,
        map_tier=map_tier,
        fitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _harvest_templates(
    payloads: Sequence[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Learn which modifier lines each affix can print.

    Only trade payloads carry affix names, so this is the one place the two
    formats are joined: what the trade API knows by name, a stash map shows only
    as text, and this table is the bridge between them.
    """
    templates: dict[str, set[str]] = {}
    affix_templates: dict[str, set[str]] = {}
    for item in payloads:
        for mod in item.get("explicitMods") or []:
            if not isinstance(mod, dict):
                continue
            line = normalise(mod.get("description", ""))
            if not line:
                continue
            for sub in mod.get("mods") or []:
                name = (sub or {}).get("name")
                if not name:
                    continue
                templates.setdefault(line, set()).add(name)
                affix_templates.setdefault(name, set()).add(line)
    return (
        {k: sorted(v) for k, v in templates.items()},
        {k: sorted(v) for k, v in affix_templates.items()},
    )


def _reported_quantity(item: dict[str, Any]) -> float | None:
    """The map's own Item Quantity, which is what disambiguates the reading."""
    for prop in item.get("properties") or []:
        if (prop or {}).get("name") not in ("Item Quantity", "quantity"):
            continue
        for value in prop.get("values") or []:
            if value and value[0]:
                digits = "".join(ch for ch in str(value[0]) if ch.isdigit() or ch == "-")
                if digits:
                    return float(digits)
    return None


def _nnls(matrix: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Non-negative least squares. An affix cannot subtract reward."""
    from scipy.optimize import nnls

    solution, _ = nnls(matrix, values)
    return solution


def payloads_from_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """The raw item payloads behind stored listings, for the text lookup."""
    out = [_item_payload(row) for row in rows]
    return [p for p in out if p]


def samples_from_rows(rows: Sequence[Any]) -> list[tuple[AffixSet, dict[str, float]]]:
    """Turn stored listing rows into fitting samples.

    Affixes come from the preserved raw item payload rather than from a stored
    column, so no migration is needed and historic listings are usable
    immediately.
    """
    out: list[tuple[AffixSet, dict[str, float]]] = []
    for row in rows:
        item = _item_payload(row)
        if item is None:
            continue
        affixes = extract_affixes(item)
        if not affixes.known or not affixes.affixes:
            continue
        out.append((affixes, {name: float(row[name] or 0) for name in FEATURE_NAMES}))
    return out


def _item_payload(row: Any) -> dict[str, Any] | None:
    # `.keys()` is required, not a style slip: `"raw_data" in row` on a
    # sqlite3.Row tests the row's *values*, so the plain membership test that
    # linters suggest here silently reports False for every row.
    raw = row["raw_data"] if "raw_data" in row.keys() else None
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    item = payload.get("item")
    return item if isinstance(item, dict) else None
