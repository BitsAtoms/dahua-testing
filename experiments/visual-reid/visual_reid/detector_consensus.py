"""Shadow validation of source person boxes with a second downloaded detector."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any

from .detector_benchmark import Detection, _iou


@dataclass(frozen=True)
class ConsensusEvaluation:
    message_id: str
    track_id: str
    camera_id: str
    phase: str
    proposed_state: str
    reason: str
    evaluated: bool
    source_confidence: float | None
    source_box: tuple[float, float, float, float] | None
    candidate_boxes: tuple[tuple[float, float, float, float], ...]
    candidate_scores: tuple[float, ...]
    max_iou: float | None
    guard_threshold: float
    candidate_threshold: float
    iou_threshold: float
    latency_ms: float
    frame_width: int | None
    frame_height: int | None
    media_path: str | None
    frame_observed_us: int | None
    alignment_delta_us: int | None

    def payload(self) -> dict[str, Any]:
        result = asdict(self)
        result["mode"] = "shadow"
        result["model"] = "source_detector+yolox_tiny_consensus"
        return result


def normalized_box_pixels(
    geometry: Any, width: int, height: int
) -> tuple[float, float, float, float] | None:
    if width <= 0 or height <= 0 or not isinstance(geometry, dict):
        return None
    if geometry.get("coordinate_space") != "normalized_0_1":
        return None
    box = geometry.get("box")
    if not isinstance(box, dict):
        return None
    try:
        values = tuple(float(box[name]) for name in ("x_min", "y_min", "x_max", "y_max"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    x1, y1, x2, y2 = values
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    return x1 * width, y1 * height, x2 * width, y2 * height


def best_consensus_iou(
    source_box: tuple[float, float, float, float],
    detections: list[Detection],
) -> float:
    return max((_iou(source_box, item.box) for item in detections), default=0.0)


def derive_track_state(
    outcomes: list[str],
    *,
    min_evidence: int = 5,
    contamination_run: int = 5,
    dominant_ratio: float = 0.8,
) -> str:
    evaluated = [value for value in outcomes if value in {"eligible", "excluded"}]
    if len(evaluated) < min_evidence:
        return "provisional"
    if min_evidence <= 0 or contamination_run <= 0:
        raise ValueError("evidence windows must be positive")
    if not 0.5 < dominant_ratio <= 1:
        raise ValueError("dominant_ratio must be in (0.5, 1]")

    # Eligibility is deliberately easier to establish than exclusion: a single
    # detector miss inside the evidence window must not suppress a real person.
    # Exclusion requires an uninterrupted run so entry/exit alignment noise
    # cannot turn four early misses into a durable negative decision.
    eligible_required = math.ceil(min_evidence * dominant_ratio)
    committed: str | None = None
    current: str | None = None
    current_length = 0
    recent: list[str] = []
    for value in evaluated:
        if value == current:
            current_length += 1
        else:
            current = value
            current_length = 1
        recent.append(value)
        if len(recent) > min_evidence:
            recent.pop(0)

        if committed is None:
            if len(recent) == min_evidence and recent.count("eligible") >= eligible_required:
                committed = "eligible"
            elif value == "excluded" and current_length >= min_evidence:
                committed = "excluded"
            continue

        opposite = "excluded" if committed == "eligible" else "eligible"
        if value == opposite and current_length >= contamination_run:
            return "contaminated"

    return committed or "provisional"


class ShadowConsensusValidator:
    def __init__(
        self,
        detector: Any,
        *,
        guard_threshold: float = 0.5,
        candidate_threshold: float = 0.4,
        iou_threshold: float = 0.3,
    ) -> None:
        if not 0 <= guard_threshold <= 1 or not 0 <= candidate_threshold <= 1:
            raise ValueError("detector thresholds must be between zero and one")
        if not 0 < iou_threshold <= 1:
            raise ValueError("iou_threshold must be in (0, 1]")
        self.detector = detector
        self.guard_threshold = guard_threshold
        self.candidate_threshold = candidate_threshold
        self.iou_threshold = iou_threshold

    def evaluate(self, update: dict[str, Any]) -> ConsensusEvaluation | None:
        if update.get("phase") != "snapshot":
            return None
        if update.get("subject", {}).get("type") != "person":
            return None
        base = {
            "message_id": str(update.get("message_id", "")),
            "track_id": str(update.get("track_id", "")),
            "camera_id": str(update.get("camera_id", "")),
            "phase": str(update.get("phase", "")),
        }
        snapshot = next(
            (
                item
                for item in update.get("media", [])
                if isinstance(item, dict) and item.get("role") == "snapshot"
            ),
            None,
        )
        media_path = (
            str(snapshot.get("path", "")) if isinstance(snapshot, dict) else ""
        )
        if not media_path or not Path(media_path).is_file():
            return self._not_evaluated(base, "snapshot_unavailable", media_path or None)
        try:
            import cv2
        except ModuleNotFoundError as error:
            raise RuntimeError("OpenCV is required for detector consensus") from error
        frame = cv2.imread(media_path, cv2.IMREAD_COLOR)
        if frame is None:
            return self._not_evaluated(base, "snapshot_decode_failed", media_path)
        return self.evaluate_frame(update, frame, media_path=media_path)

    def evaluate_frame(
        self,
        update: dict[str, Any],
        frame: Any,
        *,
        media_path: str | None = None,
        frame_observed_us: int | None = None,
        alignment_delta_us: int | None = None,
    ) -> ConsensusEvaluation:
        base = {
            "message_id": str(update.get("message_id", "")),
            "track_id": str(update.get("track_id", "")),
            "camera_id": str(update.get("camera_id", "")),
            "phase": str(update.get("phase", "")),
        }
        if frame is None or len(getattr(frame, "shape", ())) < 2:
            return self._not_evaluated(base, "frame_unavailable", media_path)
        height, width = frame.shape[:2]
        source_box = normalized_box_pixels(update.get("geometry"), width, height)
        if source_box is None:
            return self._not_evaluated(
                base,
                "source_box_unavailable",
                media_path,
                width=width,
                height=height,
            )
        started = time.perf_counter()
        detections = self.detector.detect(frame, self.candidate_threshold)
        latency_ms = (time.perf_counter() - started) * 1000
        max_iou = best_consensus_iou(source_box, detections)
        raw_confidence = update.get("subject", {}).get("confidence")
        source_confidence = (
            float(raw_confidence)
            if isinstance(raw_confidence, (int, float))
            and not isinstance(raw_confidence, bool)
            else None
        )
        guard_passed = (
            source_confidence is None or source_confidence >= self.guard_threshold
        )
        accepted = guard_passed and max_iou >= self.iou_threshold
        reason = (
            "source_confidence_below_guard"
            if not guard_passed
            else (
                "detector_spatial_consensus"
                if accepted
                else "no_detector_spatial_consensus"
            )
        )
        return ConsensusEvaluation(
            **base,
            proposed_state="eligible" if accepted else "excluded",
            reason=reason,
            evaluated=True,
            source_confidence=source_confidence,
            source_box=source_box,
            candidate_boxes=tuple(item.box for item in detections),
            candidate_scores=tuple(item.score for item in detections),
            max_iou=round(max_iou, 6),
            guard_threshold=self.guard_threshold,
            candidate_threshold=self.candidate_threshold,
            iou_threshold=self.iou_threshold,
            latency_ms=round(latency_ms, 3),
            frame_width=width,
            frame_height=height,
            media_path=media_path,
            frame_observed_us=frame_observed_us,
            alignment_delta_us=alignment_delta_us,
        )

    def _not_evaluated(
        self,
        base: dict[str, str],
        reason: str,
        media_path: str | None,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> ConsensusEvaluation:
        return ConsensusEvaluation(
            **base,
            proposed_state="provisional",
            reason=reason,
            evaluated=False,
            source_confidence=None,
            source_box=None,
            candidate_boxes=(),
            candidate_scores=(),
            max_iou=None,
            guard_threshold=self.guard_threshold,
            candidate_threshold=self.candidate_threshold,
            iou_threshold=self.iou_threshold,
            latency_ms=0.0,
            frame_width=width,
            frame_height=height,
            media_path=media_path,
            frame_observed_us=None,
            alignment_delta_us=None,
        )


class ConsensusAuditStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detector_consensus_shadow (
                message_id TEXT PRIMARY KEY,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                evaluated_us INTEGER NOT NULL,
                proposed_state TEXT NOT NULL,
                max_iou REAL,
                latency_ms REAL NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detector_consensus_tracks (
                track_id TEXT PRIMARY KEY,
                camera_id TEXT NOT NULL,
                updated_us INTEGER NOT NULL,
                state TEXT NOT NULL,
                accepted_count INTEGER NOT NULL,
                rejected_count INTEGER NOT NULL,
                provisional_count INTEGER NOT NULL
            )
            """
        )
        self.connection.commit()

    def save(self, evaluation: ConsensusEvaluation, evaluated_us: int) -> dict[str, Any]:
        payload = evaluation.payload()
        payload["evaluated_at"] = datetime.fromtimestamp(
            evaluated_us / 1_000_000, timezone.utc
        ).isoformat()
        self.connection.execute(
            """
            INSERT INTO detector_consensus_shadow (
                message_id, track_id, camera_id, evaluated_us,
                proposed_state, max_iou, latency_ms, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                evaluated_us=excluded.evaluated_us,
                proposed_state=excluded.proposed_state,
                max_iou=excluded.max_iou,
                latency_ms=excluded.latency_ms,
                payload_json=excluded.payload_json
            """,
            (
                evaluation.message_id,
                evaluation.track_id,
                evaluation.camera_id,
                evaluated_us,
                evaluation.proposed_state,
                evaluation.max_iou,
                evaluation.latency_ms,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        rows = self.connection.execute(
            """
            SELECT proposed_state
            FROM detector_consensus_shadow
            WHERE track_id = ?
            ORDER BY evaluated_us, message_id
            """,
            (evaluation.track_id,),
        ).fetchall()
        outcomes = [str(row[0]) for row in rows]
        state = derive_track_state(outcomes)
        accepted = outcomes.count("eligible")
        rejected = outcomes.count("excluded")
        provisional = outcomes.count("provisional")
        self.connection.execute(
            """
            INSERT INTO detector_consensus_tracks (
                track_id, camera_id, updated_us, state,
                accepted_count, rejected_count, provisional_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                camera_id=excluded.camera_id,
                updated_us=excluded.updated_us,
                state=excluded.state,
                accepted_count=excluded.accepted_count,
                rejected_count=excluded.rejected_count,
                provisional_count=excluded.provisional_count
            """,
            (
                evaluation.track_id,
                evaluation.camera_id,
                evaluated_us,
                state,
                accepted,
                rejected,
                provisional,
            ),
        )
        self.connection.commit()
        return {
            "state": state,
            "accepted_count": accepted,
            "rejected_count": rejected,
            "provisional_count": provisional,
        }

    def cleanup(self, now: datetime | None = None, retention_days: int = 7) -> int:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        current = now or datetime.now(timezone.utc)
        cutoff = round((current - timedelta(days=retention_days)).timestamp() * 1_000_000)
        cursor = self.connection.execute(
            "DELETE FROM detector_consensus_shadow WHERE evaluated_us < ?", (cutoff,)
        )
        self.connection.execute(
            "DELETE FROM detector_consensus_tracks WHERE updated_us < ?", (cutoff,)
        )
        self.connection.commit()
        return cursor.rowcount

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT proposed_state, COUNT(*) FROM detector_consensus_shadow GROUP BY proposed_state"
        ).fetchall()
        return {str(state): int(count) for state, count in rows}

    def track_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT state, COUNT(*) FROM detector_consensus_tracks GROUP BY state"
        ).fetchall()
        return {str(state): int(count) for state, count in rows}

    def track_state(self, track_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT state, accepted_count, rejected_count, provisional_count
            FROM detector_consensus_tracks WHERE track_id = ?
            """,
            (track_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "state": str(row[0]),
            "accepted_count": int(row[1]),
            "rejected_count": int(row[2]),
            "provisional_count": int(row[3]),
        }

    def close(self) -> None:
        self.connection.close()
