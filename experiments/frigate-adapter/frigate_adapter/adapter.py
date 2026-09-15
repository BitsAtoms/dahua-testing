"""Translate Frigate MQTT events to the common tracking contract."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FrameSize = tuple[int, int]
_PHASES = {"new", "update", "end"}


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
    ) -> None:
        if not instance_id:
            raise ValueError("instance_id is required")
        self._frame_sizes = dict(camera_frame_sizes)
        self._instance_id = instance_id
        self._labels = labels

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
        published_at: str | None = None,
    ) -> dict[str, Any]:
        """Return a correlated snapshot message for an existing track."""
        if lifecycle_update.get("source", {}).get("type") != "frigate":
            raise ValueError("snapshot lifecycle update must come from Frigate")
        if snapshot_timestamp <= 0:
            raise ValueError("snapshot timestamp is required")

        update = deepcopy(lifecycle_update)
        sequence = round(snapshot_timestamp * 1_000_000)
        event_id = _required_string(update["source_ref"], "event_id")
        update.update(
            phase="snapshot",
            sequence=sequence,
            observed_at=_timestamp_to_iso(snapshot_timestamp),
            published_at=published_at or datetime.now(timezone.utc).isoformat(),
            message_id=f"track-update:{update['track_id']}:snapshot:{sequence}",
            media=[
                {
                    "role": "snapshot",
                    "content_type": "image/jpeg",
                    "path": str(snapshot_path.resolve()),
                }
            ],
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
