"""Optional BoxMOT ReID trackers behind the local-tracker contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .local_tracking import (
    DetectionFrame,
    LocalTrackUpdate,
    LocalTrackUpdateKind,
    PixelBox,
)


BoxMotTrackerName = Literal["botsort", "deepocsort", "strongsort", "occluboost"]


@dataclass(frozen=True)
class BoxMotConfig:
    tracker: BoxMotTrackerName = "botsort"
    reid_weights: str = "osnet_x0_25_msmt17.pt"
    device: str = "cpu"
    frame_rate: float = 10.0
    max_age: int = 30
    minimum_consecutive_frames: int = 2
    botsort_proximity_threshold: float = 0.5
    botsort_appearance_threshold: float = 0.25
    occluboost_recovery_appearance_threshold: float = 0.4
    occluboost_gta_appearance_threshold: float = 0.35

    def __post_init__(self) -> None:
        if self.tracker not in ("botsort", "deepocsort", "strongsort", "occluboost"):
            raise ValueError(f"unsupported BoxMOT tracker: {self.tracker}")
        if not self.reid_weights.strip() or not self.device.strip():
            raise ValueError("ReID weights and device cannot be empty")
        if self.frame_rate <= 0 or self.max_age < 1:
            raise ValueError("frame rate and max age are invalid")
        if self.minimum_consecutive_frames < 1:
            raise ValueError("minimum_consecutive_frames must be positive")
        for value in (
            self.botsort_proximity_threshold,
            self.botsort_appearance_threshold,
            self.occluboost_recovery_appearance_threshold,
            self.occluboost_gta_appearance_threshold,
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError("BoT-SORT thresholds must be between zero and one")


class BoxMotProvider:
    """Run one BoxMOT tracker per camera without leaking its array layout."""

    def __init__(
        self,
        config: BoxMotConfig | None = None,
        *,
        _dependencies: tuple[Any, dict[str, Any]] | None = None,
    ) -> None:
        self.config = config or BoxMotConfig()
        self._np, self._tracker_types = _dependencies or _load_dependencies()
        self._trackers: dict[str, Any] = {}
        self._last_seen: dict[str, dict[int, int]] = {}
        self._generations: dict[str, int] = {}

    @property
    def provider_id(self) -> str:
        return f"boxmot:25.0.0:{self.config.tracker}:osnet-x0-25-msmt17"

    def process(self, frame: DetectionFrame) -> tuple[LocalTrackUpdate, ...]:
        if frame.image is None:
            raise ValueError("BoxMOT ReID requires the source frame")
        tracker = self._trackers.get(frame.camera_id)
        if tracker is None:
            tracker = self._new_tracker()
            self._trackers[frame.camera_id] = tracker
            self._last_seen[frame.camera_id] = {}
            self._generations.setdefault(frame.camera_id, 0)

        detections = self._np.asarray(
            [
                [
                    item.box.x1,
                    item.box.y1,
                    item.box.x2,
                    item.box.y2,
                    item.confidence,
                    0,
                ]
                for item in frame.detections
            ],
            dtype=self._np.float32,
        ).reshape((-1, 6))
        tracked = tracker.update(
            detections,
            frame.image,
            timestamp_s=frame.observed_us / 1_000_000,
        )

        generation = self._generations[frame.camera_id]
        last_seen = self._last_seen[frame.camera_id]
        updates: list[LocalTrackUpdate] = []
        for row in tracked:
            raw_id = int(row[4])
            left = max(0.0, min(float(frame.width), float(row[0])))
            top = max(0.0, min(float(frame.height), float(row[1])))
            right = max(0.0, min(float(frame.width), float(row[2])))
            bottom = max(0.0, min(float(frame.height), float(row[3])))
            if right <= left or bottom <= top:
                continue
            last_seen[raw_id] = frame.frame_index
            updates.append(
                LocalTrackUpdate(
                    camera_id=frame.camera_id,
                    local_track_id=_track_id(generation, raw_id),
                    frame_index=frame.frame_index,
                    observed_us=frame.observed_us,
                    kind=LocalTrackUpdateKind.OBSERVED,
                    box=PixelBox(left, top, right, bottom),
                    confidence=max(0.0, min(1.0, float(row[5]))),
                )
            )

        ended = [
            track_id
            for track_id, seen_frame in last_seen.items()
            if frame.frame_index - seen_frame > self.config.max_age
        ]
        for raw_id in sorted(ended):
            updates.append(
                LocalTrackUpdate(
                    camera_id=frame.camera_id,
                    local_track_id=_track_id(generation, raw_id),
                    frame_index=frame.frame_index,
                    observed_us=frame.observed_us,
                    kind=LocalTrackUpdateKind.ENDED,
                )
            )
            del last_seen[raw_id]
        return tuple(updates)

    def reset(
        self, camera_id: str, *, frame_index: int, observed_us: int
    ) -> tuple[LocalTrackUpdate, ...]:
        if frame_index < 0 or observed_us <= 0:
            raise ValueError("reset frame index and timestamp are invalid")
        generation = self._generations.get(camera_id, 0)
        ended = tuple(
            LocalTrackUpdate(
                camera_id=camera_id,
                local_track_id=_track_id(generation, raw_id),
                frame_index=frame_index,
                observed_us=observed_us,
                kind=LocalTrackUpdateKind.ENDED,
            )
            for raw_id in sorted(self._last_seen.get(camera_id, {}))
        )
        tracker = self._trackers.pop(camera_id, None)
        if tracker is not None:
            tracker.reset()
        self._last_seen.pop(camera_id, None)
        self._generations[camera_id] = generation + 1
        return ended

    def _new_tracker(self):
        tracker_type = self._tracker_types[self.config.tracker]
        common = {
            "reid_weights": self.config.reid_weights,
            "device": self.config.device,
            "half": False,
            "max_age": self.config.max_age,
            "min_hits": self.config.minimum_consecutive_frames,
            "per_class": False,
        }
        if self.config.tracker == "botsort":
            return tracker_type(
                frame_rate=round(self.config.frame_rate),
                track_buffer=max(
                    1,
                    round(self.config.max_age * 30 / self.config.frame_rate),
                ),
                use_cmc=False,
                proximity_thresh=self.config.botsort_proximity_threshold,
                appearance_thresh=self.config.botsort_appearance_threshold,
                **common,
            )
        if self.config.tracker == "deepocsort":
            return tracker_type(cmc_off=True, **common)
        if self.config.tracker == "occluboost":
            return tracker_type(
                confirm_hits=self.config.minimum_consecutive_frames,
                gta_max_gap=self.config.max_age * 2,
                recovery_appearance_thresh=(
                    self.config.occluboost_recovery_appearance_threshold
                ),
                gta_appearance_thresh=self.config.occluboost_gta_appearance_threshold,
                **common,
            )
        return tracker_type(n_init=self.config.minimum_consecutive_frames, **common)


def _track_id(generation: int, tracker_id: int) -> str:
    return f"g{generation}-t{tracker_id}"


def _load_dependencies():
    try:
        import numpy as np
        from boxmot import BotSort, DeepOcSort, OccluBoost, StrongSort
    except ModuleNotFoundError as error:
        raise RuntimeError("install boxmot==25.0.0 in .venv-boxmot") from error
    return np, {
        "botsort": BotSort,
        "deepocsort": DeepOcSort,
        "strongsort": StrongSort,
        "occluboost": OccluBoost,
    }
