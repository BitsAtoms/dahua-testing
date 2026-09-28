from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "visual-reid"))

from visual_reid.rfdetr_person_detector import (  # noqa: E402
    RfDetrMediumPersonDetector, decode_rfdetr, preprocess_rfdetr,
)


class RfDetrPersonDetectorTests(unittest.TestCase):
    def test_rgb_normalization_and_shape(self):
        frame = np.array([[[0, 0, 255]]], dtype=np.uint8)  # BGR red
        tensor = preprocess_rfdetr(frame, 576, 576)
        self.assertEqual(tensor.shape, (1, 3, 576, 576))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(float(tensor[0, 0, 0, 0]), (1 - .485) / .229, places=5)
        self.assertAlmostEqual(float(tensor[0, 2, 0, 0]), (0 - .406) / .225, places=5)

    def test_person_slot_and_normalized_box(self):
        boxes = np.zeros((1, 2, 4), dtype=np.float32)
        boxes[0, 0] = [.5, .5, .5, .5]
        logits = np.full((1, 2, 91), -10., dtype=np.float32)
        logits[0, 0, 1] = 2.0  # person, official sparse COCO slot 1
        logits[0, 1, 90] = 10.0  # final slot is not treated as person/background
        result = decode_rfdetr(boxes, logits, (100, 200), .5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].box, (50., 25., 150., 75.))
        self.assertAlmostEqual(result[0].score, .880797, places=5)

    def test_named_outputs_required_even_if_shapes_look_right(self):
        runtime = Mock(input_shape=[1, 3, 576, 576],
                       output_names=["labels", "dets"],
                       output_shapes=[[1, 300, 91], [1, 300, 4]])
        self.assertIsInstance(RfDetrMediumPersonDetector(runtime), RfDetrMediumPersonDetector)
        runtime.output_names = ["output_0", "output_1"]
        with self.assertRaises(ValueError):
            RfDetrMediumPersonDetector(runtime)

    def test_detect_maps_reordered_runtime_outputs_by_name(self):
        boxes = np.array([[[.5, .5, .5, .5]]], dtype=np.float32)
        logits = np.full((1, 1, 91), -10., dtype=np.float32)
        logits[0, 0, 1] = 3.
        runtime = Mock(input_shape=[1, 3, 576, 576],
                       output_names=["labels", "dets"],
                       output_shapes=[[1, 300, 91], [1, 300, 4]])
        runtime.run.return_value = [logits, boxes]
        result = RfDetrMediumPersonDetector(runtime).detect(
            np.zeros((100, 200, 3), dtype=np.uint8), .5)
        self.assertEqual([item.box for item in result], [(50., 25., 150., 75.)])
        self.assertEqual(runtime.run.call_count, 1)

    def test_bad_shapes_and_nonfinite_outputs_rejected(self):
        with self.assertRaises(ValueError):
            decode_rfdetr(np.zeros((1, 1, 4)), np.zeros((1, 1, 90)), (100, 100), .5)
        logits = np.zeros((1, 1, 91))
        logits[0, 0, 0] = np.nan
        with self.assertRaises(ValueError):
            decode_rfdetr(np.zeros((1, 1, 4)), logits, (100, 100), .5)


if __name__ == "__main__":
    unittest.main()
