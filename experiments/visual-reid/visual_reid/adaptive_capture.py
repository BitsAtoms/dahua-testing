"""Bounded multiframe evidence and adaptive capture decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .quality import QualityAssessment


class EvidenceState(str, Enum):
    EMPTY = "empty"
    SEEKING = "seeking"
    WEAK = "weak"
    SUFFICIENT = "sufficient"
    STRONG = "strong"
    PENDING = "pending"


@dataclass(frozen=True)
class EvidenceObservation:
    observation_id: str
    track_id: str
    camera_id: str
    observed_us: int
    quality: QualityAssessment
    payload: Any = None


@dataclass(frozen=True)
class CaptureDecision:
    state: EvidenceState
    reinforce: bool
    target_fps: float
    reasons: tuple[str, ...]


class EvidenceReservoir:
    """Keep only the best temporally diverse observations for each track."""

    def __init__(self, max_per_modality: int = 5, diversity_seconds: float = 0.75):
        if max_per_modality <= 0 or diversity_seconds <= 0:
            raise ValueError("reservoir limits must be positive")
        self.max_per_modality = max_per_modality
        self.diversity_us = round(diversity_seconds * 1_000_000)
        self._tracks: dict[str, dict[str, list[EvidenceObservation]]] = {}
        self._ids: set[str] = set()

    def add(self, observation: EvidenceObservation) -> bool:
        if not observation.observation_id or not observation.track_id:
            raise ValueError("observation and track identifiers are required")
        if observation.observed_us <= 0:
            raise ValueError("observed_us must be positive")
        if observation.observation_id in self._ids:
            return False
        self._ids.add(observation.observation_id)
        modalities = self._tracks.setdefault(observation.track_id, {})
        items = modalities.setdefault(observation.quality.modality, [])
        items.append(observation)
        modalities[observation.quality.modality] = self._select(items)
        return observation in modalities[observation.quality.modality]

    def observations(
        self, track_id: str, modality: str | None = None
    ) -> tuple[EvidenceObservation, ...]:
        modalities = self._tracks.get(track_id, {})
        if modality is not None:
            return tuple(modalities.get(modality, ()))
        return tuple(
            item
            for name in sorted(modalities)
            for item in modalities[name]
        )

    def _select(
        self, observations: list[EvidenceObservation]
    ) -> list[EvidenceObservation]:
        by_bucket: dict[int, EvidenceObservation] = {}
        for item in observations:
            bucket = item.observed_us // self.diversity_us
            current = by_bucket.get(bucket)
            if current is None or _rank(item) > _rank(current):
                by_bucket[bucket] = item
        return sorted(
            sorted(by_bucket.values(), key=_rank, reverse=True)[
                : self.max_per_modality
            ],
            key=lambda item: item.observed_us,
        )


class AdaptiveCapturePolicy:
    """Request more frames until evidence is sufficient or the track ends."""

    def __init__(
        self,
        *,
        normal_fps: float = 1.0,
        reinforce_fps: float = 5.0,
        ambiguity_margin: float = 0.10,
    ) -> None:
        if normal_fps <= 0 or reinforce_fps < normal_fps:
            raise ValueError("capture rates are invalid")
        if not 0 <= ambiguity_margin <= 1:
            raise ValueError("ambiguity_margin must be between zero and one")
        self.normal_fps = normal_fps
        self.reinforce_fps = reinforce_fps
        self.ambiguity_margin = ambiguity_margin

    def decide(
        self,
        observations: tuple[EvidenceObservation, ...],
        *,
        active: bool,
        candidate_margin: float | None = None,
    ) -> CaptureDecision:
        usable = [item for item in observations if item.quality.usable_for_embedding]
        strong = [item for item in usable if item.quality.strong]
        modalities = {item.quality.modality for item in usable}
        face_missing = "face" not in modalities
        ambiguous = (
            candidate_margin is not None
            and candidate_margin < self.ambiguity_margin
        )
        reasons: list[str] = []
        if not observations:
            reasons.append("no_evidence")
            state = EvidenceState.SEEKING if active else EvidenceState.PENDING
        elif not usable:
            reasons.extend(_quality_reasons(observations))
            state = EvidenceState.WEAK if active else EvidenceState.PENDING
        elif ambiguous:
            reasons.append("ambiguous_candidates")
            state = EvidenceState.SEEKING if active else EvidenceState.PENDING
        elif face_missing and active and len(usable) < 3:
            reasons.extend(
                reason
                for item in observations
                if item.quality.modality == "face"
                and not item.quality.usable_for_embedding
                for reason in item.quality.reasons
            )
            reasons.append("face_missing")
            state = EvidenceState.WEAK
        elif len(strong) >= 2 and len(modalities) >= 1:
            state = EvidenceState.STRONG
        elif strong or len(usable) >= 2:
            state = EvidenceState.SUFFICIENT
        else:
            reasons.append("needs_more_observations")
            state = EvidenceState.WEAK if active else EvidenceState.PENDING
        reinforce = active and state in {
            EvidenceState.SEEKING,
            EvidenceState.WEAK,
        }
        return CaptureDecision(
            state=state,
            reinforce=reinforce,
            target_fps=self.reinforce_fps if reinforce else self.normal_fps,
            reasons=tuple(dict.fromkeys(reasons)),
        )


class AdaptiveCaptureCoordinator:
    """Join retained evidence decisions to a camera capture-rate control."""

    def __init__(
        self,
        reservoir: EvidenceReservoir,
        policy: AdaptiveCapturePolicy,
        set_camera_fps: Callable[[str, float], None],
    ) -> None:
        self.reservoir = reservoir
        self.policy = policy
        self.set_camera_fps = set_camera_fps
        self._track_cameras: dict[str, str] = {}
        self._track_active: dict[str, bool] = {}
        self._track_decisions: dict[str, CaptureDecision] = {}
        self._track_last_seen_us: dict[str, int] = {}

    def register(self, track_id: str, camera_id: str) -> None:
        if not track_id or not camera_id:
            raise ValueError("track_id and camera_id are required")
        known_camera = self._track_cameras.setdefault(track_id, camera_id)
        if known_camera != camera_id:
            raise ValueError("one local track cannot change cameras")

    def observe(
        self,
        observation: EvidenceObservation,
        *,
        active: bool,
        candidate_margin: float | None = None,
        seen_us: int | None = None,
    ) -> CaptureDecision:
        self.register(observation.track_id, observation.camera_id)
        self.reservoir.add(observation)
        return self.reassess(
            observation.track_id,
            active=active,
            candidate_margin=candidate_margin,
            seen_us=seen_us,
        )

    def reassess(
        self,
        track_id: str,
        *,
        active: bool,
        candidate_margin: float | None = None,
        seen_us: int | None = None,
    ) -> CaptureDecision:
        camera_id = self._track_cameras.get(track_id)
        if camera_id is None:
            raise KeyError(f"unknown track: {track_id}")
        decision = self.policy.decide(
            self.reservoir.observations(track_id),
            active=active,
            candidate_margin=candidate_margin,
        )
        self._track_active[track_id] = active
        if seen_us is not None:
            if seen_us <= 0:
                raise ValueError("seen_us must be positive")
            self._track_last_seen_us[track_id] = seen_us
        self._track_decisions[track_id] = decision
        self._apply_camera_rate(camera_id)
        return decision

    def expire_stale(
        self, now_us: int, timeout_us: int
    ) -> tuple[tuple[str, CaptureDecision], ...]:
        if now_us <= 0 or timeout_us <= 0:
            raise ValueError("stale-track timing must be positive")
        expired: list[tuple[str, CaptureDecision]] = []
        for track_id, active in tuple(self._track_active.items()):
            last_seen = self._track_last_seen_us.get(track_id)
            if not active or last_seen is None or now_us - last_seen <= timeout_us:
                continue
            decision = self.reassess(track_id, active=False)
            expired.append((track_id, decision))
        return tuple(expired)

    def camera_id(self, track_id: str) -> str:
        return self._track_cameras[track_id]

    def _apply_camera_rate(self, camera_id: str) -> None:
        active_rates = [
            item.target_fps
            for candidate_track, item in self._track_decisions.items()
            if self._track_active.get(candidate_track)
            and self._track_cameras[candidate_track] == camera_id
        ]
        self.set_camera_fps(
            camera_id,
            max(active_rates, default=self.policy.normal_fps),
        )


def _rank(item: EvidenceObservation) -> tuple[bool, float, int]:
    return (item.quality.strong, item.quality.score, item.observed_us)


def _quality_reasons(
    observations: tuple[EvidenceObservation, ...],
) -> list[str]:
    reasons = [reason for item in observations for reason in item.quality.reasons]
    return reasons or ["quality_below_threshold"]
