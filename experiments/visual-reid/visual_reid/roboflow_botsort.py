"""Optional adapter for the isolated Roboflow Trackers BoT-SORT provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .local_tracking import (
    DetectionFrame,
    LocalTrackUpdate,
    LocalTrackUpdateKind,
    PixelBox,
)


@dataclass(frozen=True)
class RoboflowBoTSORTConfig:
    frame_rate: float = 10.0
    lost_track_buffer: int = 30
    track_activation_threshold: float = 0.7
    minimum_consecutive_frames: int = 2
    high_conf_det_threshold: float = 0.6
    enable_cmc: bool = False

    def __post_init__(self) -> None:
        if self.frame_rate <= 0 or self.lost_track_buffer < 0:
            raise ValueError("tracker rate must be positive and buffer non-negative")
        for value in (
            self.track_activation_threshold,
            self.high_conf_det_threshold,
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError("tracker thresholds must be between zero and one")
        if self.minimum_consecutive_frames < 1:
            raise ValueError("minimum_consecutive_frames must be positive")


class RoboflowBoTSORTProvider:
    """Translate the external tracker API into source-neutral local updates."""

    provider_id = "roboflow-trackers-botsort:2.6.0"

    def __init__(self, config: RoboflowBoTSORTConfig | None = None) -> None:
        self.config = config or RoboflowBoTSORTConfig()
        self._trackers: dict[str, Any] = {}
        self._active_ids: dict[str, set[int]] = {}
        self._generations: dict[str, int] = {}

    def process(self, frame: DetectionFrame) -> tuple[LocalTrackUpdate, ...]:
        np, sv, tracker_type = _load_dependencies()
        tracker = self._trackers.get(frame.camera_id)
        if tracker is None:
            tracker = tracker_type(
                frame_rate=self.config.frame_rate,
                lost_track_buffer=self.config.lost_track_buffer,
                track_activation_threshold=self.config.track_activation_threshold,
                minimum_consecutive_frames=self.config.minimum_consecutive_frames,
                high_conf_det_threshold=self.config.high_conf_det_threshold,
                enable_cmc=self.config.enable_cmc,
            )
            self._trackers[frame.camera_id] = tracker
            self._active_ids[frame.camera_id] = set()
            self._generations.setdefault(frame.camera_id, 0)

        boxes = np.asarray(
            [
                [item.box.x1, item.box.y1, item.box.x2, item.box.y2]
                for item in frame.detections
            ],
            dtype=np.float32,
        ).reshape((-1, 4))
        confidence = np.asarray(
            [item.confidence for item in frame.detections], dtype=np.float32
        )
        class_id = np.zeros(len(frame.detections), dtype=int)
        detections = sv.Detections(
            xyxy=boxes,
            confidence=confidence,
            class_id=class_id,
        )
        tracked = tracker.update(
            detections,
            frame=frame.image,
            timestamp=frame.observed_us / 1_000_000,
        )

        generation = self._generations[frame.camera_id]
        updates: list[LocalTrackUpdate] = []
        tracker_ids = tracked.tracker_id
        if tracker_ids is not None:
            for index, raw_id in enumerate(tracker_ids.tolist()):
                track_id = int(raw_id)
                if track_id < 0:
                    continue
                box = tracked.xyxy[index]
                score = (
                    float(tracked.confidence[index])
                    if tracked.confidence is not None
                    else 1.0
                )
                updates.append(
                    LocalTrackUpdate(
                        camera_id=frame.camera_id,
                        local_track_id=_track_id(generation, track_id),
                        frame_index=frame.frame_index,
                        observed_us=frame.observed_us,
                        kind=LocalTrackUpdateKind.OBSERVED,
                        box=PixelBox(*(float(value) for value in box)),
                        confidence=score,
                    )
                )

        alive = tracker.tracked_objects.tracker_id
        alive_ids = set() if alive is None else {int(value) for value in alive.tolist()}
        ended_ids = self._active_ids[frame.camera_id] - alive_ids
        updates.extend(
            LocalTrackUpdate(
                camera_id=frame.camera_id,
                local_track_id=_track_id(generation, track_id),
                frame_index=frame.frame_index,
                observed_us=frame.observed_us,
                kind=LocalTrackUpdateKind.ENDED,
            )
            for track_id in sorted(ended_ids)
        )
        self._active_ids[frame.camera_id] = alive_ids
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
                local_track_id=_track_id(generation, track_id),
                frame_index=frame_index,
                observed_us=observed_us,
                kind=LocalTrackUpdateKind.ENDED,
            )
            for track_id in sorted(self._active_ids.get(camera_id, set()))
        )
        tracker = self._trackers.pop(camera_id, None)
        if tracker is not None:
            tracker.reset()
        self._active_ids.pop(camera_id, None)
        self._generations[camera_id] = generation + 1
        return ended


def _track_id(generation: int, tracker_id: int) -> str:
    return f"g{generation}-t{tracker_id}"


def _load_dependencies():
    try:
        import numpy as np
        import supervision as sv
        from trackers import BoTSORTTracker
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install requirements-local-tracker.txt in .venv-trackers"
        ) from error
    return np, sv, BoTSORTTracker
