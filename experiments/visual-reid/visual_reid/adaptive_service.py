"""Project live track updates onto adaptive in-memory capture evidence."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from .adaptive_capture import (
    AdaptiveCaptureCoordinator,
    CaptureDecision,
    EvidenceObservation,
)
from .adaptive_media import ADAPTIVE_BODY_VALIDITY_VERSION
from .face_detection import FaceRejection
from .openvino_reid import body_crop
from .quality import assess_image_quality
from .rtsp_buffer import BufferedFrame, RollingJpegBuffer


MAX_BUFFER_ALIGNMENT_SECONDS = 0.4


@dataclass(frozen=True)
class ProcessResult:
    track_id: str
    camera_id: str
    phase: str
    accepted_observations: int
    decision: CaptureDecision


class TrackUpdateProcessor:
    def __init__(
        self,
        coordinator: AdaptiveCaptureCoordinator,
        buffers: dict[str, RollingJpegBuffer],
        face_detector: Any | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.buffers = buffers
        self.face_detector = face_detector
        self._face_cache: dict[tuple[object, ...], Any | None] = {}
        self._active: dict[str, bool] = {}
        self._last_received_us: dict[str, int] = {}

    def process(
        self,
        update: dict[str, Any],
        receiver_received_us: int,
    ) -> ProcessResult | None:
        camera_id = str(update.get("camera_id", ""))
        track_id = str(update.get("track_id", ""))
        phase = str(update.get("phase", ""))
        if camera_id not in self.buffers:
            return None
        if not track_id or phase not in {"new", "update", "end", "snapshot"}:
            raise ValueError("invalid track update")
        self.coordinator.register(track_id, camera_id)
        if phase in {"new", "update"}:
            self._active[track_id] = True
        elif phase == "end":
            self._active[track_id] = False
        active = self._active.get(track_id, False)
        self._last_received_us[track_id] = receiver_received_us

        observations = list(_media_observations(update, receiver_received_us))
        # End boxes can outlive the subject in a separately decoded RTSP
        # stream, while finalized-only sources such as Dahua already provide
        # event-correlated native crops. Only live lifecycle updates are safe
        # inputs for adaptive RTSP cropping.
        source_lifecycle = str(update.get("quality", {}).get("source_lifecycle", ""))
        buffered = (
            _best_buffer_observations(
                update,
                receiver_received_us,
                self.buffers[camera_id],
                self.face_detector,
                self._face_cache,
            )
            if phase in {"new", "update"} and source_lifecycle != "finalized_only"
            else []
        )
        observations.extend(buffered)
        for observation in observations:
            self.coordinator.observe(
                observation,
                active=active,
                seen_us=receiver_received_us,
            )
        decision = self.coordinator.reassess(
            track_id,
            active=active,
            seen_us=receiver_received_us,
        )
        return ProcessResult(
            track_id,
            camera_id,
            phase,
            len(observations),
            decision,
        )

    def expire_stale(
        self, now_us: int, *, timeout_seconds: float
    ) -> tuple[ProcessResult, ...]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        timeout_us = round(timeout_seconds * 1_000_000)
        expired: list[ProcessResult] = []
        for track_id, active in tuple(self._active.items()):
            last_seen = self._last_received_us.get(track_id)
            if not active or last_seen is None or now_us - last_seen <= timeout_us:
                continue
            self._active[track_id] = False
        for track_id, decision in self.coordinator.expire_stale(now_us, timeout_us):
            expired.append(
                ProcessResult(
                    track_id,
                    self.coordinator.camera_id(track_id),
                    "timeout",
                    0,
                    decision,
                )
            )
        return tuple(expired)


def load_updates_after(
    database: Path,
    rowid: int,
    *,
    limit: int = 500,
) -> list[tuple[int, int, dict[str, Any]]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not database.is_file():
        raise FileNotFoundError(f"receiver database does not exist: {database}")
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        rows = connection.execute(
            """
            SELECT rowid, receiver_received_us, payload_json
            FROM track_updates
            WHERE rowid > ?
            ORDER BY rowid
            LIMIT ?
            """,
            (rowid, limit),
        )
        return [
            (int(item[0]), int(item[1]), json.loads(item[2])) for item in rows
        ]
    finally:
        connection.close()


def latest_rowid(database: Path) -> int:
    if not database.is_file():
        raise FileNotFoundError(f"receiver database does not exist: {database}")
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        row = connection.execute("SELECT MAX(rowid) FROM track_updates").fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()


def _media_observations(
    update: dict[str, Any], receiver_received_us: int
) -> list[EvidenceObservation]:
    result: list[EvidenceObservation] = []
    for index, media in enumerate(update.get("media") or []):
        role = str(media.get("role", ""))
        if role not in {"face", "body", "snapshot"}:
            continue
        path = Path(str(media.get("path", "")))
        if not path.is_file():
            continue
        image = _read_image(path)
        if image is None:
            continue
        modality = "face" if role == "face" else "body"
        if role == "snapshot":
            geometry = update.get("geometry")
            box = geometry.get("box") if isinstance(geometry, dict) else None
            image = body_crop(image, box)
            if image.size == 0:
                continue
        confidence = update.get("subject", {}).get("confidence")
        quality = assess_image_quality(
            image,
            modality,
            detector_confidence=(
                float(confidence) if confidence is not None else None
            ),
        )
        result.append(
            EvidenceObservation(
                observation_id=f"{update['message_id']}:media:{index}:{role}",
                track_id=update["track_id"],
                camera_id=update["camera_id"],
                observed_us=_update_observed_us(update, receiver_received_us),
                quality=quality,
                payload={"kind": "retained_media", "path": str(path), "role": role},
            )
        )
    return result


def _best_buffer_observations(
    update: dict[str, Any],
    receiver_received_us: int,
    buffer: RollingJpegBuffer,
    face_detector: Any | None = None,
    face_cache: dict[tuple[object, ...], Any | None] | None = None,
) -> list[EvidenceObservation]:
    geometry = update.get("geometry")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("box"), dict):
        return []
    body_candidates: list[tuple[float, BufferedFrame, Any, bytes, dict[str, Any]]] = []
    face_candidates: list[tuple[float, BufferedFrame, Any, bytes, dict[str, Any]]] = []
    confidence = update.get("subject", {}).get("confidence")
    event_observed_us = _update_observed_us(update, receiver_received_us)
    for frame in buffer.window(
        event_observed_us,
        before_seconds=MAX_BUFFER_ALIGNMENT_SECONDS,
        after_seconds=MAX_BUFFER_ALIGNMENT_SECONDS,
    ):
        image = _decode_jpeg(frame.jpeg)
        if image is None:
            continue
        crop = body_crop(image, geometry["box"])
        if crop.size == 0:
            continue
        quality = assess_image_quality(
            crop,
            "body",
            detector_confidence=(
                float(confidence) if confidence is not None else None
            ),
        )
        encoded, body_jpeg = _encode_jpeg(crop)
        if not encoded:
            continue
        alignment_delta_us = abs(frame.observed_us - event_observed_us)
        body_candidates.append(
            (
                quality.score,
                frame,
                quality,
                body_jpeg,
                {
                    "body_validity_version": ADAPTIVE_BODY_VALIDITY_VERSION,
                    "alignment_delta_us": alignment_delta_us,
                },
            )
        )
        if face_detector is None:
            continue
        normalized_box = geometry["box"]
        box_signature = tuple(
            round(float(normalized_box[name]), 3)
            for name in ("x_min", "y_min", "x_max", "y_max")
        )
        cache_key = (str(update["track_id"]), frame.observed_us, *box_signature)
        if face_cache is not None and cache_key in face_cache:
            detection = face_cache[cache_key]
        else:
            detection = face_detector.detect_best(crop)
            if face_cache is not None:
                face_cache[cache_key] = detection
                while len(face_cache) > 256:
                    face_cache.pop(next(iter(face_cache)))
        if detection is None:
            continue
        face_quality = assess_image_quality(
            detection.crop,
            "face",
            detector_confidence=detection.confidence,
        )
        if isinstance(detection, FaceRejection):
            face_quality = replace(
                face_quality,
                usable_for_embedding=False,
                strong=False,
                reasons=tuple(
                    dict.fromkeys((*face_quality.reasons, detection.reason))
                ),
            )
            face_candidates.append(
                (
                    face_quality.score,
                    frame,
                    face_quality,
                    b"",
                    {
                        "detector_confidence": detection.confidence,
                        "detector_box": list(detection.box),
                        "rejection_reason": detection.reason,
                    },
                )
            )
            continue
        face_encoded, face_jpeg = _encode_jpeg(detection.crop)
        if face_encoded:
            face_candidates.append(
                (
                    face_quality.score,
                    frame,
                    face_quality,
                    face_jpeg,
                    {
                        "detector_confidence": detection.confidence,
                        "detector_box": list(detection.box),
                        "face_validity_version": detection.validity_version,
                    },
                )
            )
    result: list[EvidenceObservation] = []
    for modality, candidates in (
        ("body", body_candidates),
        ("face", face_candidates),
    ):
        if not candidates:
            continue
        _score, frame, quality, jpeg, metadata = max(
            candidates,
            key=lambda item: (
                item[2].usable_for_embedding,
                -abs(item[1].observed_us - event_observed_us),
                item[0],
            ),
        )
        payload = {
            "kind": "buffer_frame",
            "frame_observed_us": frame.observed_us,
            **metadata,
        }
        if jpeg:
            payload["crop_jpeg"] = jpeg
        result.append(
            EvidenceObservation(
                observation_id=(
                    f"{update['message_id']}:buffer:{frame.observed_us}:{modality}"
                ),
                track_id=update["track_id"],
                camera_id=update["camera_id"],
                observed_us=frame.observed_us,
                quality=quality,
                payload=payload,
            )
        )
    return result


def _update_observed_us(update: dict[str, Any], fallback: int) -> int:
    value = update.get("observed_at")
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return round(parsed.timestamp() * 1_000_000)


def _read_image(path: Path):
    try:
        import cv2
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install experiments/visual-reid/requirements.txt in its venv"
        ) from error
    return cv2.imread(str(path), cv2.IMREAD_COLOR)


def _decode_jpeg(data: bytes):
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install experiments/visual-reid/requirements.txt in its venv"
        ) from error
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def _encode_jpeg(image):
    try:
        import cv2
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install experiments/visual-reid/requirements.txt in its venv"
        ) from error
    encoded, output = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return bool(encoded), output.tobytes() if encoded else b""
