"""Parsers for live IVS frames captured from the Dahua real-time stream.

The PlaySDK headers shipped with the SDK name the binary IVS structures but do
not define them. The layout below is empirical: it was established on
``dahua_213`` (DH-IPC-HDBW7859Z-Z4-PV-X, firmware 3.146.0000000.55.R) on
2026-09-29 and confirmed by

* exact equality between the record's track ID and the ``ObjectID`` of the
  CGI ``HumanTrait`` events for the same visits, and
* exact equality between the record's box and the ONVIF XML box carried in
  the same stream.

IVS type 4 carries ONVIF ``tt:MetadataStream`` XML. Its ``tt:Object/@ObjectId``
is a per-frame slot index, not the track ID. Coordinates use 0..8191.

IVS type 7 (``TRACK_EX_B0``) carries 2272-byte records:

====== ======= ==========================================================
offset type    meaning
====== ======= ==========================================================
36     uint32  camera-local track ID (equals the CGI HumanTrait ObjectID)
50     uint8   state; 1 on the first frame of most tracks, then 2
528    4x u16  centre x, centre y, half width, half height (0..8191)
====== ======= ==========================================================

Other models or firmware must be verified with the probe before relying on
this layout. The state byte is recorded but not interpreted: a track starts
when a new ID appears and ends when its ID stops arriving.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct


ONVIF_TYPE = 4
TRACK_RECORD_TYPE = 7
TRACK_RECORD_BYTES = 2272
COORDINATE_MAX = 8191

_OBJECT = re.compile(r'<tt:Object ObjectId="(\d+)">(.*?)</tt:Object>', re.S)
_BOX = re.compile(
    r'<tt:BoundingBox left="(-?[\d.]+)" top="(-?[\d.]+)" '
    r'right="(-?[\d.]+)" bottom="(-?[\d.]+)"'
)
_CENTER = re.compile(r'<tt:CenterOfGravity x="(-?[\d.]+)" y="(-?[\d.]+)"')
_UTC = re.compile(r'<tt:Frame UtcTime="([^"]+)"')


@dataclass(frozen=True)
class TrackRecord:
    """One live target from a ``TRACK_EX_B0`` record."""

    track_id: int
    state: int
    center_x: int
    center_y: int
    half_width: int
    half_height: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        """Box as ``(left, top, right, bottom)`` in 0..8191 coordinates."""
        return (
            self.center_x - self.half_width,
            self.center_y - self.half_height,
            self.center_x + self.half_width,
            self.center_y + self.half_height,
        )


@dataclass(frozen=True)
class OnvifObject:
    slot: int
    box: tuple[float, float, float, float]
    center: tuple[float, float] | None


def parse_track_record(payload: bytes) -> TrackRecord:
    if len(payload) != TRACK_RECORD_BYTES:
        raise ValueError(
            f"expected a {TRACK_RECORD_BYTES}-byte TRACK_EX_B0 record, got {len(payload)}"
        )
    track_id = struct.unpack_from("<I", payload, 36)[0]
    center_x, center_y, half_width, half_height = struct.unpack_from("<4H", payload, 528)
    return TrackRecord(track_id, payload[50], center_x, center_y, half_width, half_height)


def parse_onvif_frame(xml: str) -> tuple[str | None, list[OnvifObject]]:
    """Return the frame UTC time and its objects from ONVIF metadata XML."""
    utc = _UTC.search(xml)
    objects = []
    for slot, body in _OBJECT.findall(xml):
        box = _BOX.search(body)
        if box is None:
            continue
        center = _CENTER.search(body)
        objects.append(
            OnvifObject(
                int(slot),
                tuple(float(value) for value in box.groups()),
                None if center is None else tuple(float(value) for value in center.groups()),
            )
        )
    return (utc.group(1) if utc else None), objects


def presence_segments(times: list[float], max_gap: float = 1.0) -> list[tuple[float, float]]:
    """Group sorted timestamps into continuous intervals split by gaps."""
    segments: list[list[float]] = []
    for value in times:
        if segments and value - segments[-1][1] <= max_gap:
            segments[-1][1] = value
        else:
            segments.append([value, value])
    return [(start, end) for start, end in segments]
