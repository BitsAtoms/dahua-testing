from __future__ import annotations

from pathlib import Path
import struct
import sys
import unittest


COLLECTOR_ROOT = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "dahua-netsdk"
    / "collector"
)
sys.path.insert(0, str(COLLECTOR_ROOT))

from dahua_collector.ivs import (  # noqa: E402
    TRACK_RECORD_BYTES,
    parse_onvif_frame,
    parse_track_record,
    presence_segments,
)


def track_record(track_id: int, state: int, centre_x: int, centre_y: int,
                 half_width: int, half_height: int) -> bytes:
    payload = bytearray(TRACK_RECORD_BYTES)
    struct.pack_into("<I", payload, 36, track_id)
    payload[50] = state
    struct.pack_into("<4H", payload, 528, centre_x, centre_y, half_width, half_height)
    return bytes(payload)


ONVIF_SAMPLE = (
    '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
    '<tt:MetadataStream xmlns:tt="http://www.onvif.org/ver10/schema">'
    '<tt:VideoAnalytics><tt:Frame UtcTime="2026-09-29T10:07:57Z">'
    '<tt:Object ObjectId="0"><tt:Appearance><tt:Shape>'
    '<tt:BoundingBox left="656" top="64" right="2704" bottom="8144"/>'
    '<tt:CenterOfGravity x="1680" y="4104"/>'
    '</tt:Shape></tt:Appearance></tt:Object>'
    '</tt:Frame></tt:VideoAnalytics></tt:MetadataStream>'
)


class TrackRecordTest(unittest.TestCase):
    def test_reads_track_id_state_and_box(self) -> None:
        record = parse_track_record(track_record(4863, 1, 1680, 4104, 1024, 4040))
        self.assertEqual(record.track_id, 4863)
        self.assertEqual(record.state, 1)
        self.assertEqual(record.box, (656, 64, 2704, 8144))

    def test_record_box_matches_onvif_box_for_the_same_frame(self) -> None:
        # Observed on dahua_213: both representations describe one target.
        record = parse_track_record(track_record(4860, 1, 1680, 4104, 1024, 4040))
        _, objects = parse_onvif_frame(ONVIF_SAMPLE)
        self.assertEqual(tuple(int(value) for value in objects[0].box), record.box)
        self.assertEqual(objects[0].center, (1680.0, 4104.0))

    def test_rejects_unexpected_record_size(self) -> None:
        with self.assertRaises(ValueError):
            parse_track_record(bytes(100))


class OnvifFrameTest(unittest.TestCase):
    def test_parses_time_slot_and_box(self) -> None:
        utc, objects = parse_onvif_frame(ONVIF_SAMPLE)
        self.assertEqual(utc, "2026-09-29T10:07:57Z")
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].slot, 0)

    def test_frame_without_objects_is_empty(self) -> None:
        utc, objects = parse_onvif_frame(
            '<tt:Frame UtcTime="2026-09-29T10:07:57Z"></tt:Frame>')
        self.assertEqual(utc, "2026-09-29T10:07:57Z")
        self.assertEqual(objects, [])


class PresenceSegmentsTest(unittest.TestCase):
    def test_splits_on_gaps_longer_than_threshold(self) -> None:
        times = [31.0, 31.1, 31.2, 41.2, 55.1, 55.2]
        self.assertEqual(
            presence_segments(times, max_gap=1.0),
            [(31.0, 31.2), (41.2, 41.2), (55.1, 55.2)],
        )

    def test_empty_input(self) -> None:
        self.assertEqual(presence_segments([]), [])


if __name__ == "__main__":
    unittest.main()
