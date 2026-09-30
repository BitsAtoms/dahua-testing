#!/usr/bin/env python3
"""Export RF-DETR Medium for Frigate's `rfdetr` model type.

Frigate feeds float models RGB pixels divided by 255 and applies no mean/std
normalization. The official RF-DETR ONNX graph expects ImageNet-normalized
input, so this script exports the audited checkpoint at 320x320 (the size
Frigate documents) and prepends the normalization to the graph. The result is
verified against the unwrapped export fed with externally normalized input.

Run with the pinned RF-DETR export environment of the visual-reid experiment:

  experiments\\visual-reid\\.venv-rfdetr-export\\Scripts\\python.exe \\
    experiments\\frigate-zmq-detector\\export_rfdetr_frigate.py
"""

from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path
import sys
import tempfile

import numpy as np
import onnx
from onnx import TensorProto, helper
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY / "experiments" / "visual-reid"))

from export_rfdetr_medium import WEIGHTS, WEIGHTS_BYTES, WEIGHTS_SHA256, sha256  # noqa: E402

RESOLUTION = 320
OUTPUT = ROOT / "models" / f"rfdetr-medium-{RESOLUTION}-frigate.onnx"
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
TOLERANCE = 1e-3


def export_raw(directory: Path) -> Path:
    from rfdetr import RFDETRMedium

    model = RFDETRMedium(pretrain_weights=str(WEIGHTS), device="cpu", resolution=RESOLUTION)
    return Path(model.export(output_dir=str(directory), format="onnx", fp16=False, verbose=False))


def prepend_normalization(raw: onnx.ModelProto) -> onnx.ModelProto:
    graph = raw.graph
    if len(graph.input) != 1:
        raise RuntimeError(f"expected one graph input, found {len(graph.input)}")
    original = graph.input[0]
    dims = [d.dim_value for d in original.type.tensor_type.shape.dim]
    if dims != [1, 3, RESOLUTION, RESOLUTION]:
        raise RuntimeError(f"unexpected RF-DETR input shape {dims}")

    external_name = original.name
    internal_name = f"{external_name}_imagenet_normalized"
    for node in graph.node:
        node.input[:] = [internal_name if name == external_name else name for name in node.input]

    mean = helper.make_tensor("rfdetr_mean", TensorProto.FLOAT, [1, 3, 1, 1], MEAN.tolist())
    std = helper.make_tensor("rfdetr_std", TensorProto.FLOAT, [1, 3, 1, 1], STD.tolist())
    graph.initializer.extend([mean, std])
    centered = f"{external_name}_centered"
    normalization = [
        helper.make_node("Sub", [external_name, "rfdetr_mean"], [centered], name="frigate_input_sub_mean"),
        helper.make_node("Div", [centered, "rfdetr_std"], [internal_name], name="frigate_input_div_std"),
    ]
    nodes = normalization + list(graph.node)
    del graph.node[:]
    graph.node.extend(nodes)
    onnx.checker.check_model(raw)
    return raw


def verify(raw_path: Path, wrapped_path: Path) -> float:
    rng = np.random.default_rng(20260930)
    pixels = rng.random((1, 3, RESOLUTION, RESOLUTION), dtype=np.float32)
    normalized = ((pixels - MEAN[None, :, None, None]) / STD[None, :, None, None]).astype(np.float32)

    raw = ort.InferenceSession(str(raw_path), providers=["CPUExecutionProvider"])
    wrapped = ort.InferenceSession(str(wrapped_path), providers=["CPUExecutionProvider"])
    expected = raw.run(None, {raw.get_inputs()[0].name: normalized})
    actual = wrapped.run(None, {wrapped.get_inputs()[0].name: pixels})
    names = [output.name for output in wrapped.get_outputs()]
    if names != ["dets", "labels"]:
        raise RuntimeError(f"unexpected output names {names}")
    return max(float(np.max(np.abs(a - e))) for a, e in zip(actual, expected))


def main() -> int:
    if importlib.metadata.version("rfdetr") != "1.11.0":
        raise RuntimeError("export requires rfdetr==1.11.0")
    if WEIGHTS.stat().st_size != WEIGHTS_BYTES or sha256(WEIGHTS) != WEIGHTS_SHA256:
        raise RuntimeError("official RF-DETR Medium checkpoint size/hash mismatch")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        raw_path = export_raw(Path(directory))
        wrapped = prepend_normalization(onnx.load(str(raw_path)))
        onnx.save(wrapped, str(OUTPUT))
        difference = verify(raw_path, OUTPUT)

    if difference > TOLERANCE:
        OUTPUT.unlink()
        raise RuntimeError(f"wrapped model differs from reference by {difference}")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"onnx={OUTPUT} bytes={OUTPUT.stat().st_size} sha256={digest} max_abs_diff={difference:.2e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
