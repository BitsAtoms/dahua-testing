from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "experiments" / "dahua-netsdk" / "collector"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "track-receiver"))

from dahua_collector.ivs import TRACK_RECORD_BYTES  # noqa: E402
from dahua_collector.live_lane import (  # noqa: E402
    DahuaLiveLane,
    LiveTarget,
    target_from_probe_line,
)
from track_receiver import validate_track_update  # noqa: E402


START = 1_790_676_000.0  # 2026-09-29T10:00:00Z, arbitrary fixed PC time
BOX = (656, 64, 2704, 8144)


def target(object_id: int, offset: float, sequence: int = 100) -> LiveTarget:
    return LiveTarget(object_id, BOX, START + offset, sequence)


class DahuaLiveLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lane = DahuaLiveLane("dahua_213", "20260929T101957Z", clock=lambda: START + 99)

    def feed(self, object_id: int, start: float, end: float, step: float = 0.1) -> list[dict]:
        messages = []
        offset = start
        while offset <= end + 1e-9:
            messages += self.lane.ingest(target(object_id, round(offset, 3)))
            offset += step
        return messages

    def test_track_is_published_on_the_third_target(self) -> None:
        self.assertEqual(self.lane.ingest(target(4863, 0.0)), [])
        self.assertEqual(self.lane.ingest(target(4863, 0.1)), [])
        (message,) = self.lane.ingest(target(4863, 0.2))
        validate_track_update(message)
        self.assertEqual(message["phase"], "new")
        self.assertEqual(message["track_id"], "dahua:dahua_213:live:20260929T101957Z:4863")
        self.assertEqual(message["subject"]["local_track_id"], "4863")
        self.assertEqual(message["quality"]["source_lifecycle"], "live")
        self.assertEqual(message["geometry"]["box"]["x_min"], round(656 / 8192, 6))
        self.assertEqual(message["observed_at"], "2026-09-29T10:00:00.200000+00:00")
        # The join keeps the first sighting, not the confirmation time.
        self.assertEqual(self.lane.track_for_object(4863)[1], START)

    def test_single_frame_ghost_is_never_published(self) -> None:
        self.assertEqual(self.lane.ingest(target(4862, 0.0)), [])
        self.assertEqual(self.lane.expire(START + 2.0), [])
        self.assertEqual(self.lane.stats.unconfirmed_dropped, 1)
        self.assertIsNone(self.lane.track_for_object(4862))

    def test_updates_are_limited_to_the_configured_interval(self) -> None:
        # Published on the third target (0.2 s), then at most every 0.5 s.
        messages = self.feed(4863, 0.0, 2.1)
        phases = [message["phase"] for message in messages]
        self.assertEqual(phases, ["new", "update", "update", "update"])
        self.assertEqual([message["sequence"] for message in messages], [1, 2, 3, 4])

    def test_camera_finalization_ends_the_track_immediately(self) -> None:
        self.feed(4866, 0.0, 1.0)
        (end,) = self.lane.finalize(4866, START + 1.4)
        validate_track_update(end)
        self.assertEqual(end["phase"], "end")
        self.assertEqual(end["quality"]["end_reason"], "camera_finalized")
        self.assertEqual(end["observed_at"], "2026-09-29T10:00:01+00:00")
        self.assertEqual(self.lane.active_object_ids, [])

    def test_face_or_unknown_ids_do_not_end_anything(self) -> None:
        self.feed(4866, 0.0, 1.0)
        self.assertEqual(self.lane.finalize(1004866, START + 1.4), [])
        self.assertEqual(self.lane.active_object_ids, [4866])

    def test_late_frames_after_finalization_do_not_start_a_phantom(self) -> None:
        self.feed(4866, 0.0, 1.0)
        self.lane.finalize(4866, START + 1.4)
        self.assertEqual(self.feed(4866, 1.5, 2.0), [])
        self.assertEqual(self.lane.stats.after_finalization_dropped, 6)

    def test_brief_silence_of_a_seated_person_keeps_the_track(self) -> None:
        # Observed on dahua_212: 3.6 s without targets under the same ObjectID.
        self.feed(132, 0.0, 17.9)
        self.assertEqual(self.lane.expire(START + 17.9 + 3.6), [])
        updates = self.feed(132, 21.5, 22.0)
        self.assertEqual({message["phase"] for message in updates}, {"update"})
        self.assertEqual(self.lane.stats.tracks_started, 1)

    def test_silence_fallback_ends_the_track(self) -> None:
        self.feed(4863, 0.0, 1.0)
        self.assertEqual(self.lane.expire(START + 10.9), [])
        (end,) = self.lane.expire(START + 11.0)
        validate_track_update(end)
        self.assertEqual(end["quality"]["end_reason"], "timeout")

    def test_replayed_connection_frames_are_dropped_and_counted(self) -> None:
        for offset in (0.0, 0.1, 0.2):
            self.assertEqual(self.lane.ingest(target(4869, offset, sequence=-1)), [])
        self.assertEqual(self.lane.stats.replayed_frames_dropped, 3)
        self.assertEqual(self.lane.active_object_ids, [])

    def test_reused_object_id_after_end_gets_a_new_track(self) -> None:
        first = self.feed(4, 0.0, 0.2)[0]["track_id"]
        self.lane.finalize(4, START + 0.5)
        second = self.feed(4, 10.0, 10.2)[0]
        self.assertEqual(second["phase"], "new")
        self.assertEqual(second["track_id"], first + ":2")
        self.assertEqual(self.lane.stats.object_id_reuses, 1)

    def test_concurrent_targets_are_independent_tracks(self) -> None:
        self.feed(23, 0.0, 0.2)
        self.feed(25, 0.05, 0.25)
        self.assertEqual(self.lane.active_object_ids, [23, 25])
        ends = self.lane.close("camera_disconnected")
        self.assertEqual({end["subject"]["local_track_id"] for end in ends}, {"23", "25"})
        self.assertTrue(all(end["quality"]["end_reason"] == "camera_disconnected" for end in ends))

    def test_recently_ended_track_remains_joinable_for_late_photos(self) -> None:
        self.feed(4866, 0.0, 1.0)
        self.lane.finalize(4866, START + 1.4)
        track_id, first_seen, last_seen = self.lane.track_for_object(4866)
        self.assertTrue(track_id.endswith(":4866"))
        self.assertEqual((first_seen, last_seen), (START, START + 1.0))
        self.lane.expire(START + 1.0 + 301)
        self.assertIsNone(self.lane.track_for_object(4866))

    def test_message_ids_are_unique_and_valid(self) -> None:
        messages = self.feed(1, 0.0, 3.0) + self.lane.close()
        ids = [message["message_id"] for message in messages]
        self.assertEqual(len(ids), len(set(ids)))
        for message in messages:
            validate_track_update(message)


def probe_line(frame_type: int = 7, sequence: int = 45082) -> str:
    payload = bytearray(TRACK_RECORD_BYTES)
    struct.pack_into("<I", payload, 36, 22)
    struct.pack_into("<4H", payload, 528, 1680, 4104, 1024, 4040)
    frame = {
        "kind": "ivs", "received_at": "2026-09-29T11:21:01.696Z", "elapsed_ms": 777.2,
        "type": frame_type, "type_name": "TRACK_EX_B0", "length": TRACK_RECORD_BYTES,
        "frame_sequence": sequence, "truncated": False, "encoding": "hex",
        "payload": payload.hex(),
    }
    return "ivs_frame=" + json.dumps(frame)


class ProbeLineTest(unittest.TestCase):
    def test_parses_a_track_record_line(self) -> None:
        target_, frame = target_from_probe_line(probe_line())
        self.assertEqual(target_.object_id, 22)
        self.assertEqual(target_.box, BOX)
        self.assertEqual(target_.frame_sequence, 45082)
        self.assertAlmostEqual(target_.received_at, 1_790_680_861.696, places=3)
        self.assertEqual(frame["type"], 7)

    def test_ignores_other_lines_and_frame_types(self) -> None:
        self.assertIsNone(target_from_probe_line("camera_model=DH-IPC-HDBW7459Z-Z-PV-X"))
        self.assertIsNone(target_from_probe_line(probe_line(frame_type=4)))

    def test_rejects_malformed_frames(self) -> None:
        with self.assertRaises(ValueError):
            target_from_probe_line("ivs_frame={not json")


if __name__ == "__main__":
    unittest.main()
