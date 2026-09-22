"""Bounded in-memory JPEG ring buffer for adaptive RTSP capture."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class BufferedFrame:
    observed_us: int
    jpeg: bytes
    width: int
    height: int


class RollingJpegBuffer:
    def __init__(
        self,
        *,
        max_seconds: float = 10.0,
        max_frames: int = 100,
        max_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if max_seconds <= 0 or max_frames <= 0 or max_bytes <= 0:
            raise ValueError("buffer limits must be positive")
        self.max_age_us = round(max_seconds * 1_000_000)
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._frames: deque[BufferedFrame] = deque()
        self._bytes = 0
        self._lock = threading.Lock()

    def append_jpeg(
        self, jpeg: bytes, width: int, height: int, observed_us: int
    ) -> bool:
        if observed_us <= 0 or width <= 0 or height <= 0:
            raise ValueError("frame metadata must be positive")
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("frame is not a complete JPEG")
        if len(jpeg) > self.max_bytes:
            return False
        frame = BufferedFrame(observed_us, bytes(jpeg), width, height)
        with self._lock:
            self._frames.append(frame)
            self._bytes += len(frame.jpeg)
            self._trim(observed_us)
        return True

    def append_image(self, image, observed_us: int, quality: int = 85) -> bool:
        try:
            import cv2
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "install experiments/visual-reid/requirements.txt in its venv"
            ) from error
        if not 1 <= quality <= 100:
            raise ValueError("JPEG quality must be between 1 and 100")
        if image is None or len(image.shape) < 2:
            raise ValueError("image must be decoded")
        height, width = image.shape[:2]
        ok, encoded = cv2.imencode(
            ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        )
        if not ok:
            return False
        return self.append_jpeg(encoded.tobytes(), width, height, observed_us)

    def window(
        self,
        center_us: int,
        *,
        before_seconds: float = 5.0,
        after_seconds: float = 1.0,
    ) -> tuple[BufferedFrame, ...]:
        if before_seconds < 0 or after_seconds < 0:
            raise ValueError("window durations cannot be negative")
        start = center_us - round(before_seconds * 1_000_000)
        end = center_us + round(after_seconds * 1_000_000)
        with self._lock:
            return tuple(
                frame for frame in self._frames if start <= frame.observed_us <= end
            )

    def stats(self) -> dict[str, int]:
        with self._lock:
            newest = self._frames[-1] if self._frames else None
            return {
                "frames": len(self._frames),
                "bytes": self._bytes,
                "oldest_us": self._frames[0].observed_us if self._frames else 0,
                "newest_us": newest.observed_us if newest else 0,
                "latest_width": newest.width if newest else 0,
                "latest_height": newest.height if newest else 0,
            }

    def _trim(self, newest_us: int) -> None:
        cutoff = newest_us - self.max_age_us
        while self._frames and (
            self._frames[0].observed_us < cutoff
            or len(self._frames) > self.max_frames
            or self._bytes > self.max_bytes
        ):
            removed = self._frames.popleft()
            self._bytes -= len(removed.jpeg)


class RtspBufferWorker:
    """Sample an RTSP stream into a rolling buffer without exposing its URL."""

    def __init__(
        self,
        camera_id: str,
        url: str,
        buffer: RollingJpegBuffer,
        *,
        target_fps: float = 2.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not camera_id or not url:
            raise ValueError("camera_id and RTSP URL are required")
        if target_fps <= 0:
            raise ValueError("target_fps must be positive")
        self.camera_id = camera_id
        self._url = url
        self.buffer = buffer
        self.target_fps = target_fps
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._last_error: str | None = None
        self._captured = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"rtsp-buffer-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def set_target_fps(self, value: float) -> None:
        if value <= 0:
            raise ValueError("target_fps must be positive")
        with self._lock:
            self.target_fps = value

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "camera_id": self.camera_id,
                "running": self._thread is not None and self._thread.is_alive(),
                "connected": self._connected,
                "target_fps": self.target_fps,
                "captured": self._captured,
                "last_error": self._last_error,
                "buffer": self.buffer.stats(),
            }

    def _run(self) -> None:
        try:
            import cv2
        except ModuleNotFoundError as error:
            with self._lock:
                self._last_error = str(error)
            return
        retry = 0.5
        while not self._stop.is_set():
            capture = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
            if not capture.isOpened():
                self._set_connection(False, "RTSP connection failed")
                self._stop.wait(retry)
                retry = min(10.0, retry * 2)
                continue
            self._set_connection(True, None)
            retry = 0.5
            next_sample = 0.0
            try:
                while not self._stop.is_set():
                    ok, image = capture.read()
                    if not ok:
                        self._set_connection(False, "RTSP read failed")
                        break
                    now = self._clock()
                    with self._lock:
                        interval = 1.0 / self.target_fps
                    if now < next_sample:
                        continue
                    next_sample = now + interval
                    if self.buffer.append_image(image, round(now * 1_000_000)):
                        with self._lock:
                            self._captured += 1
            finally:
                capture.release()
        self._set_connection(False, None)

    def _set_connection(self, connected: bool, error: str | None) -> None:
        with self._lock:
            self._connected = connected
            self._last_error = error
