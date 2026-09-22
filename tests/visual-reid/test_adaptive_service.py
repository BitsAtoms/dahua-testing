from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.adaptive_capture import (  # noqa: E402
    AdaptiveCaptureCoordinator,
    AdaptiveCapturePolicy,
    EvidenceReservoir,
    EvidenceState,
)
from visual_reid.adaptive_service import TrackUpdateProcessor  # noqa: E402
from visual_reid.face_detection import FaceDetection, FaceRejection  # noqa: E402
from visual_reid.rtsp_buffer import RollingJpegBuffer  # noqa: E402


class AdaptiveServiceTests(unittest.TestCase):
    def test_live_update_uses_correlated_buffer_crop(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        self.assertTrue(buffer.append_image(image, 2_000_000))
        rates: list[tuple[str, float]] = []
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda camera_id, fps: rates.append((camera_id, fps)),
        )
        processor = TrackUpdateProcessor(coordinator, {"cam-a": buffer})
        update = {
            "message_id": "message-a",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "update",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
        }

        result = processor.process(update, 2_000_000)

        self.assertIsNotNone(result)
        self.assertEqual(result.accepted_observations, 1)
        self.assertEqual(result.decision.state, EvidenceState.WEAK)
        self.assertIn("face_missing", result.decision.reasons)
        retained = coordinator.reservoir.observations("track-a", "body")
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].payload["kind"], "buffer_frame")
        self.assertEqual(rates[-1], ("cam-a", 5))

    def test_buffer_crop_requires_frame_near_event_observed_time(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        buffer.append_image(image, 3_000_000)
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda _camera_id, _fps: None,
        )
        processor = TrackUpdateProcessor(coordinator, {"cam-a": buffer})
        update = {
            "message_id": "message-delayed",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "update",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
        }

        result = processor.process(update, 3_000_000)

        self.assertEqual(result.accepted_observations, 0)
        self.assertEqual(coordinator.reservoir.observations("track-a"), ())
        self.assertTrue(result.decision.reinforce)

    def test_buffer_crop_records_observed_time_alignment_contract(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        buffer.append_image(image, 2_250_000)
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda _camera_id, _fps: None,
        )
        processor = TrackUpdateProcessor(coordinator, {"cam-a": buffer})
        update = {
            "message_id": "message-aligned",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "update",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
        }

        result = processor.process(update, 3_000_000)

        self.assertEqual(result.accepted_observations, 1)
        retained = coordinator.reservoir.observations("track-a", "body")
        self.assertEqual(retained[0].payload["alignment_delta_us"], 250_000)
        self.assertEqual(
            retained[0].payload["body_validity_version"],
            "adaptive-body-observed-time.v2-live-only",
        )

    def test_terminal_update_does_not_create_buffer_crop(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        buffer.append_image(image, 2_000_000)
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda _camera_id, _fps: None,
        )
        processor = TrackUpdateProcessor(coordinator, {"cam-a": buffer})
        base = {
            "track_id": "track-a",
            "camera_id": "cam-a",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
            "quality": {"source_lifecycle": "live"},
        }
        processor.process(
            {**base, "message_id": "message-new", "phase": "new"}, 2_000_000
        )
        before = coordinator.reservoir.observations("track-a")

        result = processor.process(
            {**base, "message_id": "message-end", "phase": "end"}, 2_000_000
        )

        self.assertEqual(result.accepted_observations, 0)
        self.assertEqual(coordinator.reservoir.observations("track-a"), before)

    def test_finalized_only_update_uses_native_media_without_buffer_crop(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        buffer.append_image(image, 2_000_000)
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda _camera_id, _fps: None,
        )
        processor = TrackUpdateProcessor(coordinator, {"cam-a": buffer})
        update = {
            "message_id": "message-finalized",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "snapshot",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
            "quality": {"source_lifecycle": "finalized_only"},
        }

        result = processor.process(update, 2_000_000)

        self.assertEqual(result.accepted_observations, 0)
        self.assertEqual(coordinator.reservoir.observations("track-a"), ())

    def test_live_update_discovers_face_inside_correlated_body_crop(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")

        class FakeFaceDetector:
            def __init__(self):
                self.calls = 0

            def detect_best(self, body):
                self.calls += 1
                height, width = body.shape[:2]
                crop = body[: max(32, height // 2), : max(32, width // 2)]
                return FaceDetection(crop, 0.95, (0, 0, crop.shape[1], crop.shape[0]))

        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        buffer.append_image(image, 2_000_000)
        rates: list[tuple[str, float]] = []
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda camera_id, fps: rates.append((camera_id, fps)),
        )
        detector = FakeFaceDetector()
        processor = TrackUpdateProcessor(coordinator, {"cam-a": buffer}, detector)
        update = {
            "message_id": "message-face",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "update",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
        }

        result = processor.process(update, 2_000_000)

        self.assertEqual(result.accepted_observations, 2)
        self.assertEqual(len(coordinator.reservoir.observations("track-a", "face")), 1)
        self.assertEqual(result.decision.state, EvidenceState.STRONG)
        self.assertEqual(rates[-1], ("cam-a", 1))

        processor.process(update, 2_000_000)
        self.assertEqual(len(processor._face_cache), 1)
        self.assertEqual(detector.calls, 1)

    def test_missing_frame_requests_reinforcement(self) -> None:
        rates: list[tuple[str, float]] = []
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda camera_id, fps: rates.append((camera_id, fps)),
        )
        processor = TrackUpdateProcessor(
            coordinator,
            {"cam-a": RollingJpegBuffer()},
        )
        update = {
            "message_id": "message-a",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "new",
            "observed_at": "2026-09-17T00:00:00+00:00",
            "subject": {"confidence": 0.8},
            "geometry": None,
            "media": [],
        }

        result = processor.process(update, 2_000_000)

        self.assertEqual(result.decision.state, EvidenceState.SEEKING)
        self.assertTrue(result.decision.reinforce)
        self.assertEqual(rates[-1], ("cam-a", 5))

    def test_rejected_face_is_not_saved_as_embedding_evidence(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")

        class RejectingDetector:
            def detect_best(self, body):
                crop = body[:40, :33]
                return FaceRejection(
                    crop,
                    0.72,
                    (0, 0, 33, 40),
                    "adaptive_face_too_narrow",
                )

        image = np.indices((360, 640)).sum(axis=0) % 2 * 255
        image = np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8)
        buffer = RollingJpegBuffer(max_seconds=10, max_frames=10)
        buffer.append_image(image, 2_000_000)
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda _camera_id, _fps: None,
        )
        processor = TrackUpdateProcessor(
            coordinator,
            {"cam-a": buffer},
            RejectingDetector(),
        )
        update = {
            "message_id": "message-rejected-face",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "update",
            "observed_at": "1970-01-01T00:00:02+00:00",
            "subject": {"confidence": 0.9},
            "geometry": {
                "box": {
                    "x_min": 0.35,
                    "y_min": 0.10,
                    "x_max": 0.65,
                    "y_max": 0.90,
                }
            },
            "media": [],
        }

        result = processor.process(update, 2_000_000)

        rejected = coordinator.reservoir.observations("track-a", "face")
        self.assertEqual(len(rejected), 1)
        self.assertFalse(rejected[0].quality.usable_for_embedding)
        self.assertNotIn("crop_jpeg", rejected[0].payload)
        self.assertIn("adaptive_face_too_narrow", result.decision.reasons)
        self.assertTrue(result.decision.reinforce)

    def test_silent_active_track_expires_and_releases_camera(self) -> None:
        rates: list[tuple[str, float]] = []
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=5),
            lambda camera_id, fps: rates.append((camera_id, fps)),
        )
        processor = TrackUpdateProcessor(
            coordinator,
            {"cam-a": RollingJpegBuffer()},
        )
        update = {
            "message_id": "message-a",
            "track_id": "track-a",
            "camera_id": "cam-a",
            "phase": "new",
            "observed_at": "2026-09-17T00:00:00+00:00",
            "subject": {"confidence": 0.8},
            "geometry": None,
            "media": [],
        }
        processor.process(update, 2_000_000)

        before = processor.expire_stale(16_000_000, timeout_seconds=15)
        expired = processor.expire_stale(18_000_000, timeout_seconds=15)

        self.assertEqual(before, ())
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].phase, "timeout")
        self.assertFalse(expired[0].decision.reinforce)
        self.assertEqual(rates[-1], ("cam-a", 1))
        self.assertEqual(
            processor.expire_stale(40_000_000, timeout_seconds=15), ()
        )


if __name__ == "__main__":
    unittest.main()
