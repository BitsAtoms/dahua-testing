"""Asynchronous retrieval of finalized Frigate event snapshots."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import queue
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .adapter import FrigateEventAdapter


MAX_SNAPSHOT_BYTES = 15 * 1024 * 1024


class SnapshotFetchError(RuntimeError):
    """Raised when a Frigate snapshot cannot be fetched or validated."""


@dataclass(frozen=True)
class SnapshotResult:
    camera_id: str
    local_track_id: str
    update: dict[str, Any] | None
    byte_count: int = 0
    error: str | None = None


def validate_api_url(value: str) -> str:
    """Validate an HTTP API base URL without embedded credentials."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("FRIGATE_API_URL must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("FRIGATE_API_URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("FRIGATE_API_URL must not contain query or fragment")
    return value.rstrip("/")


def fetch_snapshot(
    api_url: str,
    event_id: str,
    destination: Path,
    *,
    attempts: int = 5,
    timeout_seconds: float = 5.0,
    retry_delay_seconds: float = 0.25,
) -> int:
    """Fetch, validate, and atomically save one JPEG snapshot."""
    if attempts <= 0:
        raise ValueError("snapshot attempts must be positive")
    url = (
        f"{validate_api_url(api_url)}/api/events/"
        f"{quote(event_id, safe='')}/snapshot.jpg"
    )
    last_error = "snapshot unavailable"
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"Accept": "image/jpeg"})
            with urlopen(request, timeout=timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                if content_type != "image/jpeg":
                    raise SnapshotFetchError(
                        f"unexpected snapshot content type: {content_type}"
                    )
                data = response.read(MAX_SNAPSHOT_BYTES + 1)
            if len(data) > MAX_SNAPSHOT_BYTES:
                raise SnapshotFetchError("snapshot exceeds size limit")
            if len(data) < 4 or not data.startswith(b"\xff\xd8\xff") or not data.endswith(
                b"\xff\xd9"
            ):
                raise SnapshotFetchError("invalid JPEG snapshot")

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".part")
            try:
                temporary.write_bytes(data)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            return len(data)
        except (HTTPError, URLError, OSError, SnapshotFetchError) as error:
            last_error = _safe_error(error)
            if attempt + 1 < attempts:
                time.sleep(retry_delay_seconds * (2**attempt))
    raise SnapshotFetchError(last_error)


class SnapshotEnricher:
    """Run snapshot downloads away from the MQTT processing thread."""

    def __init__(
        self,
        adapter: FrigateEventAdapter,
        api_url: str,
        media_root: Path,
        *,
        workers: int = 2,
        max_pending: int = 100,
    ) -> None:
        if workers <= 0 or max_pending <= 0:
            raise ValueError("snapshot workers and pending limit must be positive")
        self._adapter = adapter
        self._api_url = validate_api_url(api_url)
        self._media_root = media_root
        self._max_pending = max_pending
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="frigate-snapshot"
        )
        self._results: queue.Queue[SnapshotResult] = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0

    @property
    def pending_count(self) -> int:
        with self._lock:
            return self._pending

    def submit(
        self,
        lifecycle_update: dict[str, Any],
        snapshot_timestamp: float,
    ) -> bool:
        """Schedule one download, returning false when the media queue is full."""
        with self._lock:
            if self._pending >= self._max_pending:
                return False
            self._pending += 1
        future = self._executor.submit(
            self._fetch_and_build, lifecycle_update, snapshot_timestamp
        )
        future.add_done_callback(self._completed)
        return True

    def drain(self) -> list[SnapshotResult]:
        """Return all currently completed results without blocking."""
        results: list[SnapshotResult] = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                return results

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _fetch_and_build(
        self,
        lifecycle_update: dict[str, Any],
        snapshot_timestamp: float,
    ) -> SnapshotResult:
        camera_id = lifecycle_update["camera_id"]
        local_track_id = lifecycle_update["subject"]["local_track_id"]
        filename = (
            f"{_safe_component(camera_id)}_"
            f"{_safe_component(local_track_id)}_snapshot.jpg"
        )
        destination = self._media_root / filename
        try:
            byte_count = fetch_snapshot(
                self._api_url,
                lifecycle_update["source_ref"]["event_id"],
                destination,
            )
            update = self._adapter.snapshot_update(
                lifecycle_update, destination, snapshot_timestamp
            )
            return SnapshotResult(
                camera_id, local_track_id, update, byte_count=byte_count
            )
        except (OSError, SnapshotFetchError, ValueError) as error:
            return SnapshotResult(
                camera_id, local_track_id, None, error=_safe_error(error)
            )

    def _completed(self, future: Future[SnapshotResult]) -> None:
        try:
            result = future.result()
        except Exception as error:  # defensive boundary around worker failures
            result = SnapshotResult("unknown", "unknown", None, error=_safe_error(error))
        self._results.put(result)
        with self._lock:
            self._pending -= 1


def _safe_component(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:160]


def _safe_error(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, URLError):
        return f"connection error: {error.reason}"
    return str(error).replace("\r", " ").replace("\n", " ")[:240]
