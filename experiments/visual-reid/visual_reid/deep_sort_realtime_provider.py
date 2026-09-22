"""Deep SORT adapter using the existing OpenVINO person ReID model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_tracking import (
    DetectionFrame,
    LocalTrackUpdate,
    LocalTrackUpdateKind,
    PixelBox,
)


@dataclass(frozen=True)
class DeepSortRealtimeConfig:
    reid_model: Path
    device: str = "CPU"
    max_age: int = 30
    n_init: int = 2
    max_cosine_distance: float = 0.3
    max_iou_distance: float = 0.7
    nn_budget: int | None = 100
    gating_only_position: bool = True

    def __post_init__(self) -> None:
        if self.max_age < 0 or self.n_init < 1:
            raise ValueError("tracker lifetime and initialization are invalid")
        if not 0.0 <= self.max_cosine_distance <= 2.0:
            raise ValueError("cosine distance must be between zero and two")
        if not 0.0 <= self.max_iou_distance <= 1.0:
            raise ValueError("IoU distance must be between zero and one")
        if self.nn_budget is not None and self.nn_budget < 1:
            raise ValueError("nn_budget must be positive when configured")


class DeepSortRealtimeProvider:
    """Expose Deep SORT Realtime behind the source-neutral tracker contract."""

    provider_id = "deep-sort-realtime:3341f823+openvino-0287"

    def __init__(
        self,
        config: DeepSortRealtimeConfig,
        *,
        _embedder: Any | None = None,
        _tracker_factory: Any | None = None,
    ) -> None:
        self.config = config
        tracker_factory = _tracker_factory or _load_tracker_factory()
        self._tracker_factory = tracker_factory
        self._embedder = _embedder or _OpenVinoFrameEmbedder(
            config.reid_model, config.device
        )
        self._trackers: dict[str, Any] = {}
        self._active_ids: dict[str, set[str]] = {}
        self._generations: dict[str, int] = {}

    def process(self, frame: DetectionFrame) -> tuple[LocalTrackUpdate, ...]:
        if frame.image is None:
            raise ValueError("Deep SORT ReID requires the source frame")
        tracker = self._trackers.get(frame.camera_id)
        if tracker is None:
            tracker = self._new_tracker()
            self._trackers[frame.camera_id] = tracker
            self._active_ids[frame.camera_id] = set()
            self._generations.setdefault(frame.camera_id, 0)

        raw = [
            (
                [
                    item.box.x1,
                    item.box.y1,
                    item.box.x2 - item.box.x1,
                    item.box.y2 - item.box.y1,
                ],
                item.confidence,
                item.label,
            )
            for item in frame.detections
        ]
        embeddings = self._embedder.embed(frame.image, frame.detections)
        tracks = tracker.update_tracks(raw, embeds=embeddings)

        generation = self._generations[frame.camera_id]
        updates: list[LocalTrackUpdate] = []
        alive_ids: set[str] = set()
        for track in tracks:
            if not track.is_confirmed():
                continue
            raw_id = str(track.track_id)
            alive_ids.add(raw_id)
            if track.time_since_update != 0:
                continue
            box = track.to_ltrb(orig=True, orig_strict=True)
            if box is None:
                continue
            confidence = track.get_det_conf()
            updates.append(
                LocalTrackUpdate(
                    camera_id=frame.camera_id,
                    local_track_id=_track_id(generation, raw_id),
                    frame_index=frame.frame_index,
                    observed_us=frame.observed_us,
                    kind=LocalTrackUpdateKind.OBSERVED,
                    box=PixelBox(*(float(value) for value in box)),
                    confidence=float(confidence) if confidence is not None else 1.0,
                )
            )

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
            tracker.tracker.delete_all_tracks()
        self._active_ids.pop(camera_id, None)
        self._generations[camera_id] = generation + 1
        return ended

    def _new_tracker(self):
        return self._tracker_factory(
            max_iou_distance=self.config.max_iou_distance,
            max_age=self.config.max_age,
            n_init=self.config.n_init,
            nms_max_overlap=1.0,
            max_cosine_distance=self.config.max_cosine_distance,
            nn_budget=self.config.nn_budget,
            gating_only_position=self.config.gating_only_position,
            embedder=None,
        )


class _OpenVinoFrameEmbedder:
    def __init__(self, model_path: Path, device: str) -> None:
        try:
            import cv2
            import numpy as np
            import openvino as ov
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "install requirements-local-tracker.txt in .venv-trackers"
            ) from error
        self._cv2 = cv2
        self._np = np
        core = ov.Core()
        model = core.read_model(model_path)
        self._compiled = core.compile_model(model, device)
        self._input = self._compiled.input(0)
        self._output = self._compiled.output(0)

    def embed(self, image, detections):
        cv2 = self._cv2
        np = self._np
        vectors = []
        height, width = image.shape[:2]
        for item in detections:
            left = max(0, min(width - 1, round(item.box.x1)))
            top = max(0, min(height - 1, round(item.box.y1)))
            right = max(left + 1, min(width, round(item.box.x2)))
            bottom = max(top + 1, min(height, round(item.box.y2)))
            crop = image[top:bottom, left:right]
            resized = cv2.resize(crop, (128, 256), interpolation=cv2.INTER_LINEAR)
            tensor = resized.transpose(2, 0, 1)[None].astype(np.float32)
            vector = self._compiled([tensor])[self._output].reshape(-1).astype(np.float32)
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                raise ValueError("OpenVINO ReID produced a zero embedding")
            vectors.append(vector / norm)
        return vectors


def _track_id(generation: int, tracker_id: str) -> str:
    return f"g{generation}-t{tracker_id}"


def _load_tracker_factory():
    try:
        from deep_sort_realtime.deepsort_tracker import DeepSort
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "install requirements-local-tracker.txt in .venv-trackers"
        ) from error
    return DeepSort
