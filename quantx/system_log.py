"""Structured logging primitives for trade/system/alert events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal, Protocol


LogCategory = Literal["trade", "system", "alert"]
LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]


@dataclass(slots=True)
class LogEvent:
    category: LogCategory
    event: str
    level: LogLevel = "INFO"
    ts: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    symbol: str | None = None
    client_order_id: str | None = None
    stage: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class EventLogger(Protocol):
    def log(self, event: LogEvent) -> None:
        ...


class JsonlEventLogger:
    """Append-only jsonl logger for structured operational events."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: LogEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n")


class MemoryEventLogger:
    """In-memory logger for tests and local debugging."""

    def __init__(self):
        self.events: list[LogEvent] = []

    def log(self, event: LogEvent) -> None:
        self.events.append(event)
