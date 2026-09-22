from pathlib import Path
import sys
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT))

from visual_reid.boxmot_provider import BoxMotConfig, BoxMotProvider  # noqa: E402
from visual_reid.local_tracking import (  # noqa: E402
    DetectionFrame,
    FrameDetection,
    LocalTrackUpdateKind,
    PixelBox,
)


class _Tracker:
    def __init__(self, sequences):
        self.sequences = list(sequences)
        self.calls = []
        self.was_reset = False

    def update(self, detections, image, *, timestamp_s):
        self.calls.append((detections, image, timestamp_s))
        return self.sequences.pop(0)

    def reset(self):
        self.was_reset = True


class BoxMotProviderTests(unittest.TestCase):
    @staticmethod
    def frame(index=0, *, image=object()):
        return DetectionFrame(
            camera_id="meeting",
            frame_index=index,
            observed_us=(index + 1) * 100_000,
            width=100,
            height=100,
            detections=(FrameDetection(PixelBox(10, 20, 40, 80), 0.85),),
            image=image,
        )

    def provider(self, sequences, *, max_age=1):
        self.instance = _Tracker(sequences)
        factory = lambda **_kwargs: self.instance
        dependencies = (
            np,
            {
                "botsort": factory,
                "deepocsort": factory,
                "strongsort": factory,
                "occluboost": factory,
            },
        )
        return BoxMotProvider(
            BoxMotConfig(max_age=max_age),
            _dependencies=dependencies,
        )

    def test_translates_output_and_expires_after_configured_gap(self):
        observed_row = np.asarray(
            [[10, 20, 40, 80, 7, 0.85, 0, 0]], dtype=np.float32
        )
        empty = np.empty((0, 8), dtype=np.float32)
        provider = self.provider([observed_row, empty, empty])

        observed = provider.process(self.frame(0))
        missing = provider.process(self.frame(1))
        ended = provider.process(self.frame(2))

        self.assertEqual(observed[0].local_track_id, "g0-t7")
        self.assertEqual(observed[0].kind, LocalTrackUpdateKind.OBSERVED)
        self.assertEqual(missing, ())
        self.assertEqual(ended[0].kind, LocalTrackUpdateKind.ENDED)
        self.assertAlmostEqual(self.instance.calls[0][2], 0.1)

    def test_reset_ends_known_tracks_and_resets_external_tracker(self):
        row = np.asarray([[10, 20, 40, 80, 7, 0.85, 0, 0]], dtype=np.float32)
        provider = self.provider([row])
        provider.process(self.frame(0))

        ended = provider.reset("meeting", frame_index=1, observed_us=200_000)

        self.assertEqual(ended[0].local_track_id, "g0-t7")
        self.assertTrue(self.instance.was_reset)

    def test_requires_source_frame(self):
        provider = self.provider([])
        with self.assertRaisesRegex(ValueError, "source frame"):
            provider.process(self.frame(image=None))


if __name__ == "__main__":
    unittest.main()
