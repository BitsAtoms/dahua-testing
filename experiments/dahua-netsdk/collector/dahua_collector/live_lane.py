"""Turn live Dahua IVS targets into ``track_update.v1`` lifecycle messages.

The camera reports each visible person about ten times per second with its
camera-local ``ObjectID`` (the ``TRACK_EX_B0`` track ID, equal to the CGI
HumanTrait ``ObjectID``). This module is pure logic: it decides when a track
starts, how often it is refreshed and when it ends. Transport and native SDK
code live elsewhere.

Timeline: PC receipt time is authoritative because camera clocks can be
skewed (one tested camera was 26.9 days behind). Camera time stays in the raw
evidence only.

Lifecycle rules and the evidence behind them (2026-09-29, two models):

* **Confirmation.** A track is published after ``confirm_targets`` targets
  (default 3, about 0.3 s). A one-frame target seen once had no HumanTrait,
  i.e. the camera itself did not keep it.
* **Updates** are limited to two per second per track, enough for a 1 Hz map.
* **End.** The camera publishes HumanTrait when it closes its own track
  (0.41 s after the last live target in the measured case) and does not
  publish it while a seated person is briefly unreported. ``finalize`` ends
  the track on that signal. Silence alone ends a track only after
  ``end_after_seconds`` (default 10 s): walking targets had gaps up to 1.0 s,
  but a seated person produced gaps of 2.4 and 3.6 s under the same ObjectID.
* **Bounce.** Targets of a finalized ObjectID arriving within
  ``finalize_grace_seconds`` are ignored so a late frame cannot start a
  phantom track.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import time
from typing import Any, Callable

from .ivs import TRACK_RECORD_TYPE, parse_track_record


REPLAYED_FRAME_SEQUENCE = -1  # frames replayed at connection, not live video
PROBE_FRAME_PREFIX = "ivs_frame="  # dahua-ivs-probe --stdout line prefix
_COORDINATE_EXTENT = 8192.0  # matches the existing Dahua geometry mapping


@dataclass(frozen=True)
class LiveTarget:
    """One person reported by the camera in one frame."""

    object_id: int
    box: tuple[int, int, int, int]  # left, top, right, bottom in 0..8191
    received_at: float  # PC receipt time, POSIX seconds
    frame_sequence: int


@dataclass
class _Pending:
    count: int
    first_seen: float
    last_seen: float


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
    unconfirmed_dropped: int = 0
    after_finalization_dropped: int = 0
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
        confirm_targets: int = 3,
        pending_timeout_seconds: float = 2.0,
        update_interval_seconds: float = 0.5,
        end_after_seconds: float = 10.0,
        finalize_grace_seconds: float = 5.0,
        join_retention_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not camera_id or not session_id:
            raise ValueError("camera_id and session_id are required")
        if confirm_targets < 1:
            raise ValueError("confirm_targets must be at least 1")
        if min(pending_timeout_seconds, end_after_seconds) <= 0 or update_interval_seconds < 0:
            raise ValueError("timeouts must be positive")
        self.camera_id = camera_id
        self.session_id = session_id
        self.confirm_targets = confirm_targets
        self.pending_timeout_seconds = pending_timeout_seconds
        self.update_interval_seconds = update_interval_seconds
        self.end_after_seconds = end_after_seconds
        self.finalize_grace_seconds = finalize_grace_seconds
        self.join_retention_seconds = join_retention_seconds
        self._clock = clock
        self._pending: dict[int, _Pending] = {}
        self._active: dict[int, _Track] = {}
        self._ended: dict[int, _Track] = {}
        self._finalized_at: dict[int, float] = {}
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
        finalized_at = self._finalized_at.get(target.object_id)
        if finalized_at is not None and target.received_at - finalized_at < self.finalize_grace_seconds:
            self.stats.after_finalization_dropped += 1
            return []
        self.stats.targets += 1

        track = self._active.get(target.object_id)
        if track is None:
            pending = self._pending.get(target.object_id)
            if pending is None:
                pending = self._pending[target.object_id] = _Pending(
                    0, target.received_at, target.received_at
                )
            pending.count += 1
            pending.last_seen = target.received_at
            if pending.count < self.confirm_targets:
                return []
            del self._pending[target.object_id]
            track = self._start(target, pending.first_seen)
            return [self._message(track, "new", target.received_at)]

        track.last_seen = max(track.last_seen, target.received_at)
        track.box = target.box
        if (
            track.last_emitted is None
            or target.received_at - track.last_emitted >= self.update_interval_seconds
        ):
            return [self._message(track, "update", target.received_at)]
        return []

    def finalize(self, object_id: int, now: float) -> list[dict[str, Any]]:
        """End a track because the camera closed it (HumanTrait arrived).

        Unknown IDs, such as HumanTrait face objects, are ignored.
        """
        self._pending.pop(object_id, None)
        if object_id not in self._active:
            return []
        self._finalized_at[object_id] = now
        return [self._end(object_id, "camera_finalized")]

    def expire(self, now: float) -> list[dict[str, Any]]:
        """End tracks silent for ``end_after_seconds`` and drop stale state."""
        for object_id, pending in list(self._pending.items()):
            if now - pending.last_seen >= self.pending_timeout_seconds:
                del self._pending[object_id]
                self.stats.unconfirmed_dropped += 1
        messages = [
            self._end(object_id, "timeout")
            for object_id, track in list(self._active.items())
            if now - track.last_seen >= self.end_after_seconds
        ]
        for object_id, finalized_at in list(self._finalized_at.items()):
            if now - finalized_at >= self.finalize_grace_seconds:
                del self._finalized_at[object_id]
        for object_id, track in list(self._ended.items()):
            if track.ended_at is not None and now - track.ended_at > self.join_retention_seconds:
                del self._ended[object_id]
        return messages

    def close(self, reason: str = "collector_stopped") -> list[dict[str, Any]]:
        """End every active track, e.g. on shutdown or camera disconnect."""
        self._pending.clear()
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

    def _start(self, target: LiveTarget, first_seen: float) -> _Track:
        # The same ObjectID after an end is a new visit (e.g. a camera reboot
        # restarted its counter) or a silence longer than the fallback. It
        # gets a new track_id because an ended track is never reopened.
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
            first_seen=first_seen,
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


def target_from_probe_line(line: str) -> tuple[LiveTarget, dict[str, Any]] | None:
    """Parse one ``dahua-ivs-probe --stdout`` line into a target.

    Returns the target plus the raw frame (kept for audit samples), or
    ``None`` for any other line. A malformed frame raises ``ValueError``.
    """
    if not line.startswith(PROBE_FRAME_PREFIX):
        return None
    try:
        frame = json.loads(line[len(PROBE_FRAME_PREFIX):])
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid ivs_frame JSON: {error}") from error
    if frame.get("type") != TRACK_RECORD_TYPE or frame.get("encoding") != "hex":
        return None
    record = parse_track_record(bytes.fromhex(frame["payload"]))
    received_at = datetime.fromisoformat(
        str(frame["received_at"]).replace("Z", "+00:00")
    ).timestamp()
    target = LiveTarget(record.track_id, record.box, received_at, int(frame["frame_sequence"]))
    return target, frame


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
