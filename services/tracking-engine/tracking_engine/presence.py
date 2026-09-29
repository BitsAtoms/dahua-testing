"""Source-neutral presences and per-space occupancy from local tracks.

A camera-local track is what one source reports; a *presence* is what the
system counts. They differ because sources fragment one person into several
tracks. The Dahua test camera closed and re-opened the track of a person
sitting still every 1-6 minutes, with overlapping and very short re-detections
at the same image position (docs/dahua-research.md, 2026-09-29).

Rules, deliberately simple and deterministic:

* **Join.** A track joins an existing presence of the same camera when it
  starts while that presence is alive (active, or ended less than
  ``hold_seconds`` ago) and its first centre is within ``join_distance`` of the
  presence's latest centre (normalized image coordinates). Overlapping
  duplicates at one position therefore count once.
* **Confirm.** A presence counts after ``confirm_seconds`` of observed track
  time, so one-off detections never reach the map.
* **Hold.** A confirmed presence keeps counting for ``hold_seconds`` after its
  last track ends, bridging the source's re-detection gaps.
* **Space.** Presences are per camera. A space covered by several cameras
  counts the maximum over its cameras, never their sum, until overlapping
  views can be de-duplicated geometrically.

Defaults come from an offline simulation over 26 recorded minutes; they are
provisional and must be calibrated with group visits after final camera
placement. Joining by proximity can merge two people standing very close.
Tracks marked ``excluded`` or ``contaminated`` never count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Iterable


INELIGIBLE_STATES = {"excluded", "contaminated"}


@dataclass(frozen=True)
class PresenceParams:
    join_distance: float = 0.15
    confirm_seconds: float = 3.0
    hold_seconds: float = 20.0

    def as_dict(self) -> dict[str, float]:
        return {
            "join_distance": self.join_distance,
            "confirm_seconds": self.confirm_seconds,
            "hold_seconds": self.hold_seconds,
        }


@dataclass(frozen=True)
class TrackSpan:
    """The part of a local track that presence logic needs."""

    track_id: str
    camera_id: str
    start: float  # POSIX seconds of the first observation
    end: float  # POSIX seconds of the latest observation
    active: bool
    first_center: tuple[float, float] | None
    last_center: tuple[float, float] | None


@dataclass
class Presence:
    presence_id: str
    camera_id: str
    first_seen: float
    last_seen: float
    last_center: tuple[float, float] | None
    track_ids: list[str] = field(default_factory=list)
    spans: list[tuple[float, float, bool]] = field(default_factory=list)

    def active(self) -> bool:
        return any(active for _, _, active in self.spans)

    def observed_seconds(self, now: float) -> float:
        total = 0.0
        for start, end, active in self.spans:
            stop = now if active else min(end, now)
            total += max(0.0, stop - start)
        return total

    def counted(self, now: float, params: PresenceParams) -> bool:
        if self.first_seen > now or self.observed_seconds(now) < params.confirm_seconds:
            return False
        return self.active() or now - self.last_seen <= params.hold_seconds


def span_from_track(track: dict[str, Any]) -> TrackSpan | None:
    """Build a span from a ``local_tracks`` row (JSON fields already decoded).

    Returns ``None`` for tracks without observation times or marked
    ineligible by a source classification.
    """
    eligibility = (track.get("attributes") or {}).get("track_eligibility") or {}
    if eligibility.get("state") in INELIGIBLE_STATES:
        return None
    start = _timestamp(track.get("first_observed_at"))
    end = _timestamp(track.get("last_observed_at"))
    if start is None or end is None:
        return None
    last_center = _center(track.get("geometry"))
    first_center = _center(track.get("first_geometry")) or last_center
    return TrackSpan(
        track_id=str(track["track_id"]),
        camera_id=str(track["camera_id"]),
        start=start,
        end=max(start, end),
        active=track.get("status") == "active",
        first_center=first_center,
        last_center=last_center,
    )


def build_presences(
    spans: Iterable[TrackSpan], params: PresenceParams = PresenceParams()
) -> list[Presence]:
    presences: list[Presence] = []
    for span in sorted(spans, key=lambda item: (item.start, item.track_id)):
        best: tuple[float, Presence] | None = None
        for presence in presences:
            if presence.camera_id != span.camera_id:
                continue
            alive = presence.active() or span.start - presence.last_seen <= params.hold_seconds
            if not alive or presence.last_center is None or span.first_center is None:
                continue
            distance = math.dist(presence.last_center, span.first_center)
            if distance <= params.join_distance and (best is None or distance < best[0]):
                best = (distance, presence)
        if best is None:
            presence = Presence(
                presence_id=span.track_id,
                camera_id=span.camera_id,
                first_seen=span.start,
                last_seen=span.end,
                last_center=span.last_center,
            )
            presences.append(presence)
        else:
            presence = best[1]
        presence.track_ids.append(span.track_id)
        presence.spans.append((span.start, span.end, span.active))
        presence.first_seen = min(presence.first_seen, span.start)
        if span.end >= presence.last_seen or span.active:
            presence.last_seen = max(presence.last_seen, span.end)
            presence.last_center = span.last_center or presence.last_center
    return presences


def occupancy(
    spans: Iterable[TrackSpan],
    camera_spaces: dict[str, str | None],
    now: float,
    params: PresenceParams = PresenceParams(),
) -> dict[str, Any]:
    """Count presences per space at ``now``."""
    presences = build_presences(spans, params)
    per_camera: dict[str, list[Presence]] = {}
    for presence in presences:
        if presence.counted(now, params):
            per_camera.setdefault(presence.camera_id, []).append(presence)
    spaces: dict[str, dict[str, Any]] = {}
    unmapped = 0
    for camera_id, counted in per_camera.items():
        space_id = camera_spaces.get(camera_id)
        if not space_id:
            unmapped += len(counted)
            continue
        entry = spaces.setdefault(space_id, {"count": 0, "by_camera": {}, "presences": []})
        entry["by_camera"][camera_id] = len(counted)
        entry["presences"] += [
            {
                "presence_id": presence.presence_id,
                "camera_id": camera_id,
                "first_seen": presence.first_seen,
                "last_seen": presence.last_seen,
                "active": presence.active(),
                "tracks": len(presence.track_ids),
            }
            for presence in counted
        ]
    for entry in spaces.values():
        entry["count"] = max(entry["by_camera"].values(), default=0)
    return {
        "rule": params.as_dict(),
        "total": sum(entry["count"] for entry in spaces.values()),
        "unmapped_presences": unmapped,
        "spaces": spaces,
    }


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _center(geometry: Any) -> tuple[float, float] | None:
    if not isinstance(geometry, dict):
        return None
    center = geometry.get("center")
    if isinstance(center, dict) and "x" in center and "y" in center:
        return float(center["x"]), float(center["y"])
    return None
