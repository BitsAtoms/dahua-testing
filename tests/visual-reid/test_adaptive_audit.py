from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = REPOSITORY_ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from visual_reid.adaptive_audit import AdaptiveAuditStore  # noqa: E402
from visual_reid.adaptive_capture import (  # noqa: E402
    CaptureDecision,
    EvidenceState,
)
from visual_reid.adaptive_service import ProcessResult  # noqa: E402


class AdaptiveAuditTests(unittest.TestCase):
    def test_records_sanitized_decision_and_finishes_run(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.sqlite3"
            store = AdaptiveAuditStore(path)
            run_id = store.start_run(
                ["cam-b", "cam-a"], normal_fps=1, reinforce_fps=5
            )
            result = ProcessResult(
                "track-a",
                "cam-a",
                "update",
                1,
                CaptureDecision(
                    EvidenceState.WEAK,
                    True,
                    5,
                    ("too_small",),
                ),
            )
            store.save_decision(
                run_id,
                42,
                2_000_000,
                "message-a",
                result,
                actual_camera_fps=5,
            )
            store.finish_run(run_id)
            store.close()

            connection = sqlite3.connect(path)
            run = connection.execute(
                "SELECT cameras_json, stopped_at FROM adaptive_runs"
            ).fetchone()
            decision = connection.execute(
                """
                SELECT receiver_rowid, evidence_state, reinforce,
                       decision_target_fps, actual_camera_fps, reasons_json
                FROM adaptive_decisions
                """
            ).fetchone()
            connection.close()

            self.assertEqual(run[0], '["cam-a","cam-b"]')
            self.assertIsNotNone(run[1])
            self.assertEqual(decision, (42, "weak", 1, 5.0, 5.0, '["too_small"]'))

    def test_cleanup_removes_expired_decisions(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.sqlite3"
            store = AdaptiveAuditStore(path)
            run_id = store.start_run(["cam-a"], normal_fps=1, reinforce_fps=5)
            result = ProcessResult(
                "track-a",
                "cam-a",
                "end",
                0,
                CaptureDecision(EvidenceState.PENDING, False, 1, ("no_evidence",)),
            )
            store.save_decision(
                run_id,
                1,
                1_000_000,
                "message-a",
                result,
                actual_camera_fps=1,
            )

            removed = store.cleanup(datetime(2026, 9, 17, tzinfo=timezone.utc))

            self.assertEqual(removed, 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
