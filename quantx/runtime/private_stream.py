from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace('Z', '+00:00')
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(slots=True)
class PrivateStreamSupervisor:
    stale_after_s: int = 30
    reconnect_backoff_s: int = 1
    state: str = 'idle'
    connected_at: str | None = None
    last_message_ts: str | None = None
    last_disconnect_ts: str | None = None
    disconnect_reason: str | None = None
    gap_detected: bool = False
    reconcile_required: bool = False

    def mark_connected(self, ts: str) -> None:
        self.connected_at = ts
        if self.last_disconnect_ts is not None:
            self.state = 'reconnected'
            self.reconcile_required = True
        else:
            self.state = 'connected'

    def mark_message(self, ts: str) -> None:
        if self.connected_at is None:
            self.connected_at = ts
        self.last_message_ts = ts
        if self.state == 'idle':
            self.state = 'connected'

    def mark_disconnect(self, ts: str, *, reason: str) -> None:
        self.last_disconnect_ts = ts
        self.disconnect_reason = reason
        self.gap_detected = True
        self.reconcile_required = True
        self.state = 'disconnected'

    def snapshot(self, *, now_ts: str) -> dict[str, Any]:
        stale = self._is_stale(now_ts)
        state = self.state
        gap_detected = self.gap_detected
        if stale:
            state = 'stale'
            gap_detected = True

        return {
            'state': state,
            'connected_at': self.connected_at,
            'last_message_ts': self.last_message_ts,
            'last_disconnect_ts': self.last_disconnect_ts,
            'disconnect_reason': self.disconnect_reason,
            'gap_detected': gap_detected,
            'reconcile_required': self.reconcile_required,
            'stale': stale,
            'reconnect_backoff_s': self.reconnect_backoff_s,
        }

    def _is_stale(self, now_ts: str) -> bool:
        if self.stale_after_s <= 0:
            return False
        anchor = self.last_message_ts or self.connected_at
        if anchor is None:
            return False
        now_dt = _parse_ts(now_ts)
        anchor_dt = _parse_ts(anchor)
        if now_dt is None or anchor_dt is None:
            return False
        return (now_dt - anchor_dt).total_seconds() > self.stale_after_s
