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
    """Append-only jsonl logger for structured operational events.

    Supports optional lightweight size-based rotation for personal deployments.
    """

    def __init__(self, path: str, *, max_bytes: int = 0, backup_count: int = 3):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(0, int(max_bytes))
        self.backup_count = max(0, int(backup_count))

    def log(self, event: LogEvent) -> None:
        line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n"
        self._rotate_if_needed(len(line.encode("utf-8")))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self.max_bytes <= 0:
            return
        if not self.path.exists():
            return
        if self.path.stat().st_size + incoming_bytes <= self.max_bytes:
            return

        if self.backup_count > 0:
            for idx in range(self.backup_count - 1, 0, -1):
                src = self.path.with_name(f"{self.path.name}.{idx}")
                dst = self.path.with_name(f"{self.path.name}.{idx + 1}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
            first = self.path.with_name(f"{self.path.name}.1")
            if first.exists():
                first.unlink()
            self.path.rename(first)
        else:
            self.path.unlink(missing_ok=True)


class MemoryEventLogger:
    """In-memory logger for tests and local debugging."""

    def __init__(self):
        self.events: list[LogEvent] = []

    def log(self, event: LogEvent) -> None:
        self.events.append(event)
