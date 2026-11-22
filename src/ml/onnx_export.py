"""Export a fitted regressor to ONNX, and run one without its training library.

The point is what the shipped app has to carry. Training needs CatBoost,
LightGBM, XGBoost, scikit-learn and their shared numpy/scipy/OpenMP baggage --
hundreds of megabytes that exist only to *fit* a model. Inference needs a
matrix multiply over a tree ensemble, which ONNX Runtime does in a ~13 MB
wheel. So training stays a developer concern run from the full stack, and the
app ships `.onnx` plus that runtime.

Every consumer of a model in this codebase calls `.predict(frame)` and nothing
else -- `Predictor.predict_chaos`, `fair_value`, the Model Analysis page, and
`explain`'s ablation path. The two places that want more than that already
degrade on their own: `global_importance` falls back to permutation importance
when the backend exposes no `feature_importances_`, and `explain_item` falls
back to ablation when SHAP raises. So `OnnxModel` only has to be a `.predict`,
and the richer paths keep working without a native model present.

All thirteen features this project can train on are numeric (see
`src/schema.py:FEATURE_NAMES` and `src/ml/features.py:OPTIONAL_FEATURES`), which
is what makes CatBoost exportable at all: its ONNX exporter supports numerical
features only, and categorical support is an open issue upstream. If a
categorical feature is ever added, this module is where it breaks, loudly, and
`export()` raising is the correct outcome rather than a silently wrong model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

#: The file that sits beside `originator_model.pkl`.
ONNX_NAME = "originator_model.onnx"


#: The arrays `Predictor` actually reads once it is loaded: the comparables it
#: measures distance against, their prices, and when each was observed. Note
#: what is absent -- `training_ids` is written into the pickle but never read
#: outside training, so it is not part of the runtime set.
RUNTIME_ARRAYS: tuple[str, ...] = (
    "training_features",
    "training_prices",
    "training_times",
)


class ExportUnsupported(RuntimeError):
    """This backend cannot be written to ONNX, and the caller should say so."""


def onnx_path_for(artifact: str | Path) -> Path:
    """The `.onnx` beside a given `.pkl`, whatever the pickle is called."""
    return Path(artifact).with_suffix(".onnx")


def arrays_path_for(artifact: str | Path) -> Path:
    """The `.npz` holding the runtime arrays."""
    return Path(artifact).with_suffix(".npz")


def metadata_path_for(artifact: str | Path) -> Path:
    """The `.json` metadata sidecar training already writes."""
    return Path(artifact).with_suffix(".json")


def write_runtime_arrays(payload: dict[str, Any], artifact: str | Path) -> Path:
    """Write the arrays an exported model needs, outside the pickle.

    The pickle holds a fitted CatBoost/LightGBM/XGBoost object, so *reading* it
    imports that library -- which defeats the entire point of exporting. The
    arrays therefore have to live somewhere a library-free install can open,
    and `.npz` is numpy's own format with no such problem.
    """
    path = arrays_path_for(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        **{
            name: np.asarray(payload.get(name, np.empty(0)))
            for name in RUNTIME_ARRAYS
        },
    )
    return path


def load_runtime_payload(artifact: str | Path) -> dict[str, Any] | None:
    """A payload built from the `.json` and `.npz`, or None if either is absent.

    Deliberately shaped like the pickle's payload minus the model, so the
    caller can hand it to `Predictor` unchanged.
    """
    import json

    meta_file = metadata_path_for(artifact)
    arrays_file = arrays_path_for(artifact)
    if not meta_file.exists() or not arrays_file.exists():
        return None
    try:
        payload: dict[str, Any] = {
            "metadata": json.loads(meta_file.read_text(encoding="utf-8"))
        }
        with np.load(arrays_file) as data:
            for name in RUNTIME_ARRAYS:
                if name in data:
                    payload[name] = data[name]
    except (OSError, ValueError):
        return None
    return payload


def export(model: Any, n_features: int, path: str | Path) -> Path:
    """Write `model` to `path` as ONNX. Raises ExportUnsupported if it cannot.

    CatBoost writes ONNX itself; everything else goes through the converter
    that knows its library. The converters are imported lazily and separately
    so that exporting a LightGBM model does not require XGBoost to be
    installed, and so this module imports cleanly in an app that has none of
    them.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    module = type(model).__module__.split(".")[0]

    if module == "catboost":
        # CatBoost's own exporter. Regression emits a `predictions` output;
        # only numerical features are supported, which holds here.
        model.save_model(str(path), format="onnx")
        return path

    try:
        from skl2onnx.common.data_types import FloatTensorType
    except ImportError as exc:  # pragma: no cover - exercised by the env, not a test
        raise ExportUnsupported(
            "skl2onnx is not installed; ONNX export is a training-time "
            "dependency (see requirements-train.txt)"
        ) from exc

    initial_types = [("input", FloatTensorType([None, n_features]))]
    try:
        if module == "lightgbm":
            from onnxmltools import convert_lightgbm

            onnx_model = convert_lightgbm(model, initial_types=initial_types)
        elif module == "xgboost":
            from onnxmltools import convert_xgboost

            onnx_model = convert_xgboost(model, initial_types=initial_types)
        else:
            from skl2onnx import convert_sklearn

            onnx_model = convert_sklearn(model, initial_types=initial_types)
    except Exception as exc:
        raise ExportUnsupported(f"could not convert {type(model).__name__}: {exc}") from exc

    path.write_bytes(onnx_model.SerializeToString())
    return path


class OnnxModel:
    """A fitted regressor loaded from ONNX. Quacks like `.predict(frame)`.

    Deliberately not a drop-in for everything a booster offers: there is no
    `feature_importances_` and no tree structure for SHAP to walk, because
    neither survives the export. The callers that want those already have
    fallbacks, and a stub that returned plausible-looking numbers would be
    worse than an absent attribute they already know how to handle.
    """

    def __init__(self, path: str | Path):
        import onnxruntime  # lazy: the import costs ~100ms and is not always needed

        self.path = Path(path)
        # One thread. This runs beside a Streamlit server and a tkinter overlay
        # on a player's machine, and a seven-feature tree ensemble is nowhere
        # near large enough for intra-op parallelism to pay for the contention.
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        # Errors only. CatBoost's exporter declares the `predictions` output as
        # rank-1 while emitting (N, 1), so the runtime warns about the shape on
        # every single call. The values are correct -- parity against the native
        # model is pinned by a test -- and `predict` reshapes, so the warning is
        # noise that would otherwise print once per priced map.
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(
            str(self.path), options, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name
        # CatBoost emits `predictions`; the sklearn-family converters emit
        # `variable`. Taking the first output rather than a name keeps this
        # working across both without a per-backend table.
        self._output = self._session.get_outputs()[0].name

    def predict(self, X: Any) -> np.ndarray:
        """Predict for a DataFrame or array. Returns a 1-D float array."""
        values = X.to_numpy() if hasattr(X, "to_numpy") else np.asarray(X)
        values = np.asarray(values, dtype=np.float32)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        raw = self._session.run([self._output], {self._input: values})[0]
        return np.asarray(raw, dtype=float).reshape(-1)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"OnnxModel({self.path.name})"
