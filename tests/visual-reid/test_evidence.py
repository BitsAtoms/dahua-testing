from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.evidence_store import EvidenceStore  # noqa: E402
from visual_reid.evaluator import candidate_fingerprint  # noqa: E402
from visual_reid.fusion import rank_evidence  # noqa: E402


class FusionTests(unittest.TestCase):
    def test_missing_visual_signals_reduce_coverage_without_becoming_negative(self) -> None:
        timing_only = rank_evidence(1.0, None, None, None)
        supported = rank_evidence(1.0, 0.8, None, 0.4)

        self.assertEqual(timing_only.ranking_score, 0.35)
        self.assertEqual(timing_only.visual_coverage, 0)
        self.assertGreater(supported.ranking_score, timing_only.ranking_score)
        self.assertEqual(supported.available_modalities, ("face", "color"))

    def test_store_keeps_scores_not_vectors_and_applies_seven_day_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            old = datetime(2026, 9, 1, tzinfo=timezone.utc)
            evidence = {
                "candidate_id": "candidate-1",
                "candidate_observed_at": old.isoformat(),
                "candidate_observed_us": round(old.timestamp() * 1_000_000),
                "candidate_fingerprint": "revision-1",
                "model_version": "model-v1",
                "ranking_score": 0.5,
                "visual_coverage": 0.0,
                "signals": {},
            }
            with EvidenceStore(path) as store:
                store.save(evidence)
                self.assertEqual(
                    store.current_fingerprints("model-v1"),
                    {"candidate-1": "revision-1"},
                )
                self.assertNotIn("embedding", store.get("candidate-1"))
                removed = store.cleanup(old + timedelta(days=8))
                self.assertEqual(removed, 1)
                self.assertEqual(store.count(), 0)

    def test_candidate_fingerprint_changes_with_timing_projection(self) -> None:
        candidate = {
            "candidate_id": "candidate-1",
            "timing_score": 0.5,
            "gap_seconds": 3.0,
            "observed_us": 123,
        }
        changed = {**candidate, "timing_score": 0.6}

        self.assertNotEqual(
            candidate_fingerprint(candidate), candidate_fingerprint(changed)
        )


if __name__ == "__main__":
    unittest.main()
