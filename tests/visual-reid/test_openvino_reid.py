from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.openvino_reid import facenet_tensor  # noqa: E402


class _FakeCv2:
    COLOR_BGR2RGB = 1

    @staticmethod
    def cvtColor(image, _conversion):
        return image[:, :, ::-1]


class FaceNetTests(unittest.TestCase):
    def test_preprocessing_is_nhwc_rgb_and_normalized(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        image = np.zeros((160, 160, 3), dtype=np.uint8)
        image[0, 0] = [0, 127, 255]

        tensor = facenet_tensor(image, _FakeCv2, np)

        self.assertEqual(tensor.shape, (1, 160, 160, 3))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(float(tensor[0, 0, 0, 0]), 1.0)
        self.assertAlmostEqual(float(tensor[0, 0, 0, 1]), 127 / 127.5 - 1.0)
        self.assertAlmostEqual(float(tensor[0, 0, 0, 2]), -1.0)

    def test_manifest_pins_facenet_artifact(self) -> None:
        manifest = json.loads(
            (EXPERIMENT_ROOT / "model-manifest.json").read_text(encoding="utf-8")
        )
        model = next(
            item for item in manifest["models"] if item["name"] == "facenet-small-v1"
        )
        artifact = model["files"][0]

        self.assertEqual(model["license"], "Apache-2.0")
        self.assertEqual(artifact["bytes"], 93941044)
        self.assertEqual(len(artifact["sha384"]), 96)
        self.assertTrue(artifact["url"].endswith("/v1.0/facenet.tflite"))


if __name__ == "__main__":
    unittest.main()
