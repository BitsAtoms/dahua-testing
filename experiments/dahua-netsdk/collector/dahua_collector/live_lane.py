"""Turn live Dahua IVS targets into ``track_update.v1`` lifecycle messages.

The camera reports each visible person about ten times per second with its
camera-local ``ObjectID`` (the ``TRACK_EX_B0`` track ID, equal to the CGI
HumanTrait ``ObjectID``). This module is pure logic: it decides when a track
starts, how often it is refreshed and when it ends. Transport and native SDK
code live elsewhere.

Timeline: PC receipt time is authoritative because camera clocks can be
skewed (one tested camera was 26.9 days behind). Camera time stays in the raw
evidence only.

Defaults come from recorded sessions on two models (2026-09-29): intra-track
gaps had a median of 0.1 s, p99 of 0.2 s and a maximum of 1.0 s, so a track
ends after 2 s without targets. Updates are limited to two per second per
track, which is enough for a 1 Hz map and keeps the transport light.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Callable


REPLAYED_FRAME_SEQUENCE = -1  # frames replayed at connection, not live video
_COORDINATE_EXTENT = 8192.0  # matches the existing Dahua geometry mapping


@dataclass(frozen=True)
class LiveTarget:
    """One person reported by the camera in one frame."""

    object_id: int
    box: tuple[int, int, int, int]  # left, top, right, bottom in 0..8191
    received_at: float  # PC receipt time, POSIX seconds
    frame_sequence: int


@dataclass
class _Track:
    track_id: str
    object_id: int
    first_seen: float
    last_seen: float
    box: tuple[int, int, int, int]
    sequence: int = 0
    last_emitted: float | None = None
    ended_at: float | None = None


@dataclass
class LaneStats:
    targets: int = 0
    replayed_frames_dropped: int = 0
    tracks_started: int = 0
    tracks_ended: int = 0
    object_id_reuses: int = 0
    by_end_reason: dict[str, int] = field(default_factory=dict)


class DahuaLiveLane:
    def __init__(
        self,
        camera_id: str,
        session_id: str,
        *,
        end_after_seconds: float = 2.0,
        update_interval_seconds: float = 0.5,
        join_retention_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not camera_id or not session_id:
            raise ValueError("camera_id and session_id are required")
        if end_after_seconds <= 0 or update_interval_seconds < 0:
            raise ValueError("timeouts must be positive")
        self.camera_id = camera_id
        self.session_id = session_id
        self.end_after_seconds = end_after_seconds
        self.update_interval_seconds = update_interval_seconds
        self.join_retention_seconds = join_retention_seconds
        self._clock = clock
        self._active: dict[int, _Track] = {}
        self._ended: dict[int, _Track] = {}
        self._uses: dict[int, int] = {}
        self.stats = LaneStats()

    # -- input ---------------------------------------------------------------

    def ingest(self, target: LiveTarget) -> list[dict[str, Any]]:
        """Return ``new`` or throttled ``update`` messages for one target."""
        if target.frame_sequence == REPLAYED_FRAME_SEQUENCE:
            # Replayed at (re)connection with stale camera time; seen on both
            # tested models. Counting it keeps the drop observable.
            self.stats.replayed_frames_dropped += 1
            return []
        self.stats.targets += 1
        track = self._active.get(target.object_id)
        if track is None:
            track = self._start(target)
            return [self._message(track, "new", target.received_at)]
        track.last_seen = max(track.last_seen, target.received_at)
        track.box = target.box
        if (
            track.last_emitted is None
            or target.received_at - track.last_emitted >= self.update_interval_seconds
        ):
            return [self._message(track, "update", target.received_at)]
        return []

    def expire(self, now: float) -> list[dict[str, Any]]:
        """End tracks that have been silent for ``end_after_seconds``."""
        messages = []
        for object_id, track in list(self._active.items()):
            if now - track.last_seen >= self.end_after_seconds:
                messages.append(self._end(object_id, "timeout"))
        self._prune_ended(now)
        return messages

    def close(self, reason: str = "collector_stopped") -> list[dict[str, Any]]:
        """End every active track, e.g. on shutdown or camera disconnect."""
        return [self._end(object_id, reason) for object_id in list(self._active)]

    # -- joins ---------------------------------------------------------------

    def track_for_object(self, object_id: int) -> tuple[str, float, float] | None:
        """Return ``(track_id, first_seen, last_seen)`` for an active or
        recently ended track, so late HumanTrait photos can join it."""
        track = self._active.get(object_id) or self._ended.get(object_id)
        if track is None:
            return None
        return track.track_id, track.first_seen, track.last_seen

    @property
    def active_object_ids(self) -> list[int]:
        return sorted(self._active)

    # -- internals -----------------------------------------------------------

    def _start(self, target: LiveTarget) -> _Track:
        # The same ObjectID after an end is a new visit (e.g. a camera reboot
        # restarted its counter) or a gap longer than the timeout. It gets a
        # new track_id because an ended track is never reopened downstream.
        uses = self._uses.get(target.object_id, 0) + 1
        self._uses[target.object_id] = uses
        suffix = "" if uses == 1 else f":{uses}"
        if uses > 1:
            self.stats.object_id_reuses += 1
        self._ended.pop(target.object_id, None)
        track = _Track(
            track_id=(
                f"dahua:{self.camera_id}:live:{self.session_id}:"
                f"{target.object_id}{suffix}"
            ),
            object_id=target.object_id,
            first_seen=target.received_at,
            last_seen=target.received_at,
            box=target.box,
        )
        self._active[target.object_id] = track
        self.stats.tracks_started += 1
        return track

    def _end(self, object_id: int, reason: str) -> dict[str, Any]:
        track = self._active.pop(object_id)
        track.ended_at = track.last_seen
        self._ended[object_id] = track
        self.stats.tracks_ended += 1
        self.stats.by_end_reason[reason] = self.stats.by_end_reason.get(reason, 0) + 1
        return self._message(track, "end", track.last_seen, end_reason=reason)

    def _prune_ended(self, now: float) -> None:
        for object_id, track in list(self._ended.items()):
            if track.ended_at is not None and now - track.ended_at > self.join_retention_seconds:
                del self._ended[object_id]

    def _message(
        self,
        track: _Track,
        phase: str,
        observed_at: float,
        end_reason: str | None = None,
    ) -> dict[str, Any]:
        track.sequence += 1
        track.last_emitted = observed_at
        message_id = f"track-update:{track.track_id}:{phase}:{track.sequence}"
        quality: dict[str, Any] = {
            "status": "complete",
            "source_lifecycle": "live",
            "timeline": "pc_receipt",
        }
        if end_reason is not None:
            quality["end_reason"] = end_reason
        return {
            "schema_version": "track_update.v1",
            "message_id": message_id,
            "track_id": track.track_id,
            "source": {"type": "dahua", "instance_id": None},
            "camera_id": self.camera_id,
            "phase": phase,
            "sequence": track.sequence,
            "observed_at": _iso(observed_at),
            "published_at": _iso(self._clock()),
            "subject": {
                "type": "person",
                "local_track_id": str(track.object_id),
                "confidence": None,
            },
            "geometry": _geometry(track.box),
            "zones": {"current": [], "entered": []},
            "attributes": {},
            "media": [],
            "quality": quality,
            "source_ref": {
                "event_id": f"ivs:{track.object_id}",
                "message_id": f"{track.track_id}:{track.sequence}",
            },
        }


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _geometry(box: tuple[int, int, int, int]) -> dict[str, Any]:
    left, top, right, bottom = (
        round(max(0.0, min(_COORDINATE_EXTENT, float(value))) / _COORDINATE_EXTENT, 6)
        for value in box
    )
    return {
        "coordinate_space": "normalized_0_1",
        "box": {"x_min": left, "y_min": top, "x_max": right, "y_max": bottom},
        "center": {"x": round((left + right) / 2, 6), "y": round((top + bottom) / 2, 6)},
    }
