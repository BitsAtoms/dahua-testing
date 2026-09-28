from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "visual-reid"))

from visual_reid.onnx_person_detector import (  # noqa: E402
    ProviderError, decode_yolox, preprocess_yolox, select_provider, verify_profile,
)


class OnnxPersonDetectorTests(unittest.TestCase):
    def test_letterbox_preserves_bgr_and_pads_bottom_right(self):
        frame = np.zeros((40, 80, 3), dtype=np.uint8)
        frame[:, :] = (3, 7, 11)
        tensor, ratio = preprocess_yolox(frame, 64, 64)
        self.assertEqual(tensor.shape, (1, 3, 64, 64))
        self.assertEqual(ratio, .8)
        np.testing.assert_array_equal(tensor[0, :, 0, 0], [3, 7, 11])
        np.testing.assert_array_equal(tensor[0, :, 63, 63], [114, 114, 114])

    def test_yolox_decode_person_only_and_unletterbox(self):
        rows = np.zeros((1, 84, 85), dtype=np.float32)  # 64x64: 8²+4²+2²
        rows[0, 0, :6] = [2, 2, 0, 0, .9, .8]
        rows[0, 1, 4] = .9
        rows[0, 1, 6] = .9  # a non-person COCO class
        result = decode_yolox(rows, (40, 80), .8, .4, (64, 64))
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].score, .72, places=5)
        self.assertEqual(result[0].box, (15., 15., 25., 25.))

    def test_output_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_yolox(np.zeros((1, 1, 85)), (64, 64), 1, .4, (64, 64))

    def test_explicit_provider_device_selection(self):
        ort = Mock()
        ort.get_ep_devices.return_value = [Mock(ep_name="CPUExecutionProvider"),
                                           Mock(ep_name="DmlExecutionProvider")]
        options = Mock()
        self.assertEqual(select_provider(ort, options, "cpu", None), ["CPUExecutionProvider"])
        select_provider(ort, options, "directml", 0)
        options.add_provider_for_devices.assert_called_once()
        with self.assertRaises(ProviderError):
            select_provider(ort, options, "directml", None)
        with self.assertRaises(ProviderError):
            select_provider(ort, options, "directml", 1)
        with self.assertRaises(ProviderError):
            select_provider(ort, options, "cpu", 0)

    def test_cpu_fallback_rejected_from_node_profile(self):
        directml = {"args": {"provider": "DmlExecutionProvider"}}
        cpu = {"args": {"provider": "CPUExecutionProvider"}}
        self.assertEqual(verify_profile([directml], "DmlExecutionProvider")["cpu_fallback"], False)
        with self.assertRaises(ProviderError):
            verify_profile([directml, cpu], "DmlExecutionProvider")
        with self.assertRaises(ProviderError):
            verify_profile([cpu], "DmlExecutionProvider")


if __name__ == "__main__":
    unittest.main()
