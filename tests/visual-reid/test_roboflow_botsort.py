from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.local_tracking import (  # noqa: E402
    DetectionFrame,
    FrameDetection,
    LocalTrackUpdateKind,
    PixelBox,
)
from visual_reid.roboflow_botsort import (  # noqa: E402
    RoboflowBoTSORTConfig,
    RoboflowBoTSORTProvider,
)


TRACKERS_AVAILABLE = importlib.util.find_spec("trackers") is not None


@unittest.skipUnless(TRACKERS_AVAILABLE, "isolated tracker environment required")
class RoboflowBoTSORTProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = RoboflowBoTSORTProvider(
            RoboflowBoTSORTConfig(
                frame_rate=5,
                lost_track_buffer=6,
                track_activation_threshold=0.5,
                minimum_consecutive_frames=1,
                high_conf_det_threshold=0.5,
                enable_cmc=False,
            )
        )

    def _frame(
        self, frame_index: int, detections: tuple[FrameDetection, ...]
    ) -> DetectionFrame:
        return DetectionFrame(
            camera_id="meetings",
            frame_index=frame_index,
            observed_us=(frame_index + 1) * 200_000,
            width=640,
            height=480,
            detections=detections,
        )

    def test_preserves_id_and_emits_end_after_expiry(self) -> None:
        person = FrameDetection(PixelBox(10, 10, 60, 120), 0.9)
        first = self.provider.process(self._frame(0, (person,)))
        second = self.provider.process(self._frame(1, (person,)))
        self.assertEqual(self.provider.process(self._frame(2, ())), ())
        ended = self.provider.process(self._frame(3, ()))

        self.assertEqual(first[0].local_track_id, "g0-t0")
        self.assertEqual(second[0].local_track_id, "g0-t0")
        self.assertEqual(ended[0].kind, LocalTrackUpdateKind.ENDED)
        self.assertEqual(ended[0].local_track_id, "g0-t0")

    def test_reset_ends_tracks_and_prevents_id_reuse(self) -> None:
        person = FrameDetection(PixelBox(10, 10, 60, 120), 0.9)
        self.provider.process(self._frame(0, (person,)))

        ended = self.provider.reset(
            "meetings", frame_index=1, observed_us=400_000
        )
        restarted = self.provider.process(self._frame(2, (person,)))

        self.assertEqual(ended[0].local_track_id, "g0-t0")
        self.assertEqual(restarted[0].local_track_id, "g1-t0")


if __name__ == "__main__":
    unittest.main()
