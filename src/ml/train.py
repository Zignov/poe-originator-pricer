"""Model training.

Run with:  python -m src.ml.train        (or python -m src.cli train)

Produces models/originator_model.pkl plus a JSON metadata sidecar, and records a
row in model_runs. The artifact stores the exact feature list; predict.py refuses
to load a model whose feature schema differs from the current parser schema.
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.config import Config, load_config
from src.database.repository import Repository
from src.ml import models as backends
from src.ml.features import (
    bulk_listing_weights,
    feature_columns,
    monotone_constraints,
    recency_weights,
    rows_to_frame,
    target_inverse,
    target_transform,
)
from src.ml.affix_catalogue import CATALOGUE_NAME, fit_catalogue, samples_from_rows
from src.ml.validation import choose_split, is_snapshot, metrics, rolling_time_folds
from src.schema import FEATURE_NAMES

ARTIFACT_NAME = "originator_model.pkl"
METADATA_NAME = "originator_model.json"

# Candidate recency schedules tried when --tune-weights is passed. The spec's
# example schedule is one of them; it is not assumed correct.
CANDIDATE_WEIGHT_SCHEDULES: dict[str, list[list[float]]] = {
    "flat": [[999999, 1.0]],
    "spec_example": [[12, 1.0], [24, 0.9], [72, 0.7], [168, 0.4], [999999, 0.2]],
    "gentle": [[24, 1.0], [72, 0.9], [168, 0.75], [999999, 0.5]],
    "aggressive": [[12, 1.0], [24, 0.7], [72, 0.4], [168, 0.15], [999999, 0.05]],
}


class NotEnoughData(RuntimeError):
    pass


def load_training_frame(cfg: Config, repo: Repository) -> pd.DataFrame:
    rate = repo.get_divine_rate(cfg.get("league"))
    divine = rate[0] if rate else cfg.get("currency.divine_to_chaos")
    rows = repo.listing_rows(
        league=cfg.get("league"),
        include_outliers=False,
        max_age_days=None,
        map_tier=cfg.get("scope.map_tier"),
        require_uncorrupted=cfg.get("scope.require_uncorrupted"),
    )
    frame = rows_to_frame(rows, divine_to_chaos=divine)
    if frame.empty:
        return frame
    return frame[frame["price_chaos"].notna() & (frame["price_chaos"] > 0)].reset_index(drop=True)


def _fit_and_score(
    name: str,
    frame: pd.DataFrame,
    columns: list[str],
    target_mode: str,
    weight_schedule: Sequence[Sequence[float]],
    idx_train: pd.Index,
    idx_eval: pd.Index,
) -> tuple[Any, dict[str, Any], np.ndarray]:
    X_train = frame.loc[idx_train, columns]
    X_eval = frame.loc[idx_eval, columns]
    y_train_raw = frame.loc[idx_train, "price_chaos"].to_numpy(dtype=float)
    y_eval_raw = frame.loc[idx_eval, "price_chaos"].to_numpy(dtype=float)

    # Two independent corrections, multiplied: age, and how much of a bulk dump
    # the row belongs to.
    weights = recency_weights(frame.loc[idx_train, "observed_at"], weight_schedule)
    weights = weights * bulk_listing_weights(frame.loc[idx_train])
    model = backends.build(name, {"monotone": monotone_constraints(list(X_train.columns))})
    backends.fit(model, X_train, target_transform(y_train_raw, target_mode), weights)

    pred_eval = target_inverse(np.asarray(model.predict(X_eval), dtype=float), target_mode)
    pred_eval = np.clip(pred_eval, 0.0, None)
    return model, metrics(y_eval_raw, pred_eval), pred_eval


def train(
    cfg: Config | None = None,
    optional_features: Sequence[str] = (),
    tune_weights: bool = False,
    compare_backends: bool = True,
    min_rows: int = 60,
    log=print,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    repo = Repository(cfg.db_path)
    try:
        frame = load_training_frame(cfg, repo)
        if len(frame) < min_rows:
            raise NotEnoughData(
                f"only {len(frame)} usable listings; need at least {min_rows}. "
                f"Collect market data first (python -m src.cli collect-market)."
            )

        columns = feature_columns(optional_features)
        target_mode = cfg.get("model.target", "log1p")
        idx_train, idx_valid, idx_test, split_kind = choose_split(frame)
        log(
            f"{len(frame)} listings -> train {len(idx_train)} / valid {len(idx_valid)} "
            f"/ test {len(idx_test)}"
        )
        log(f"split: {split_kind}")

        # -- pick the recency schedule ---------------------------------
        schedules = (
            CANDIDATE_WEIGHT_SCHEDULES
            if tune_weights
            else {"configured": cfg.get("market.recency_weights")}
        )
        # -- pick the backend ------------------------------------------
        candidates = list(cfg.get("model.backends", ["sklearn_hgb"]))
        if compare_backends and backends.BASELINE_BACKEND not in candidates:
            candidates.append(backends.BASELINE_BACKEND)
        installed = backends.available_backends()
        usable = [c for c in candidates if c in installed]
        if not usable:
            usable = ["sklearn_hgb"]
        # A config.yaml saved before a backend existed silently pins the old
        # list, so say what is being skipped rather than letting it go unnoticed.
        untried = [b for b in installed if b not in usable]
        if untried:
            log(f"not tried (not in model.backends): {', '.join(untried)}")

        comparison: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        for schedule_name, schedule in schedules.items():
            for backend_name in usable:
                started = time.perf_counter()
                try:
                    model, valid_metrics, _ = _fit_and_score(
                        backend_name, frame, columns, target_mode, schedule, idx_train, idx_valid
                    )
                except Exception as exc:  # a missing/broken backend must not stop training
                    log(f"  {backend_name} ({schedule_name}) failed: {exc}")
                    continue
                entry = {
                    "backend": backend_name,
                    "weights": schedule_name,
                    "seconds": round(time.perf_counter() - started, 2),
                    **valid_metrics,
                }
                comparison.append(entry)
                log(
                    f"  {backend_name:<12} {schedule_name:<12} "
                    f"MAE {valid_metrics['mae']:.0f}c  medAE {valid_metrics['median_ae']:.0f}c  "
                    f"SMAPE {valid_metrics['smape']:.1f}%  R2 {valid_metrics['r2']:.3f}"
                )
                score = valid_metrics["median_ae"]
                if best is None or score < best["score"]:
                    best = {
                        "score": score,
                        "backend": backend_name,
                        "schedule_name": schedule_name,
                        "schedule": schedule,
                        "valid_metrics": valid_metrics,
                    }

        if best is None:
            raise RuntimeError("no backend trained successfully")
        log(f"selected: {best['backend']} with '{best['schedule_name']}' weights")

        # -- refit on train+valid, score on the held-out newest slice ---
        idx_fit = idx_train.append(idx_valid)
        model, test_metrics, _ = _fit_and_score(
            best["backend"], frame, columns, target_mode, best["schedule"], idx_fit, idx_test
        )

        # -- conformal interval from cross-fitted residuals -------------
        # Calibrating on the validation slice alone left only ~119 points, too
        # few to calibrate the upper price bands separately, so expensive maps
        # fell back to a band widened by cheap noise. Cross-fitting gives one
        # out-of-fold residual for every row instead.
        oof_true, oof_pred = _out_of_fold_predictions(
            frame, columns, target_mode, best["schedule"], best["backend"], log=log
        )
        conformal = _conformal_ratios(oof_true, oof_pred)

        # -- rolling-window check --------------------------------------
        # Only meaningful once the data spans several collection sessions; on a
        # single snapshot the folds would again be splitting on staleness.
        rolling: list[dict[str, Any]] = []
        folds = [] if is_snapshot(frame) else rolling_time_folds(frame)
        for fold_train, fold_eval in folds:
            try:
                _, fold_metrics, _ = _fit_and_score(
                    best["backend"], frame, columns, target_mode, best["schedule"], fold_train, fold_eval
                )
                rolling.append(fold_metrics)
            except Exception:
                continue

        # -- final model on everything ---------------------------------
        final = backends.build(
            best["backend"], {"monotone": monotone_constraints(columns)}
        )
        y_all = frame["price_chaos"].to_numpy(dtype=float)
        backends.fit(
            final,
            frame[columns],
            target_transform(y_all, target_mode),
            recency_weights(frame["observed_at"], best["schedule"])
            * bulk_listing_weights(frame),
        )

        observed = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
        version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        meta: dict[str, Any] = {
            "model_version": version,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "backend": best["backend"],
            "target": target_mode,
            "dataset_size": int(len(frame)),
            "feature_list": columns,
            "core_features": list(FEATURE_NAMES),
            "split": split_kind,
            "recency_weights_name": best["schedule_name"],
            "recency_weights": [list(pair) for pair in best["schedule"]],
            "metrics": {
                "validation": best["valid_metrics"],
                "test": test_metrics,
                "rolling_folds": rolling,
                "backend_comparison": comparison,
            },
            "conformal": conformal,
            "market_date_start": observed.min().isoformat() if observed.notna().any() else None,
            "market_date_end": observed.max().isoformat() if observed.notna().any() else None,
            "league": cfg.get("league"),
            "scope": {
                "map_tier": cfg.get("scope.map_tier"),
                "require_uncorrupted": cfg.get("scope.require_uncorrupted"),
            },
            "divine_to_chaos_at_training": float(frame["divine_to_chaos"].dropna().iloc[0])
            if frame["divine_to_chaos"].notna().any()
            else None,
        }

        artifact_path = cfg.model_dir / ARTIFACT_NAME
        payload = {
            "model": final,
            "metadata": meta,
            # Kept for nearest-neighbour comparables and confidence scoring.
            "training_features": frame[columns].to_numpy(dtype=float),
            "training_prices": y_all,
            # Unix seconds. Do NOT use astype("int64") // 10**9: pandas 2 stores
            # datetimes in nanoseconds and pandas 3 in microseconds, so that
            # expression is silently off by 1000x depending on the version.
            "training_times": _unix_seconds(observed),
            "training_ids": frame["id"].to_numpy(),
        }
        with open(artifact_path, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        (cfg.model_dir / METADATA_NAME).write_text(
            json.dumps(meta, indent=2, default=str), encoding="utf-8"
        )
        repo.record_model_run(meta, str(artifact_path))
        _fit_affix_catalogue(cfg, repo, log)

        log(
            f"saved {artifact_path.name}  |  held-out test: "
            f"MAE {test_metrics['mae']:.0f}c  medAE {test_metrics['median_ae']:.0f}c  "
            f"SMAPE {test_metrics['smape']:.1f}%  R2 {test_metrics['r2']:.3f}"
        )
        return meta
    finally:
        repo.close()


def _unix_seconds(times: pd.Series) -> np.ndarray:
    """Datetime series -> Unix seconds, independent of pandas' storage resolution."""
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    return (
        (pd.to_datetime(times, utc=True, errors="coerce") - epoch)
        .dt.total_seconds()
        .fillna(0.0)
        .to_numpy(dtype="int64")
    )


#: Predicted-price bands the conformal interval is calibrated within, in chaos.
#: A single global band is wrong here: a 5-chaos dump missed by 4x and a
#: 300-chaos map missed by 4x are not the same kind of error, and pooling them
#: gave a 255c map an interval of 115-1000c. Cheap listings are close to
#: arbitrary -- someone dumping at 1c versus 20c is a 20x ratio error that says
#: nothing about how well a real map can be priced -- so their noise must not be
#: charged to the maps the user actually cares about.
CONFORMAL_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 30.0),
    (30.0, 150.0),
    (150.0, 600.0),
    (600.0, float("inf")),
)

#: Below this many calibration points a band borrows the global band instead.
MIN_PER_BAND = 25


def _out_of_fold_predictions(
    frame: pd.DataFrame,
    columns: list[str],
    target_mode: str,
    schedule: Sequence[Sequence[float]],
    backend_name: str,
    folds: int = 5,
    log=print,
) -> tuple[np.ndarray, np.ndarray]:
    """One prediction per row, from a model that never saw that row.

    Folds are split by seller, for the same reason the train/test split is: a
    seller who lists several maps at one flat price must not appear on both sides
    of a fold, or the residuals look better than they are.
    """
    sellers = (
        frame["seller"].fillna("<unknown>")
        if "seller" in frame.columns
        else pd.Series(frame.index.astype(str), index=frame.index)
    )
    unique = pd.Index(pd.unique(sellers))
    if len(unique) < folds * 2:
        sellers = pd.Series(frame.index.astype(str), index=frame.index)
        unique = pd.Index(pd.unique(sellers))

    rng = np.random.default_rng(11)
    shuffled = unique[rng.permutation(len(unique))]
    assignment = {key: i % folds for i, key in enumerate(shuffled)}
    fold_of = sellers.map(assignment)

    y_all = frame["price_chaos"].to_numpy(dtype=float)
    predicted = np.full(len(frame), np.nan)
    for fold in range(folds):
        holdout = frame.index[fold_of == fold]
        fit_on = frame.index[fold_of != fold]
        if len(holdout) == 0 or len(fit_on) < 20:
            continue
        try:
            _, _, preds = _fit_and_score(
                backend_name, frame, columns, target_mode, schedule, fit_on, holdout
            )
        except Exception as exc:
            log(f"  conformal fold {fold} failed: {exc}")
            continue
        predicted[frame.index.get_indexer(holdout)] = preds

    ok = ~np.isnan(predicted)
    return y_all[ok], predicted[ok]


def _ratio_band(ratio: np.ndarray) -> tuple[float, float]:
    return (
        float(max(np.quantile(ratio, 0.10), 0.05)),
        float(min(np.quantile(ratio, 0.90), 20.0)),
    )


def _conformal_ratios(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    """Split-conformal interval, as multiplicative ratios, calibrated per band.

    Prices are long-tailed, so an additive band would be nonsense at the cheap
    end; the calibration is on the ratio actual/predicted instead. It is then
    done separately within predicted-price bands, because the ratio error is far
    larger for near-worthless listings than for the maps worth pricing.

    `predict.py` picks the band from its own prediction and falls back to
    `global` when a band was too sparse to calibrate.
    """
    mask = (y_pred > 0) & (y_true > 0)
    n = int(mask.sum())
    if n < 10:
        return {
            "low": 0.7,
            "high": 1.4,
            "calibrated_on": n,
            "coverage": float("nan"),
            "bands": [],
        }

    true_ok = y_true[mask]
    pred_ok = y_pred[mask]
    ratio = true_ok / pred_ok
    low, high = _ratio_band(ratio)
    coverage = float(np.mean((ratio >= low) & (ratio <= high)) * 100.0)

    bands: list[dict[str, Any]] = []
    for lower, upper in CONFORMAL_BANDS:
        in_band = (pred_ok >= lower) & (pred_ok < upper)
        count = int(in_band.sum())
        if count < MIN_PER_BAND:
            continue
        band_ratio = ratio[in_band]
        band_low, band_high = _ratio_band(band_ratio)
        bands.append(
            {
                "min_chaos": lower,
                "max_chaos": None if upper == float("inf") else upper,
                "low": band_low,
                "high": band_high,
                "calibrated_on": count,
                "coverage": float(
                    np.mean((band_ratio >= band_low) & (band_ratio <= band_high)) * 100.0
                ),
            }
        )

    return {
        "low": low,
        "high": high,
        "calibrated_on": n,
        "coverage": coverage,
        "bands": bands,
    }



def _fit_affix_catalogue(cfg: Config, repo: Repository, log=print) -> None:
    """Recover the per-affix contribution table alongside the price model.

    It is fitted here rather than on demand because it is scope-bound: the
    contributions belong to one league and map tier, exactly like the model, so
    the two should never be able to come from different datasets. A failure is
    logged and swallowed -- the exalt feature is an extra, and it must not be
    able to take training down with it.
    """
    try:
        rows = repo.listing_rows(
            league=cfg.get("league"),
            include_outliers=False,
            map_tier=cfg.get("scope.map_tier"),
            require_uncorrupted=cfg.get("scope.require_uncorrupted"),
        )
        catalogue = fit_catalogue(
            samples_from_rows(rows),
            league=cfg.get("league"),
            map_tier=cfg.get("scope.map_tier"),
        )
    except Exception as exc:  # noqa: BLE001 - never fail training over this
        log(f"affix catalogue: not fitted ({exc})")
        return

    if catalogue is None:
        log("affix catalogue: not enough listings with affix data; exalt advice disabled")
        return

    catalogue.save(cfg.model_dir / CATALOGUE_NAME)
    refused = catalogue.refused_features()
    log(
        f"affix catalogue: {len(catalogue.entries)} affixes from {catalogue.sample_size} "
        f"listings, {len(catalogue.modelled_features)}/{len(FEATURE_NAMES)} rewards modelled"
        + (f" (not affix-driven: {', '.join(refused)})" if refused else "")
    )

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the Originator Map price model")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--optional-features",
        nargs="*",
        default=[],
        help="extra context columns to include, e.g. item_level hour_of_day",
    )
    parser.add_argument("--tune-weights", action="store_true", help="search recency schedules")
    parser.add_argument("--min-rows", type=int, default=60)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    try:
        train(
            cfg,
            optional_features=args.optional_features,
            tune_weights=args.tune_weights,
            min_rows=args.min_rows,
        )
    except NotEnoughData as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
