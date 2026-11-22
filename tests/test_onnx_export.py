"""ONNX export: the app must price identically without the training libraries.

The whole point of exporting is that the shipped app carries a ~13 MB runtime
instead of CatBoost, LightGBM, XGBoost and scikit-learn. That trade is only
honest if the exported model answers the same as the one that was fitted, so
the parity check is the load-bearing test here -- converter and opset drift is
a real hazard and this is the cheapest place to catch it.

Exact equality is not the bar and never can be: export converts leaf values to
float32. The bar is that the difference is far below anything that could change
a price shown to a user.
"""
from __future__ import annotations

import pickle

import numpy as np
import pandas as pd
import pytest

from src.ml.models import available_backends, build, fit
from src.ml.onnx_export import ExportUnsupported, OnnxModel, export, onnx_path_for

pytest.importorskip("onnxruntime")

#: Matches scripts/export_onnx.py. Float32 leaves, so ~1e-6 on these models.
RTOL = 1e-5
ATOL = 1e-6

COLUMNS = ["quantity", "rarity", "pack_size", "more_currency"]


def _training_data(rows: int = 240, seed: int = 11):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(
        rng.integers(0, 150, size=(rows, len(COLUMNS))).astype(float), columns=COLUMNS
    )
    # A signal worth fitting, so the trees actually branch and the comparison
    # is not two constant predictors agreeing trivially.
    y = (
        0.9 * X["quantity"]
        + 0.4 * X["rarity"]
        + 2.0 * X["pack_size"]
        + 0.2 * X["more_currency"]
        + rng.normal(0, 3, size=rows)
    )
    return X, y.to_numpy()


def _fitted(backend: str):
    X, y = _training_data()
    model = build(backend, {"iterations": 40, "n_estimators": 40, "depth": 4, "verbose": 0})
    fit(model, X, y)
    return model, X


# -- the core claim ---------------------------------------------------------
@pytest.mark.parametrize("backend", [b for b in available_backends() if b != "linear"])
def test_an_exported_model_predicts_what_the_fitted_one_predicts(backend, tmp_path):
    """Every backend this install can fit, through ONNX and back."""
    model, X = _fitted(backend)
    try:
        path = export(model, len(COLUMNS), tmp_path / "m.onnx")
    except ExportUnsupported as exc:
        pytest.skip(f"{backend} cannot be exported here: {exc}")

    native = np.asarray(model.predict(X), dtype=float)
    converted = OnnxModel(path).predict(X)

    assert converted.shape == native.shape
    assert np.allclose(native, converted, rtol=RTOL, atol=ATOL), (
        f"{backend}: worst row differs by {np.max(np.abs(native - converted)):.3e}"
    )


def test_the_shipped_model_matches_its_own_export():
    """The artifact users actually price with, not a fixture built for the test.

    This is the one that would catch a converter or opset change breaking the
    model that ships, which no amount of testing a freshly fitted toy model
    would notice.
    """
    from src.config import load_config
    from src.ml.train import ARTIFACT_NAME

    artifact = load_config().model_dir / ARTIFACT_NAME
    if not artifact.exists():
        pytest.skip("no trained model in this checkout")
    onnx_file = onnx_path_for(artifact)
    if not onnx_file.exists():
        pytest.skip("no exported model beside it; run scripts/export_onnx.py")

    with open(artifact, "rb") as handle:
        payload = pickle.load(handle)
    if payload.get("model") is None:
        pytest.skip("artifact holds no fitted model to compare against")
    rows = payload.get("training_features")
    if rows is None or not len(rows):
        pytest.skip("artifact stored no training rows")

    columns = list(payload["metadata"]["feature_list"])
    frame = pd.DataFrame(np.asarray(rows, dtype=float), columns=columns)
    native = np.asarray(payload["model"].predict(frame), dtype=float)
    converted = OnnxModel(onnx_file).predict(frame)
    assert np.allclose(native, converted, rtol=RTOL, atol=ATOL), (
        f"shipped model and its export disagree by up to "
        f"{np.max(np.abs(native - converted)):.3e}"
    )


# -- the shape of the thing -------------------------------------------------
def test_predict_takes_a_frame_or_an_array_and_returns_one_dimension(tmp_path):
    """Callers pass a DataFrame; `explain` passes single rows. Both, flat out.

    CatBoost's exporter declares a rank-1 output and then emits (N, 1), so
    without the reshape this returns a column and every caller that indexes
    `[0]` silently gets an array instead of a float.
    """
    backend = available_backends()[0]
    model, X = _fitted(backend)
    try:
        path = export(model, len(COLUMNS), tmp_path / "m.onnx")
    except ExportUnsupported as exc:
        pytest.skip(str(exc))
    loaded = OnnxModel(path)

    assert loaded.predict(X).ndim == 1
    assert loaded.predict(X.to_numpy()).ndim == 1
    one = loaded.predict(X.iloc[[0]])
    assert one.shape == (1,)
    assert isinstance(float(one[0]), float)


def test_a_single_flat_row_is_treated_as_one_row_not_one_column(tmp_path):
    backend = available_backends()[0]
    model, X = _fitted(backend)
    try:
        path = export(model, len(COLUMNS), tmp_path / "m.onnx")
    except ExportUnsupported as exc:
        pytest.skip(str(exc))
    loaded = OnnxModel(path)
    assert loaded.predict(X.to_numpy()[0]).shape == (1,)


# -- which model the Predictor actually uses --------------------------------
def _payload(model, X, y):
    return {
        "model": model,
        "metadata": {
            "feature_list": list(COLUMNS),
            "core_features": [],
            "target": "raw",
            "model_version": "test",
        },
        "training_features": X.to_numpy(dtype=float),
        "training_prices": y,
    }


def _predictor_from(tmp_path, *, with_onnx: bool, with_pickle_model: bool):
    """Write an artifact the way training does, then load it the way the app does."""
    from src.ml.predict import Predictor

    backend = available_backends()[0]
    X, y = _training_data()
    model = build(backend, {"iterations": 40, "n_estimators": 40, "depth": 4, "verbose": 0})
    fit(model, X, y)

    artifact = tmp_path / "originator_model.pkl"
    payload = _payload(model, X, y)
    if with_onnx:
        export(model, len(COLUMNS), onnx_path_for(artifact))
    if not with_pickle_model:
        payload.pop("model")
    with open(artifact, "wb") as handle:
        pickle.dump(payload, handle)
    return Predictor.load(path=artifact), model, X


def test_the_predictor_prefers_the_exported_model_when_one_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.ml.predict.FEATURE_NAMES", tuple(COLUMNS), raising=False
    )
    monkeypatch.setattr("src.ml.features.FEATURE_NAMES", tuple(COLUMNS), raising=False)
    predictor, _, _ = _predictor_from(tmp_path, with_onnx=True, with_pickle_model=True)
    assert isinstance(predictor.model, OnnxModel), (
        "the pickled model was used even though an export was sitting beside it"
    )


def test_an_artifact_with_no_export_still_loads_from_the_pickle(tmp_path, monkeypatch):
    """Models trained before export existed must keep working untouched."""
    monkeypatch.setattr("src.ml.predict.FEATURE_NAMES", tuple(COLUMNS), raising=False)
    monkeypatch.setattr("src.ml.features.FEATURE_NAMES", tuple(COLUMNS), raising=False)
    predictor, model, _ = _predictor_from(tmp_path, with_onnx=False, with_pickle_model=True)
    assert not isinstance(predictor.model, OnnxModel)
    assert type(predictor.model) is type(model)


def test_an_artifact_with_neither_says_so_rather_than_failing_later(tmp_path, monkeypatch):
    """The failure has to name the fix, not surface as an AttributeError."""
    from src.ml.predict import ModelNotTrained

    monkeypatch.setattr("src.ml.predict.FEATURE_NAMES", tuple(COLUMNS), raising=False)
    monkeypatch.setattr("src.ml.features.FEATURE_NAMES", tuple(COLUMNS), raising=False)
    with pytest.raises(ModelNotTrained, match="retrain|Retrain"):
        _predictor_from(tmp_path, with_onnx=False, with_pickle_model=False)


# -- the library-free artifact set ------------------------------------------
def test_a_model_loads_with_no_pickle_on_disk_at_all(tmp_path, monkeypatch):
    """The point of the whole exercise.

    An install with no boosting library cannot even *read* a pickle that holds
    a fitted CatBoost object -- unpickling imports the class. So the shipped
    artifact set has to be `.onnx` plus `.npz` plus `.json`, with the pickle
    absent, and this pins that it loads and prices without it.
    """
    import json

    from src.ml.onnx_export import metadata_path_for, write_runtime_arrays
    from src.ml.predict import Predictor

    monkeypatch.setattr("src.ml.predict.FEATURE_NAMES", tuple(COLUMNS), raising=False)
    monkeypatch.setattr("src.ml.features.FEATURE_NAMES", tuple(COLUMNS), raising=False)

    backend = available_backends()[0]
    X, y = _training_data()
    model = build(backend, {"iterations": 40, "n_estimators": 40, "depth": 4, "verbose": 0})
    fit(model, X, y)

    artifact = tmp_path / "originator_model.pkl"
    payload = _payload(model, X, y)
    export(model, len(COLUMNS), onnx_path_for(artifact))
    write_runtime_arrays(payload, artifact)
    metadata_path_for(artifact).write_text(json.dumps(payload["metadata"]), encoding="utf-8")

    assert not artifact.exists(), "this test is only meaningful with no pickle written"

    predictor = Predictor.load(path=artifact)
    assert isinstance(predictor.model, OnnxModel)
    # The arrays came back, which is what confidence scoring and the
    # nearest-neighbour comparables need.
    assert predictor.training_features.shape == X.shape
    assert predictor.training_prices.shape == y.shape
    assert np.allclose(predictor.model.predict(X), model.predict(X), rtol=RTOL, atol=ATOL)


def test_training_without_a_backend_explains_itself(monkeypatch, tmp_path):
    """A player's install can price but cannot train, and must say which.

    Caught as `NotEnoughData` on purpose: the Settings button, the Market Data
    retrain, passive collection and the CLI all already handle that and print
    the message, so this reaches the user as text rather than a red traceback
    about an import nobody asked for.
    """
    from src.config import load_config
    from src.ml import models as backends_module
    from src.ml.train import NotEnoughData, TrainingUnavailable, train

    monkeypatch.setattr(backends_module, "available_backends", lambda: [])

    with pytest.raises(NotEnoughData) as caught:
        train(load_config(), log=lambda *a, **k: None)

    assert isinstance(caught.value, TrainingUnavailable)
    message = str(caught.value)
    assert "requirements-train.txt" in message, "the message must name the fix"
    assert "Pricing keeps working" in message, "and must say what still works"


def test_runtime_arrays_round_trip_without_the_pickle(tmp_path):
    from src.ml.onnx_export import RUNTIME_ARRAYS, load_runtime_payload, write_runtime_arrays
    import json

    artifact = tmp_path / "m.pkl"
    original = {
        "training_features": np.arange(12, dtype=float).reshape(4, 3),
        "training_prices": np.array([1.0, 2.0, 3.0, 4.0]),
        "training_times": np.array([10.0, 20.0, 30.0, 40.0]),
    }
    write_runtime_arrays(original, artifact)
    (artifact.with_suffix(".json")).write_text(json.dumps({"feature_list": ["a"]}), encoding="utf-8")

    back = load_runtime_payload(artifact)
    assert back is not None
    for name in RUNTIME_ARRAYS:
        assert np.array_equal(back[name], original[name]), name


def test_an_incomplete_runtime_set_falls_back_rather_than_half_loading(tmp_path):
    """Missing either sidecar means the pickle path, not a predictor with no data."""
    import json

    from src.ml.onnx_export import load_runtime_payload, write_runtime_arrays

    artifact = tmp_path / "m.pkl"
    assert load_runtime_payload(artifact) is None, "nothing on disk"

    write_runtime_arrays({"training_features": np.zeros((2, 2))}, artifact)
    assert load_runtime_payload(artifact) is None, "arrays without metadata"

    artifact.with_suffix(".json").write_text(json.dumps({"feature_list": []}), encoding="utf-8")
    assert load_runtime_payload(artifact) is not None, "both present"
