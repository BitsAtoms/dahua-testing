"""Correlate Dahua records and emit progressive ``observation.v1`` messages."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any


CorrelationKey = tuple[str, int]


def _source_time_to_iso(value: Any) -> str | None:
    """Convert Dahua RealUTC epoch seconds without guessing local timestamps."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


@dataclass
class _Pending:
    payload: dict[str, Any]
    received_at: float


class DahuaEventCorrelator:
    """Join CGI metadata and NetSDK media without delaying the live event.

    The CGI body produces the first observation immediately. NetSDK media and
    optional face metadata are emitted later as updates sharing the same
    ``observation_id``. ``GroupID`` remains only a correlation key.
    """

    def __init__(
        self,
        ttl_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._clock = clock
        self._bodies: dict[CorrelationKey, _Pending] = {}
        self._netsdk: dict[CorrelationKey, _Pending] = {}
        self._faces: dict[str, list[_Pending]] = {}
        self._stages: dict[CorrelationKey, set[str]] = {}
        self._revisions: dict[CorrelationKey, int] = {}
        self._completed: dict[CorrelationKey, float] = {}

    def ingest_cgi(
        self, camera_id: str, event: dict[str, Any]
    ) -> list[dict[str, Any]]:
        self._validate_camera_id(camera_id)
        self._prune_completed(self._clock())
        if event.get("action") != "Start":
            return []

        now = self._clock()
        payload = deepcopy(event)
        object_type = payload.get("object_type")
        if object_type == "HumanFace":
            self._faces.setdefault(camera_id, []).append(_Pending(payload, now))
            return self._updates_for_camera(camera_id)
        if object_type != "Human":
            return []

        key = self._key(camera_id, payload)
        if key in self._completed:
            return []
        self._bodies[key] = _Pending(payload, now)
        return self._updates(key)

    def ingest_netsdk(
        self, camera_id: str, event: dict[str, Any]
    ) -> list[dict[str, Any]]:
        self._validate_camera_id(camera_id)
        self._prune_completed(self._clock())
        key = self._key(camera_id, event)
        if key in self._completed:
            return []
        self._netsdk[key] = _Pending(deepcopy(event), self._clock())
        return self._updates(key)

    def expire(self) -> list[dict[str, Any]]:
        """Emit unmatched NetSDK records and release stale correlation state."""
        now = self._clock()
        self._prune_completed(now)
        expired: list[dict[str, Any]] = []
        keys = set(self._bodies) | set(self._netsdk)
        for key in sorted(keys):
            timestamps = [
                pending.received_at
                for pending in (self._bodies.get(key), self._netsdk.get(key))
                if pending is not None
            ]
            if not timestamps or now - min(timestamps) < self._ttl:
                continue
            if self._bodies.get(key) is None and self._netsdk.get(key) is not None:
                expired.append(self._build_message(key, "observation", "partial"))
            self._finish(key)

        for camera_id, faces in list(self._faces.items()):
            fresh = [face for face in faces if now - face.received_at < self._ttl]
            if fresh:
                self._faces[camera_id] = fresh
            else:
                self._faces.pop(camera_id, None)
        return expired

    @staticmethod
    def _validate_camera_id(camera_id: str) -> None:
        if not camera_id:
            raise ValueError("camera_id is required")

    @staticmethod
    def _key(camera_id: str, event: dict[str, Any]) -> CorrelationKey:
        group_id = event.get("group_id")
        if not isinstance(group_id, int):
            raise ValueError("group_id must be an integer")
        return camera_id, group_id

    def _updates_for_camera(self, camera_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for key in list(self._bodies):
            if key[0] == camera_id:
                messages.extend(self._updates(key))
        return messages

    def _updates(self, key: CorrelationKey) -> list[dict[str, Any]]:
        body_pending = self._bodies.get(key)
        netsdk_pending = self._netsdk.get(key)
        if body_pending is None:
            return []

        stages = self._stages.setdefault(key, set())
        messages: list[dict[str, Any]] = []
        if "body" not in stages:
            stages.add("body")
            messages.append(self._build_message(key, "observation", "partial"))

        face = self._find_face(key[0], body_pending.payload)
        emitted_media_with_face = False
        if netsdk_pending is not None and "media" not in stages:
            stages.add("media")
            complete = not self._expects_face(body_pending.payload) or face is not None
            emitted_media_with_face = face is not None
            if emitted_media_with_face:
                stages.add("face")
            messages.append(
                self._build_message(key, "update", "complete" if complete else "partial")
            )

        if face is not None and "face" not in stages and not emitted_media_with_face:
            stages.add("face")
            complete = netsdk_pending is not None
            messages.append(
                self._build_message(key, "update", "complete" if complete else "partial")
            )

        if netsdk_pending is not None and (
            not self._expects_face(body_pending.payload) or face is not None
        ):
            self._finish(key, face)
        return messages

    @staticmethod
    def _expects_face(body: dict[str, Any]) -> bool:
        return body.get("relative_id") not in (None, 0)

    def _find_face(self, camera_id: str, body: dict[str, Any]) -> _Pending | None:
        body_id = body.get("object_id")
        expected_face_id = body.get("relative_id")
        for face in self._faces.get(camera_id, []):
            payload = face.payload
            if (
                expected_face_id not in (None, 0)
                and payload.get("object_id") == expected_face_id
            ) or (
                body_id is not None
                and body_id in (payload.get("belong_id"), payload.get("relative_id"))
            ):
                return face
        return None

    def _build_message(
        self, key: CorrelationKey, phase: str, status: str
    ) -> dict[str, Any]:
        body = self._bodies[key].payload if key in self._bodies else None
        netsdk = self._netsdk[key].payload if key in self._netsdk else None
        face_pending = self._find_face(key[0], body) if body else None
        face = face_pending.payload if face_pending else None
        revision = self._revisions.get(key, 0) + 1
        self._revisions[key] = revision

        snapshots = deepcopy(netsdk.get("snapshots", {})) if netsdk else {}
        if isinstance(snapshots, list):
            snapshots = {
                item["type"]: item["file"]
                for item in snapshots
                if "type" in item and "file" in item
            }
        media = [
            {"role": role, "content_type": "image/jpeg", "path": path}
            for role, path in snapshots.items()
        ]

        cgi_source_timestamp = body.get("timestamp") if body else None
        netsdk_source_timestamp = netsdk.get("timestamp") if netsdk else None
        source_timestamp = netsdk_source_timestamp or cgi_source_timestamp
        source_event_uuid = body.get("event_uuid") if body else None
        identity = source_event_uuid or f"group-{key[1]}:{source_timestamp or 'unknown'}"
        observation_id = f"dahua:{key[0]}:{identity}"
        complete_sources = body is not None and netsdk is not None
        normalized_at = datetime.now(timezone.utc).isoformat()
        pending_times = [
            pending.received_at
            for pending in (self._bodies.get(key), self._netsdk.get(key), face_pending)
            if pending is not None
        ]
        correlation_wait_ms = (
            round((self._clock() - min(pending_times)) * 1000, 1)
            if pending_times
            else None
        )

        return {
            "schema_version": "observation.v1",
            "message_id": f"{observation_id}:r{revision}",
            "observation_id": observation_id,
            "source": {"type": "dahua", "instance_id": None},
            "camera_id": key[0],
            "phase": phase,
            "observed_at": _source_time_to_iso(cgi_source_timestamp),
            "ingested_at": normalized_at,
            "subject": {
                "type": "person",
                "local_track_id": (
                    str(body.get("object_id"))
                    if body and body.get("object_id") is not None
                    else None
                ),
                "confidence": None,
            },
            "geometry": None,
            "zones": {"current": [], "entered": []},
            "attributes": deepcopy(body.get("attributes", {})) if body else {},
            "media": media,
            "relationships": {
                "face_local_object_id": (
                    str(face.get("object_id"))
                    if face and face.get("object_id") is not None
                    else None
                )
            },
            "quality": {
                "status": status,
                "netsdk_body_group_match": complete_sources,
                "body_face_relation_match": face is not None,
            },
            "timing": {
                "camera_observed_at": _source_time_to_iso(cgi_source_timestamp),
                "cgi_received_at": body.get("_received_at") if body else None,
                "netsdk_callback_received_at": (
                    netsdk.get("callback_received_at") if netsdk else None
                ),
                "netsdk_python_received_at": (
                    netsdk.get("_python_received_at") if netsdk else None
                ),
                "normalized_at": normalized_at,
                "correlation_wait_ms": correlation_wait_ms,
                "source_timestamps": {
                    "cgi_real_utc": cgi_source_timestamp,
                    "netsdk_camera_local": netsdk_source_timestamp,
                },
            },
            "source_data": {
                "group_id": key[1],
                "body_event_id": body.get("event_id") if body else None,
                "face_event_id": face.get("event_id") if face else None,
                "event_uuid": source_event_uuid,
            },
            "raw": {
                "cgi_body": deepcopy(body),
                "cgi_face": deepcopy(face),
                "netsdk": deepcopy(netsdk),
            },
        }

    def _finish(self, key: CorrelationKey, face: _Pending | None = None) -> None:
        self._bodies.pop(key, None)
        self._netsdk.pop(key, None)
        self._stages.pop(key, None)
        self._revisions.pop(key, None)
        self._completed[key] = self._clock()
        if face is not None:
            faces = self._faces.get(key[0], [])
            if face in faces:
                faces.remove(face)
            if not faces:
                self._faces.pop(key[0], None)

    def _prune_completed(self, now: float) -> None:
        """Bound duplicate-suppression state and allow future GroupID reuse."""
        self._completed = {
            key: completed_at
            for key, completed_at in self._completed.items()
            if now - completed_at < self._ttl
        }
