"""Write the `.onnx` for an already-trained model, without retraining it.

Training writes one itself now, but an artifact fitted before that -- including
the model this project ships -- has only the pickle. Retraining to get an ONNX
file would change the model, which is the opposite of what is wanted here: the
point is to carry the *same* model over so the app can price without CatBoost,
LightGBM, XGBoost or scikit-learn installed.

Needs the library that fitted the model, because it has to unpickle it. That is
a developer machine's problem, run once; the result is what ships.

    python scripts/export_onnx.py                      # models/originator_model.pkl
    python scripts/export_onnx.py path/to/model.pkl    # somewhere else
    python scripts/export_onnx.py --check              # verify parity, write nothing
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.ml.onnx_export import (  # noqa: E402
    ExportUnsupported,
    OnnxModel,
    export,
    onnx_path_for,
    write_runtime_arrays,
)
from src.ml.train import ARTIFACT_NAME  # noqa: E402

#: Tolerance for the parity check. Export converts the leaf values to float32,
#: so the two will not be bit-identical; on the shipped CatBoost model the worst
#: row differs by ~3e-6 in the model's own target space, which survives the
#: expm1 back to chaos as a fraction of a chaos orb on a 100c map.
RTOL = 1e-5
ATOL = 1e-6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", nargs="?", help="the .pkl to export (default: the configured one)")
    parser.add_argument("--check", action="store_true", help="compare against an existing .onnx, write nothing")
    args = parser.parse_args(argv)

    artifact = Path(args.artifact) if args.artifact else load_config().model_dir / ARTIFACT_NAME
    if not artifact.exists():
        print(f"no model at {artifact}", file=sys.stderr)
        return 1

    with open(artifact, "rb") as handle:
        payload = pickle.load(handle)
    model = payload.get("model")
    if model is None:
        print(f"{artifact.name} holds no fitted model to export", file=sys.stderr)
        return 1

    columns = list(payload["metadata"]["feature_list"])
    target = onnx_path_for(artifact)

    if not args.check:
        try:
            export(model, len(columns), target)
        except ExportUnsupported as exc:
            print(f"cannot export {type(model).__name__}: {exc}", file=sys.stderr)
            return 1
        arrays = write_runtime_arrays(payload, artifact)
        print(f"wrote {target}  ({target.stat().st_size / 1e6:.2f} MB)")
        print(f"wrote {arrays}  ({arrays.stat().st_size / 1e6:.2f} MB)")
    elif not target.exists():
        print(f"no {target.name} to check", file=sys.stderr)
        return 1

    # Parity, on the data the model was fitted to. Converter and opset drift is
    # a real hazard and this is the cheapest place to catch it.
    features = payload.get("training_features")
    if features is None or not len(features):
        print("no stored training rows, so parity was not checked")
        return 0

    frame = pd.DataFrame(np.asarray(features, dtype=float), columns=columns)
    native = np.asarray(model.predict(frame), dtype=float)
    converted = OnnxModel(target).predict(frame)
    worst = float(np.max(np.abs(native - converted)))
    agree = bool(np.allclose(native, converted, rtol=RTOL, atol=ATOL))
    print(f"parity over {len(native)} rows: max abs diff {worst:.3e}  ->  {'OK' if agree else 'MISMATCH'}")
    return 0 if agree else 2


if __name__ == "__main__":
    raise SystemExit(main())
