"""Loading a trained model and producing an estimate with an interval.

A model is refused if its feature list does not match the current parser schema,
so a schema change can never silently produce wrong prices.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.config import Config, load_config
from src.ml.features import feature_columns, map_to_values, target_inverse
from src.ml.train import ARTIFACT_NAME
from src.schema import FEATURE_NAMES, NormalizedMap


class ModelSchemaMismatch(RuntimeError):
    pass


class ModelNotTrained(RuntimeError):
    pass


@dataclass
class Estimate:
    fair_value_chaos: float
    interval_low_chaos: float
    interval_high_chaos: float
    confidence: str
    confidence_score: float
    confidence_reasons: list[str] = field(default_factory=list)
    model_version: str = ""

    def as_divine(self, divine_to_chaos: float) -> dict[str, float]:
        return {
            "fair_value": self.fair_value_chaos / divine_to_chaos,
            "low": self.interval_low_chaos / divine_to_chaos,
            "high": self.interval_high_chaos / divine_to_chaos,
        }


class Predictor:
    def __init__(self, payload: dict[str, Any]):
        self.model = payload["model"]
        self.metadata: dict[str, Any] = payload["metadata"]
        self.training_features: np.ndarray = payload.get("training_features", np.empty((0, 0)))
        self.training_prices: np.ndarray = payload.get("training_prices", np.empty(0))
        self.training_times: np.ndarray = payload.get("training_times", np.empty(0))
        self.columns: list[str] = list(self.metadata["feature_list"])
        self.target_mode: str = self.metadata["target"]

        core = list(self.metadata.get("core_features", []))
        if core and core != list(FEATURE_NAMES):
            raise ModelSchemaMismatch(
                f"model was trained on features {core}, parser now produces "
                f"{list(FEATURE_NAMES)}. Retrain before using it."
            )
        expected_optional = [c for c in self.columns if c not in FEATURE_NAMES]
        if self.columns != feature_columns(expected_optional):
            raise ModelSchemaMismatch(
                f"model feature order {self.columns} does not match the current schema"
            )

        # Feature scale used for nearest-neighbour distance, so a 130 rarity roll
        # does not dominate a 40 more-currency roll purely by magnitude.
        if self.training_features.size:
            spread = self.training_features.std(axis=0)
            self._scale = np.where(spread > 1e-6, spread, 1.0)
        else:
            self._scale = np.ones(len(self.columns))

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, cfg: Config | None = None, path: str | Path | None = None) -> "Predictor":
        cfg = cfg or load_config()
        artifact = Path(path) if path else cfg.model_dir / ARTIFACT_NAME
        if not artifact.exists():
            raise ModelNotTrained(
                f"no trained model at {artifact}. Run: python -m src.ml.train"
            )
        with open(artifact, "rb") as handle:
            payload = pickle.load(handle)
        return cls(payload)

    @property
    def version(self) -> str:
        return self.metadata.get("model_version", "unknown")

    def frame_for(self, items: Sequence[NormalizedMap], divine_to_chaos: float | None = None) -> pd.DataFrame:
        return pd.DataFrame(
            [map_to_values(item, self.columns, divine_to_chaos) for item in items],
            columns=self.columns,
        )

    def predict_chaos(self, items: Sequence[NormalizedMap], divine_to_chaos: float | None = None) -> np.ndarray:
        if not items:
            return np.empty(0)
        raw = np.asarray(self.model.predict(self.frame_for(items, divine_to_chaos)), dtype=float)
        return np.clip(target_inverse(raw, self.target_mode), 0.0, None)

    # ------------------------------------------------------------------
    def estimate(self, item: NormalizedMap, divine_to_chaos: float | None = None) -> Estimate:
        return self.estimate_many([item], divine_to_chaos)[0]

    def estimate_many(
        self, items: Sequence[NormalizedMap], divine_to_chaos: float | None = None
    ) -> list[Estimate]:
        """Estimates for a batch, with one call into the model rather than N.

        A stash tab is sixty maps, and `model.predict` costs about as much for
        sixty rows as for one -- the per-call overhead is the whole expense, so
        pricing them one at a time paid it sixty times. The feature matrix is
        built once here and reused for the nearest-neighbour lookup as well,
        which otherwise rebuilt the same single-row frame per map.
        """
        from src.ml.confidence import score_confidence

        if not items:
            return []
        frame = self.frame_for(items, divine_to_chaos)
        raw = np.asarray(self.model.predict(frame), dtype=float)
        fairs = np.clip(target_inverse(raw, self.target_mode), 0.0, None)
        vectors = frame.to_numpy(dtype=float)

        estimates: list[Estimate] = []
        for item, fair_value, vector in zip(items, fairs, vectors):
            fair = float(fair_value)
            low_ratio, high_ratio = self.interval_ratios(fair)
            low = fair * low_ratio
            high = fair * high_ratio
            level, score, reasons = score_confidence(
                self, item, fair, low, high, vector=vector
            )
            estimates.append(
                Estimate(
                    fair_value_chaos=fair,
                    interval_low_chaos=low,
                    interval_high_chaos=high,
                    confidence=level,
                    confidence_score=score,
                    confidence_reasons=reasons,
                    model_version=self.version,
                )
            )
        return estimates

    def interval_ratios(self, predicted_chaos: float) -> tuple[float, float]:
        """Conformal band for a prediction of this size.

        Calibrated per price band: pooling a 5-chaos dump's 4x miss with a
        300-chaos map's 4x miss produced absurdly wide intervals on the maps that
        matter. Falls back to the global band where a band was too sparse.
        """
        conformal = self.metadata.get("conformal", {})
        for band in conformal.get("bands", []) or []:
            upper = band.get("max_chaos")
            if predicted_chaos >= band.get("min_chaos", 0.0) and (
                upper is None or predicted_chaos < upper
            ):
                return float(band["low"]), float(band["high"])
        return float(conformal.get("low", 0.7)), float(conformal.get("high", 1.4))

    # ------------------------------------------------------------------
    def neighbour_distances(
        self, item: NormalizedMap, k: int = 10, vector: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """(indices, scaled distances) of the k closest training examples.

        `vector` lets a caller that has already built the item's feature row --
        `estimate_many` has, for the whole batch -- skip rebuilding it here.
        """
        if not self.training_features.size:
            return np.empty(0, dtype=int), np.empty(0)
        if vector is None:
            vector = self.frame_for([item]).to_numpy(dtype=float)[0]
        deltas = (self.training_features - vector) / self._scale
        distances = np.sqrt((deltas**2).sum(axis=1))
        order = np.argsort(distances)[:k]
        return order, distances[order]
