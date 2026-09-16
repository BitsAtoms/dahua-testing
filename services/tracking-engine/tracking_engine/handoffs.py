"""Explainable spatial-temporal handoff candidate projection."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .store import ProjectionResult, TrackingStore
from .topology import SpaceTopology


TOPOLOGY_FINGERPRINT_KEY = "handoff_projection.v3.topology_fingerprint"


@dataclass(frozen=True)
class TopologySyncResult:
    changed: bool
    enabled: bool
    candidates: int


class HandoffEngine:
    """Derive candidates without assigning a persistent person identity."""

    def __init__(self, map_path: Path, store: TrackingStore) -> None:
        self.map_path = map_path
        self.store = store
        self.topology: SpaceTopology | None = None

    def sync_topology(self, *, force: bool = False) -> TopologySyncResult:
        if not self.map_path.is_file():
            changed = (
                self.topology is not None
                or self.store.get_metadata(TOPOLOGY_FINGERPRINT_KEY) is not None
            )
            if changed:
                self.store.clear_handoff_candidates()
                self.store.set_metadata(TOPOLOGY_FINGERPRINT_KEY, "")
            self.topology = None
            return TopologySyncResult(changed, False, 0)
        topology = SpaceTopology.load(self.map_path)
        persisted_fingerprint = self.store.get_metadata(TOPOLOGY_FINGERPRINT_KEY)
        if not force and topology.fingerprint == persisted_fingerprint:
            self.topology = topology
            return TopologySyncResult(
                False, True, self.store.handoff_candidate_count()
            )
        self.topology = topology
        self.store.clear_handoff_candidates()
        candidates = self.rebuild()
        self.store.set_metadata(TOPOLOGY_FINGERPRINT_KEY, topology.fingerprint)
        return TopologySyncResult(True, True, candidates)

    def on_projected(
        self, update: dict[str, Any], result: ProjectionResult
    ) -> int:
        if not result.applied or self.topology is None:
            return 0
        created = self.evaluate_destination(result.track_id)
        if result.status == "ended":
            created += self.evaluate_origin(result.track_id)
        return created

    def rebuild(self) -> int:
        if self.topology is None:
            return 0
        tracks = self.store.list_tracks()
        ended = sorted(
            (
                (datetime.fromisoformat(track["ended_at"]).timestamp(), track)
                for track in tracks
                if track["status"] == "ended" and track.get("ended_at")
            ),
            key=lambda item: item[0],
        )
        ended_times = [item[0] for item in ended]
        if not ended or not self.topology.transitions:
            return 0
        minimum = min(item.min_seconds for item in self.topology.transitions)
        maximum = max(item.max_seconds for item in self.topology.transitions)
        maximum_overlap = max(
            item.overlap_tolerance_seconds for item in self.topology.transitions
        )
        candidates: list[dict[str, Any]] = []
        for destination in tracks:
            if not destination.get("first_observed_at"):
                continue
            destination_space = self.topology.space_for_camera(
                destination["camera_id"]
            )
            if destination_space is None:
                continue
            appeared = datetime.fromisoformat(
                destination["first_observed_at"]
            ).timestamp()
            start = bisect_left(ended_times, appeared - maximum)
            stop = bisect_right(
                ended_times,
                appeared - minimum + maximum_overlap,
            )
            for _ended_timestamp, origin in ended[start:stop]:
                candidates.extend(
                    self._candidate_rows(origin, destination, destination_space)
                )
        return self.store.save_handoff_candidates(candidates)

    def evaluate_destination(self, destination_track_id: str) -> int:
        if self.topology is None:
            return 0
        destination = self.store.get_track(destination_track_id)
        if not destination or not destination.get("first_observed_at"):
            return 0
        destination_space = self.topology.space_for_camera(destination["camera_id"])
        if destination_space is None:
            return 0
        created = 0
        for origin in self.store.list_tracks(status="ended"):
            created += self.store.save_handoff_candidates(
                self._candidate_rows(origin, destination, destination_space)
            )
        return created

    def evaluate_origin(self, origin_track_id: str) -> int:
        if self.topology is None:
            return 0
        origin = self.store.get_track(origin_track_id)
        if not origin or origin.get("status") != "ended":
            return 0
        created = 0
        for destination in self.store.list_tracks():
            destination_space = self.topology.space_for_camera(destination["camera_id"])
            if destination_space is not None:
                created += self.store.save_handoff_candidates(
                    self._candidate_rows(origin, destination, destination_space)
                )
        return created

    def _candidate_rows(
        self,
        origin: dict[str, Any],
        destination: dict[str, Any],
        destination_space: str,
    ) -> list[dict[str, Any]]:
        assert self.topology is not None
        if origin["track_id"] == destination["track_id"]:
            return []
        if origin["subject_type"] != destination["subject_type"]:
            return []
        origin_space = self.topology.space_for_camera(origin["camera_id"])
        if origin_space is None or origin_space == destination_space:
            return []
        if not origin.get("ended_at") or not destination.get("first_observed_at"):
            return []
        ended_at = datetime.fromisoformat(origin["ended_at"])
        appeared_at = datetime.fromisoformat(destination["first_observed_at"])
        gap_seconds = (appeared_at - ended_at).total_seconds()
        candidates: list[dict[str, Any]] = []
        for transition in self.topology.transitions_between(
            origin_space, destination_space
        ):
            if gap_seconds < (
                transition.min_seconds - transition.overlap_tolerance_seconds
            ) or gap_seconds > transition.max_seconds:
                continue
            span = transition.max_seconds - transition.min_seconds
            ranking_gap = max(gap_seconds, transition.min_seconds)
            time_score = (
                1.0
                if span == 0
                else 1.0 - (ranking_gap - transition.min_seconds) / span
            )
            score = round(max(0.0, min(1.0, time_score)), 6)
            observed = appeared_at.astimezone(timezone.utc)
            candidate_id = _candidate_id(
                origin["track_id"], destination["track_id"], transition.transition_id
            )
            evidence = {
                "kind": "spatial_temporal_only",
                "ranking_not_identity_probability": True,
                "topology": {
                    "transition_id": transition.transition_id,
                    "origin_space_id": origin_space,
                    "destination_space_id": destination_space,
                    "bidirectional": transition.bidirectional,
                },
                "timing": {
                    "gap_seconds": round(gap_seconds, 6),
                    "ranking_gap_seconds": round(ranking_gap, 6),
                    "min_seconds": transition.min_seconds,
                    "max_seconds": transition.max_seconds,
                    "source_overlap_tolerance_seconds": (
                        transition.overlap_tolerance_seconds
                    ),
                },
            }
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "origin_track_id": origin["track_id"],
                    "destination_track_id": destination["track_id"],
                    "transition_id": transition.transition_id,
                    "origin_space_id": origin_space,
                    "destination_space_id": destination_space,
                    "gap_seconds": round(gap_seconds, 6),
                    "score": score,
                    "observed_at": observed.isoformat(),
                    "observed_us": round(observed.timestamp() * 1_000_000),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_json": json.dumps(
                        evidence, ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )
        return candidates


def _candidate_id(origin: str, destination: str, transition: str) -> str:
    wire = f"{origin}\0{destination}\0{transition}".encode("utf-8")
    return "handoff:" + hashlib.sha256(wire).hexdigest()
