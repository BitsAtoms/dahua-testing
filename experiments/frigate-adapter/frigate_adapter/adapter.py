"""Translate Frigate MQTT events to the common tracking contract."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any


FrameSize = tuple[int, int]
_PHASES = {"new", "update", "end"}
_ELIGIBILITY_STATES = {"eligible", "excluded", "contaminated"}


class FrigateEventAdapter:
    """Project ``frigate/events`` payloads onto ``track_update.v1``.

    Args:
        camera_frame_sizes: Detect-stream sizes as ``camera: (width, height)``.
        instance_id: Stable name for the Frigate installation.
        labels: Object labels accepted by this tracking pipeline.
    """

    def __init__(
        self,
        camera_frame_sizes: Mapping[str, FrameSize],
        instance_id: str = "frigate",
        labels: frozenset[str] = frozenset({"person"}),
        classification_policy: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        if not instance_id:
            raise ValueError("instance_id is required")
        self._frame_sizes = dict(camera_frame_sizes)
        self._instance_id = instance_id
        self._labels = labels
        self._classification_policy = _validate_classification_policy(
            classification_policy or {}
        )

    def adapt(
        self,
        payload: dict[str, Any],
        published_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one common update, or ``None`` for an ignored label."""
        phase = payload.get("type")
        if phase not in _PHASES:
            raise ValueError("Frigate event type must be new, update, or end")

        after = payload.get("after")
        if not isinstance(after, dict):
            raise ValueError("Frigate event after payload is required")
        event_id = _required_string(after, "id")
        camera_id = _required_string(after, "camera")
        label = _required_string(after, "label")
        if label not in self._labels or after.get("false_positive") is True:
            return None

        event_timestamp = _event_timestamp(phase, after)
        sequence = round(event_timestamp * 1_000_000)
        track_id = f"frigate:{self._instance_id}:{camera_id}:{event_id}"
        source_message_id = f"{event_id}:{phase}:{sequence}"
        geometry, geometry_issue = self._geometry(camera_id, after.get("box"))
        confidence = _confidence(after)
        quality_status = "complete" if geometry_issue is None else "partial"

        return {
            "schema_version": "track_update.v1",
            "message_id": f"track-update:{track_id}:{phase}:{sequence}",
            "track_id": track_id,
            "source": {"type": "frigate", "instance_id": self._instance_id},
            "camera_id": camera_id,
            "phase": phase,
            "sequence": sequence,
            "observed_at": _timestamp_to_iso(event_timestamp),
            "published_at": published_at or datetime.now(timezone.utc).isoformat(),
            "subject": {
                "type": label,
                "local_track_id": event_id,
                "confidence": confidence,
            },
            "geometry": geometry,
            "zones": {
                "current": _string_list(after.get("current_zones")),
                "entered": _string_list(after.get("entered_zones")),
            },
            "attributes": {
                "stationary": bool(after.get("stationary")),
                "track_eligibility": {
                    "state": (
                        "provisional"
                        if self._classification_policy
                        else "eligible"
                    ),
                    "reason": (
                        "awaiting_source_classification"
                        if self._classification_policy
                        else "no_source_classification_policy"
                    ),
                },
            },
            "media": [],
            "quality": {
                "status": quality_status,
                "source_lifecycle": "live",
                "issues": [] if geometry_issue is None else [geometry_issue],
            },
            "source_ref": {
                "event_id": event_id,
                "message_id": source_message_id,
            },
        }

    def adapt_classification(
        self,
        payload: dict[str, Any],
        published_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Project a configured Frigate classification onto an existing track."""
        if payload.get("type") != "classification":
            return None
        event_id = _required_string(payload, "id")
        camera_id = _required_string(payload, "camera")
        model = _required_string(payload, "model")
        policy = self._classification_policy.get(model)
        if policy is None:
            return None

        timestamp = payload.get("timestamp")
        if (
            not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
            or timestamp <= 0
        ):
            raise ValueError("Frigate classification timestamp is required")
        score = payload.get("score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= float(score) <= 1
        ):
            raise ValueError("Frigate classification score must be between 0 and 1")
        labels = [
            value
            for value in (payload.get("attribute"), payload.get("sub_label"))
            if isinstance(value, str) and value
        ]
        if len(labels) != 1:
            raise ValueError(
                "Frigate classification requires one attribute or sub_label"
            )
        label = labels[0]
        eligibility = policy.get(label, "provisional")
        sequence = round(float(timestamp) * 1_000_000)
        track_id = f"frigate:{self._instance_id}:{camera_id}:{event_id}"
        fingerprint = hashlib.sha256(
            f"{event_id}\0{model}\0{label}\0{sequence}".encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": "track_update.v1",
            "message_id": f"track-update:{track_id}:classification:{fingerprint}",
            "track_id": track_id,
            "source": {"type": "frigate", "instance_id": self._instance_id},
            "camera_id": camera_id,
            "phase": "update",
            "sequence": sequence,
            "observed_at": _timestamp_to_iso(float(timestamp)),
            "published_at": published_at or datetime.now(timezone.utc).isoformat(),
            "subject": {
                "type": "person",
                "local_track_id": event_id,
                "confidence": None,
            },
            "geometry": None,
            "zones": {"current": [], "entered": []},
            "attributes": {
                "source_classification": {
                    "provider": "frigate",
                    "model": model,
                    "label": label,
                    "score": round(float(score), 6),
                },
                "track_eligibility": {
                    "state": eligibility,
                    "reason": "source_object_classification",
                    "model": model,
                    "label": label,
                    "score": round(float(score), 6),
                },
            },
            "media": [],
            "quality": {
                "status": "partial",
                "source_lifecycle": "live",
                "issues": ["classification_enrichment_only"],
            },
            "source_ref": {
                "event_id": event_id,
                "message_id": f"{event_id}:classification:{fingerprint}",
            },
        }

    def _geometry(
        self, camera_id: str, box: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        frame_size = self._frame_sizes.get(camera_id)
        if frame_size is None:
            return None, "missing_camera_frame_size"
        width, height = frame_size
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid frame size for camera {camera_id}")
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in box
            )
        ):
            return None, "invalid_bounding_box"

        x_min, y_min, x_max, y_max = (
            _normalized(box[0], width),
            _normalized(box[1], height),
            _normalized(box[2], width),
            _normalized(box[3], height),
        )
        return (
            {
                "coordinate_space": "normalized_0_1",
                "box": {
                    "x_min": x_min,
                    "y_min": y_min,
                    "x_max": x_max,
                    "y_max": y_max,
                },
                "center": {
                    "x": round((x_min + x_max) / 2, 6),
                    "y": round((y_min + y_max) / 2, 6),
                },
            },
            None,
        )

    def snapshot_update(
        self,
        lifecycle_update: dict[str, Any],
        snapshot_path: Path,
        snapshot_timestamp: float,
        face_paths: list[Path] | None = None,
        published_at: str | None = None,
        snapshot_box: Any = None,
    ) -> dict[str, Any]:
        """Return a correlated snapshot message for an existing track."""
        if lifecycle_update.get("source", {}).get("type") != "frigate":
            raise ValueError("snapshot lifecycle update must come from Frigate")
        if snapshot_timestamp <= 0:
            raise ValueError("snapshot timestamp is required")

        update = deepcopy(lifecycle_update)
        sequence = round(snapshot_timestamp * 1_000_000)
        event_id = _required_string(update["source_ref"], "event_id")
        update["attributes"].pop("track_eligibility", None)
        update["attributes"].pop("source_classification", None)
        if snapshot_box is not None:
            geometry, geometry_issue = self._geometry(
                str(update["camera_id"]), snapshot_box
            )
            update["geometry"] = geometry
            update["quality"] = {
                "status": "complete" if geometry_issue is None else "partial",
                "source_lifecycle": "live",
                "issues": [] if geometry_issue is None else [geometry_issue],
            }
        media = [
            {
                "role": "snapshot",
                "content_type": "image/jpeg",
                "path": str(snapshot_path.resolve()),
            }
        ]
        media.extend(
            {
                "role": "face",
                "content_type": "image/webp",
                "path": str(path.resolve()),
            }
            for path in (face_paths or [])
        )
        update.update(
            phase="snapshot",
            sequence=sequence,
            observed_at=_timestamp_to_iso(snapshot_timestamp),
            published_at=published_at or datetime.now(timezone.utc).isoformat(),
            message_id=f"track-update:{update['track_id']}:snapshot:{sequence}",
            media=media,
            source_ref={
                "event_id": event_id,
                "message_id": f"{event_id}:snapshot:{sequence}",
            },
        )
        return update


def _event_timestamp(phase: str, event: dict[str, Any]) -> float:
    value = event.get("end_time") if phase == "end" else event.get("frame_time")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError("Frigate event timestamp is required")
    return float(value)


def _timestamp_to_iso(value: float) -> str:
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("invalid Frigate event timestamp") from error


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Frigate {key} is required")
    return value


def _confidence(payload: dict[str, Any]) -> float | None:
    value = payload.get("top_score", payload.get("score"))
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(max(0.0, min(1.0, float(value))), 6)


def _normalized(value: float, extent: int) -> float:
    return round(max(0.0, min(float(extent), float(value))) / extent, 6)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return deepcopy([item for item in value if isinstance(item, str)])


def _validate_classification_policy(
    policy: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    validated: dict[str, dict[str, str]] = {}
    for model, labels in policy.items():
        if not isinstance(model, str) or not model:
            raise ValueError("classification policy model names must be non-empty")
        if not isinstance(labels, Mapping) or not labels:
            raise ValueError("classification policy models require label mappings")
        validated_labels: dict[str, str] = {}
        for label, state in labels.items():
            if not isinstance(label, str) or not label:
                raise ValueError("classification policy labels must be non-empty")
            if state not in _ELIGIBILITY_STATES:
                raise ValueError(
                    "classification policy state must be eligible, excluded, or contaminated"
                )
            validated_labels[label] = state
        validated[model] = validated_labels
    return validated
