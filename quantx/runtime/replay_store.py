from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable


class RuntimeReplayStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def append(self, event: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize_event(event)
        with self.path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def append_all(self, events: Iterable[Any]) -> None:
        for event in events:
            self.append(event)

    def load(self) -> tuple[list[dict[str, Any]], int]:
        if not self.path.exists():
            return [], 0
        rows: list[dict[str, Any]] = []
        invalid = 0
        with self.path.open('r', encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if isinstance(raw, dict):
                    rows.append(raw)
                else:
                    invalid += 1
        return rows, invalid

    @staticmethod
    def market_tape(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if str(row.get('kind', '')) == 'market_event']

    @staticmethod
    def execution_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {'order_event', 'fill_event', 'account_event'}
        return [row for row in rows if str(row.get('kind', '')) in allowed]

    def _serialize_event(self, event: Any) -> dict[str, Any]:
        if is_dataclass(event):
            payload = asdict(event)
        elif isinstance(event, dict):
            payload = dict(event)
        else:
            raise TypeError('runtime replay events must be dataclasses or dicts')
        return self._serialize_value(payload)

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(v) for v in value]
        if isinstance(value, tuple):
            return [self._serialize_value(v) for v in value]
        return value

