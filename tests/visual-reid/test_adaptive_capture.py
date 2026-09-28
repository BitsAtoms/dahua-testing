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
    EvidenceObservation,
    EvidenceReservoir,
    EvidenceState,
)
from visual_reid.quality import QualityAssessment, assess_image_quality  # noqa: E402
from visual_reid.rtsp_buffer import RollingJpegBuffer  # noqa: E402


def assessment(
    modality: str, score: float, *, usable: bool = True, strong: bool = False
) -> QualityAssessment:
    return QualityAssessment(
        modality=modality,
        score=score,
        usable_for_embedding=usable,
        strong=strong,
        width=100,
        height=160,
        brightness=120,
        contrast=40,
        sharpness=100,
        reasons=(),
    )


def observation(
    identifier: str,
    observed_us: int,
    score: float,
    *,
    track_id: str = "track-a",
    camera_id: str = "camera-a",
    modality: str = "face",
    usable: bool = True,
    strong: bool = False,
) -> EvidenceObservation:
    return EvidenceObservation(
        identifier,
        track_id,
        camera_id,
        observed_us,
        assessment(modality, score, usable=usable, strong=strong),
    )


class AdaptiveCaptureTests(unittest.TestCase):
    def test_reservoir_keeps_best_diverse_observations(self) -> None:
        reservoir = EvidenceReservoir(max_per_modality=2, diversity_seconds=1)

        self.assertTrue(reservoir.add(observation("weak", 1_100_000, 0.4)))
        self.assertTrue(reservoir.add(observation("better", 1_200_000, 0.8)))
        self.assertTrue(reservoir.add(observation("later", 2_200_000, 0.7)))
        self.assertTrue(reservoir.add(observation("latest", 3_200_000, 0.9)))

        selected = reservoir.observations("track-a", "face")
        self.assertEqual(
            [item.observation_id for item in selected], ["better", "latest"]
        )
        self.assertFalse(reservoir.add(observation("latest", 4_200_000, 1.0)))

    def test_policy_reinforces_weak_or_ambiguous_tracks(self) -> None:
        policy = AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=6)
        weak = (observation("weak", 1, 0.3, usable=False),)
        strong = (
            observation("one", 1, 0.9, strong=True),
            observation("two", 2_000_000, 0.8, strong=True),
        )

        self.assertEqual(policy.decide((), active=True).state, EvidenceState.SEEKING)
        self.assertTrue(policy.decide(weak, active=True).reinforce)
        self.assertEqual(policy.decide(strong, active=True).state, EvidenceState.STRONG)
        ambiguous = policy.decide(strong, active=True, candidate_margin=0.04)
        self.assertEqual(ambiguous.state, EvidenceState.SEEKING)
        self.assertEqual(ambiguous.target_fps, 6)
        self.assertEqual(
            policy.decide(weak, active=False).state, EvidenceState.PENDING
        )

    def test_policy_bounds_face_search_when_only_body_is_available(self) -> None:
        policy = AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=6)
        bodies = tuple(
            observation(
                f"body-{index}",
                index + 1,
                0.8,
                modality="body",
                strong=True,
            )
            for index in range(3)
        )

        searching = policy.decide(bodies[:2], active=True)
        bounded = policy.decide(bodies, active=True)

        self.assertTrue(searching.reinforce)
        self.assertIn("face_missing", searching.reasons)
        self.assertEqual(bounded.state, EvidenceState.STRONG)
        self.assertFalse(bounded.reinforce)

    def test_coordinator_changes_camera_rate_as_evidence_improves(self) -> None:
        rates: list[tuple[str, float]] = []
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(max_per_modality=3),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=6),
            lambda camera_id, fps: rates.append((camera_id, fps)),
        )

        weak = coordinator.observe(
            observation("weak", 1, 0.3, usable=False), active=True
        )
        coordinator.observe(
            observation("strong-1", 1_000_000, 0.9, strong=True),
            active=True,
        )
        strong = coordinator.observe(
            observation("strong-2", 2_000_000, 0.9, strong=True),
            active=True,
        )

        self.assertTrue(weak.reinforce)
        self.assertEqual(strong.state, EvidenceState.STRONG)
        self.assertEqual(rates[0], ("camera-a", 6))
        self.assertEqual(rates[-1], ("camera-a", 1))

    def test_camera_stays_reinforced_while_any_active_track_needs_it(self) -> None:
        rates: list[tuple[str, float]] = []
        coordinator = AdaptiveCaptureCoordinator(
            EvidenceReservoir(max_per_modality=3),
            AdaptiveCapturePolicy(normal_fps=1, reinforce_fps=6),
            lambda camera_id, fps: rates.append((camera_id, fps)),
        )
        coordinator.observe(
            observation("a-weak", 1, 0.2, usable=False, track_id="track-a"),
            active=True,
        )
        coordinator.observe(
            observation(
                "b-strong-1", 1_000_000, 0.9, strong=True, track_id="track-b"
            ),
            active=True,
        )
        coordinator.observe(
            observation(
                "b-strong-2", 2_000_000, 0.9, strong=True, track_id="track-b"
            ),
            active=True,
        )

        self.assertEqual(rates[-1], ("camera-a", 6))
        coordinator.reassess("track-a", active=False)
        self.assertEqual(rates[-1], ("camera-a", 1))

    def test_quality_detects_flat_low_information_face(self) -> None:
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("numpy is installed in the visual-reid environment")
        flat = np.full((48, 48, 3), 127, dtype=np.uint8)
        detailed = np.indices((96, 96)).sum(axis=0) % 2 * 255
        detailed = np.repeat(detailed[:, :, None], 3, axis=2).astype(np.uint8)

        weak = assess_image_quality(flat, "face")
        strong = assess_image_quality(detailed, "face")

        self.assertIn("blurred", weak.reasons)
        self.assertIn("low_contrast", weak.reasons)
        self.assertGreater(strong.score, weak.score)
        self.assertTrue(strong.strong)

    def test_ring_buffer_enforces_age_frame_and_byte_limits(self) -> None:
        buffer = RollingJpegBuffer(max_seconds=2, max_frames=2, max_bytes=12)
        jpeg = b"\xff\xd8ab\xff\xd9"

        self.assertTrue(buffer.append_jpeg(jpeg, 10, 10, 1_000_000))
        self.assertTrue(buffer.append_jpeg(jpeg, 10, 10, 2_000_000))
        self.assertTrue(buffer.append_jpeg(jpeg, 10, 10, 4_500_000))

        stats = buffer.stats()
        self.assertEqual(stats["frames"], 1)
        self.assertEqual(stats["bytes"], len(jpeg))
        self.assertEqual(
            len(buffer.window(4_500_000, before_seconds=1, after_seconds=0)), 1
        )


if __name__ == "__main__":
    unittest.main()
