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
class RuntimeHealthState:
    replay_persistence: bool = False
    recovery_mode: str = 'live'
    reconcile_report: dict[str, Any] | None = None
    stream_started_ts: str | None = None
    last_stream_event_ts: str | None = None
    last_degrade_reason: str | None = None
    last_error: dict[str, Any] | None = None

    def mark_replay_persistence(self, available: bool) -> None:
        self.replay_persistence = bool(available)

    def mark_reconcile(self, report: dict[str, Any] | None) -> None:
        self.reconcile_report = dict(report) if report is not None else None
        if self.reconcile_report and self.reconcile_report.get('severity') == 'block':
            self.last_degrade_reason = 'reconcile_blocked'

    def mark_stream_started(self, ts: str) -> None:
        self.stream_started_ts = ts

    def mark_stream_event(self, ts: str) -> None:
        if self.stream_started_ts is None:
            self.stream_started_ts = ts
        self.last_stream_event_ts = ts

    def mark_apply_error(self, exc: Exception, *, stage: str) -> None:
        self.last_error = {
            'stage': stage,
            'error': str(exc),
            'type': type(exc).__name__,
        }
        self.last_degrade_reason = stage

    def snapshot(self, *, now_ts: str | None = None, stale_after_s: int = 30) -> dict[str, Any]:
        stale = self._is_stream_stale(now_ts=now_ts, stale_after_s=stale_after_s)
        reconcile = self.reconcile_report or {}
        reconcile_ok = bool(reconcile.get('ok', True))

        degraded = bool(self.last_error) or stale or not reconcile_ok or self.recovery_mode in {'blocked', 'read_only', 'reduce_only', 'cold'}
        execution_mode = self._derive_execution_mode(stale=stale, reconcile=reconcile)

        if stale:
            self.last_degrade_reason = 'stream_stale'

        return {
            'replay_persistence': self.replay_persistence,
            'degraded': degraded,
            'recovery_mode': self.recovery_mode,
            'reconcile_ok': reconcile_ok,
            'reconcile': dict(reconcile),
            'execution_mode': execution_mode,
            'last_degrade_reason': self.last_degrade_reason,
            'last_error': dict(self.last_error) if self.last_error is not None else None,
            'stream': {
                'started_at': self.stream_started_ts,
                'last_event_ts': self.last_stream_event_ts,
                'stale': stale,
            },
        }

    def _derive_execution_mode(self, *, stale: bool, reconcile: dict[str, Any]) -> str:
        if self.last_error or stale or reconcile.get('severity') == 'block' or self.recovery_mode in {'blocked', 'cold'}:
            return 'blocked'
        if self.recovery_mode == 'read_only':
            return 'read_only'
        if self.recovery_mode == 'reduce_only' or reconcile.get('severity') == 'warn':
            return 'reduce_only'
        return 'live'

    def _is_stream_stale(self, *, now_ts: str | None, stale_after_s: int) -> bool:
        if stale_after_s <= 0:
            return False

        anchor_ts = self.last_stream_event_ts or self.stream_started_ts
        if anchor_ts is None or now_ts is None:
            return False

        now_dt = _parse_ts(now_ts)
        anchor_dt = _parse_ts(anchor_ts)
        if now_dt is None or anchor_dt is None:
            return False
        return (now_dt - anchor_dt).total_seconds() > stale_after_s
