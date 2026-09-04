"""Output destinations for normalized collector events."""

from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from typing import Any, Protocol, TextIO


class EventSink(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class JsonlEventSink:
    """Durable append-only sink used by the isolated experiment."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._output: TextIO = path.open("a", encoding="utf-8", newline="\n")

    def publish(self, event: dict[str, Any]) -> None:
        self._output.write(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._output.flush()

    def close(self) -> None:
        self._output.close()

    def __enter__(self) -> JsonlEventSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class CompositeEventSink:
    """Fan out one event to several destinations in a defined order."""

    def __init__(self, sinks: Iterable[EventSink]) -> None:
        self._sinks = list(sinks)

    def publish(self, event: dict[str, Any]) -> None:
        for sink in self._sinks:
            sink.publish(event)

    def close(self) -> None:
        for sink in reversed(self._sinks):
            sink.close()
