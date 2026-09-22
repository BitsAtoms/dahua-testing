from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "visual-reid"
sys.path.insert(0, str(EXPERIMENT))

from visual_reid.deep_sort_realtime_provider import (  # noqa: E402
    DeepSortRealtimeConfig,
    DeepSortRealtimeProvider,
)
from visual_reid.local_tracking import (  # noqa: E402
    DetectionFrame,
    FrameDetection,
    LocalTrackUpdateKind,
    PixelBox,
)


class _Embedder:
    def __init__(self):
        self.calls = 0

    def embed(self, image, detections):
        self.calls += 1
        return [[1.0, 0.0] for _ in detections]


class _Track:
    def __init__(self, track_id="7", *, updated=True, confirmed=True):
        self.track_id = track_id
        self.time_since_update = 0 if updated else 1
        self._confirmed = confirmed

    def is_confirmed(self):
        return self._confirmed

    def to_ltrb(self, **_kwargs):
        return [10.0, 20.0, 40.0, 80.0]

    def get_det_conf(self):
        return 0.85


class _CoreTracker:
    def __init__(self):
        self.deleted = False

    def delete_all_tracks(self):
        self.deleted = True


class _DeepSort:
    def __init__(self, sequences):
        self.sequences = list(sequences)
        self.tracker = _CoreTracker()
        self.calls = []

    def update_tracks(self, raw, embeds):
        self.calls.append((raw, embeds))
        return self.sequences.pop(0)


class DeepSortRealtimeProviderTests(unittest.TestCase):
    def setUp(self):
        self.embedder = _Embedder()
        self.instances = []

    def provider(self, sequences):
        def factory(**_kwargs):
            instance = _DeepSort(sequences)
            self.instances.append(instance)
            return instance

        return DeepSortRealtimeProvider(
            DeepSortRealtimeConfig(Path("unused.xml")),
            _embedder=self.embedder,
            _tracker_factory=factory,
        )

    @staticmethod
    def frame(index=0, *, image=object()):
        return DetectionFrame(
            camera_id="meeting",
            frame_index=index,
            observed_us=(index + 1) * 100_000,
            width=100,
            height=100,
            detections=(FrameDetection(PixelBox(10, 20, 40, 80), 0.85),),
            image=image,
        )

    def test_emits_only_current_confirmed_observations_and_end(self):
        provider = self.provider(
            [[_Track()], [_Track(updated=False)], []]
        )

        observed = provider.process(self.frame(0))
        predicted = provider.process(self.frame(1))
        ended = provider.process(self.frame(2))

        self.assertEqual(observed[0].local_track_id, "g0-t7")
        self.assertEqual(observed[0].kind, LocalTrackUpdateKind.OBSERVED)
        self.assertEqual(observed[0].box, PixelBox(10, 20, 40, 80))
        self.assertEqual(predicted, ())
        self.assertEqual(ended[0].kind, LocalTrackUpdateKind.ENDED)
        self.assertEqual(self.embedder.calls, 3)

    def test_reset_ends_tracks_and_advances_generation(self):
        provider = self.provider([[_Track()]])
        provider.process(self.frame(0))

        ended = provider.reset("meeting", frame_index=1, observed_us=200_000)

        self.assertEqual(ended[0].local_track_id, "g0-t7")
        self.assertTrue(self.instances[0].tracker.deleted)

        provider.process(self.frame(2))
        self.assertEqual(len(self.instances), 2)

    def test_requires_source_frame(self):
        provider = self.provider([])
        with self.assertRaisesRegex(ValueError, "source frame"):
            provider.process(self.frame(image=None))


if __name__ == "__main__":
    unittest.main()
