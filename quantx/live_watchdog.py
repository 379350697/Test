from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(slots=True)
class LiveWatchdogResult:
    ok: bool
    status: str
    reason: str
    should_alert: bool
    detail: dict[str, Any]


def evaluate_live_watchdog(
    *,
    status_payload: Mapping[str, Any],
    process_alive: bool,
    now: str | datetime,
    stale_after_s: int,
) -> LiveWatchdogResult:
    process = status_payload.get('process', {}) if isinstance(status_payload.get('process'), Mapping) else {}
    runtime = status_payload.get('runtime', {}) if isinstance(status_payload.get('runtime'), Mapping) else {}
    supervisor = status_payload.get('supervisor', {}) if isinstance(status_payload.get('supervisor'), Mapping) else {}

    current_time = _coerce_utc_datetime(now)
    updated_at_raw = runtime.get('updated_at')
    updated_at = _coerce_utc_datetime(updated_at_raw) if updated_at_raw else None
    age_s = None if updated_at is None else max(0, int((current_time - updated_at).total_seconds()))
    supervisor_state = str(supervisor.get('state', '') or '').lower()
    execution_mode = str(runtime.get('execution_mode', supervisor.get('execution_mode', '')) or '').lower()
    degrade_reason = str(supervisor.get('last_degrade_reason', '') or '')

    detail = {
        'pid': int(process.get('pid', 0) or 0) if process.get('pid') is not None else 0,
        'process_alive': bool(process_alive),
        'started_at': process.get('started_at'),
        'updated_at': updated_at.isoformat() if updated_at is not None else None,
        'status_age_s': age_s,
        'stale_after_s': int(stale_after_s),
        'supervisor_state': supervisor_state or None,
        'execution_mode': execution_mode or None,
    }

    if detail['pid'] and not process_alive:
        return LiveWatchdogResult(
            ok=False,
            status='blocked',
            reason='process_dead',
            should_alert=True,
            detail=detail,
        )

    if age_s is None:
        return LiveWatchdogResult(
            ok=False,
            status='blocked',
            reason='status_missing',
            should_alert=True,
            detail=detail,
        )

    if age_s > int(stale_after_s):
        return LiveWatchdogResult(
            ok=False,
            status='blocked',
            reason='status_stale',
            should_alert=True,
            detail=detail,
        )

    if supervisor_state == 'blocked' or execution_mode == 'blocked':
        reason = degrade_reason or 'blocked'
        return LiveWatchdogResult(
            ok=False,
            status='blocked',
            reason=reason,
            should_alert=True,
            detail=detail,
        )

    if supervisor_state == 'reduce_only' or execution_mode == 'reduce_only':
        reason = degrade_reason or 'reduce_only'
        return LiveWatchdogResult(
            ok=False,
            status='reduce_only',
            reason=reason,
            should_alert=True,
            detail=detail,
        )

    if supervisor_state == 'read_only' or execution_mode == 'read_only':
        reason = degrade_reason or 'read_only'
        return LiveWatchdogResult(
            ok=False,
            status='read_only',
            reason=reason,
            should_alert=True,
            detail=detail,
        )

    return LiveWatchdogResult(
        ok=True,
        status='ok',
        reason='healthy',
        should_alert=False,
        detail=detail,
    )


def _coerce_utc_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
