#!/usr/bin/env python3
"""Export the audited official RF-DETR Medium checkpoint to a local ONNX file."""

from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEIGHTS = ROOT / "models" / "rfdetr" / "rf-detr-medium.pth"
ONNX_DIR = ROOT / "models" / "rfdetr"
WEIGHTS_BYTES = 404992918
WEIGHTS_SHA256 = "749ff6071828aaffac63e204c4f4135ed3d6cdae4d702e086c360edc3b5768c8"
ONNX_BYTES = 131569304
ONNX_SHA256 = "534ca11b273449052cf83f6d6eb53c5e11585225d41c66d271004150618f7937"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if importlib.metadata.version("rfdetr") != "1.11.0":
        raise RuntimeError("export requires rfdetr==1.11.0")
    if WEIGHTS.stat().st_size != WEIGHTS_BYTES or sha256(WEIGHTS) != WEIGHTS_SHA256:
        raise RuntimeError("official RF-DETR Medium checkpoint size/hash mismatch")

    from rfdetr import RFDETRMedium

    model = RFDETRMedium(pretrain_weights=str(WEIGHTS), device="cpu")
    output = Path(model.export(output_dir=str(ONNX_DIR), format="onnx", fp16=False, verbose=False))
    if output.stat().st_size != ONNX_BYTES or sha256(output) != ONNX_SHA256:
        raise RuntimeError("exported ONNX size/hash mismatch; inspect exporter and dependencies")
    print(f"onnx={output} bytes={output.stat().st_size} sha256={sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
