from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.local_tracking import (  # noqa: E402
    DetectionFrame,
    FrameDetection,
    LocalTrackerProvider,
    LocalTrackUpdate,
    LocalTrackUpdateKind,
    PixelBox,
)


class _FakeProvider:
    provider_id = "fake:v1"

    def process(self, frame: DetectionFrame) -> tuple[LocalTrackUpdate, ...]:
        return tuple(
            LocalTrackUpdate(
                camera_id=frame.camera_id,
                local_track_id=f"track-{index}",
                frame_index=frame.frame_index,
                observed_us=frame.observed_us,
                kind=LocalTrackUpdateKind.OBSERVED,
                box=detection.box,
                confidence=detection.confidence,
            )
            for index, detection in enumerate(frame.detections)
        )

    def reset(
        self, camera_id: str, *, frame_index: int, observed_us: int
    ) -> tuple[LocalTrackUpdate, ...]:
        return ()


class LocalTrackingContractTests(unittest.TestCase):
    def test_provider_contract_is_runtime_checkable(self) -> None:
        self.assertIsInstance(_FakeProvider(), LocalTrackerProvider)

    def test_processes_source_neutral_detection_frame(self) -> None:
        box = PixelBox(10, 20, 50, 100)
        frame = DetectionFrame(
            camera_id="meetings",
            frame_index=7,
            observed_us=1_000_000,
            width=1920,
            height=1080,
            detections=(FrameDetection(box, 0.9),),
        )

        updates = _FakeProvider().process(frame)

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].local_track_id, "track-0")
        self.assertEqual(updates[0].kind, LocalTrackUpdateKind.OBSERVED)

    def test_rejects_invalid_or_out_of_frame_boxes(self) -> None:
        with self.assertRaises(ValueError):
            PixelBox(10, 10, 5, 20)
        with self.assertRaises(ValueError):
            DetectionFrame(
                camera_id="meetings",
                frame_index=0,
                observed_us=1,
                width=100,
                height=100,
                detections=(FrameDetection(PixelBox(0, 0, 101, 50), 0.8),),
            )

    def test_ended_update_does_not_carry_current_detection(self) -> None:
        ended = LocalTrackUpdate(
            camera_id="meetings",
            local_track_id="track-1",
            frame_index=10,
            observed_us=2_000_000,
            kind=LocalTrackUpdateKind.ENDED,
        )
        self.assertIsNone(ended.box)
        with self.assertRaises(ValueError):
            LocalTrackUpdate(
                camera_id="meetings",
                local_track_id="track-1",
                frame_index=10,
                observed_us=2_000_000,
                kind=LocalTrackUpdateKind.ENDED,
                box=PixelBox(1, 1, 2, 2),
            )


if __name__ == "__main__":
    unittest.main()
