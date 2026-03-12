from __future__ import annotations

from datetime import datetime
from typing import Any

from .runtime.replay_store import RuntimeReplayStore


def run_paper_harness(*, event_log_path: str, duration_minutes: int = 60) -> dict[str, object]:
    rows, invalid_event_lines = RuntimeReplayStore(event_log_path).load()
    ordered_rows = sorted(rows, key=lambda row: _parse_ts(row.get('ts')).timestamp() if _parse_ts(row.get('ts')) is not None else 0.0)

    duration_minutes = max(0, int(duration_minutes))
    continuous_minutes = _continuous_minutes(ordered_rows, duration_minutes=duration_minutes)
    incident_counts = {
        'warnings': 0,
        'errors': 0,
        'rejections': 0,
        'retries': 0,
        'invalid_event_lines': invalid_event_lines,
    }
    last_error: dict[str, object] | None = None

    for row in ordered_rows:
        event_name = str(row.get('event', row.get('kind', 'unknown')))
        payload = row.get('payload', {}) if isinstance(row.get('payload'), dict) else {}
        level = str(row.get('level', payload.get('level', 'INFO'))).upper()

        if level == 'WARN':
            incident_counts['warnings'] += 1
        if level == 'ERROR':
            incident_counts['errors'] += 1
            last_error = {
                'event': event_name,
                'ts': str(row.get('ts', '')),
                'level': level,
            }
        if event_name == 'place_order_retry':
            incident_counts['retries'] += 1
        if event_name == 'order_rejected' or str(row.get('status', '')) == 'rejected':
            incident_counts['rejections'] += 1
            if last_error is None:
                last_error = {
                    'event': event_name,
                    'ts': str(row.get('ts', '')),
                    'level': level,
                }

    alerts_ok = incident_counts['errors'] == 0 and incident_counts['invalid_event_lines'] == 0
    promotion_ready = (
        duration_minutes > 0
        and continuous_minutes >= duration_minutes
        and incident_counts['errors'] == 0
        and incident_counts['rejections'] == 0
        and incident_counts['invalid_event_lines'] == 0
    )

    return {
        'event_log_path': event_log_path,
        'duration_minutes': duration_minutes,
        'continuous_minutes': continuous_minutes,
        'incident_counts': incident_counts,
        'alerts_ok': alerts_ok,
        'promotion_ready': promotion_ready,
        'health': {
            'degraded': not alerts_ok,
            'last_error': last_error,
        },
    }


def _continuous_minutes(rows: list[dict[str, Any]], *, duration_minutes: int) -> int:
    timestamps = [dt for dt in (_parse_ts(row.get('ts')) for row in rows) if dt is not None]
    if not timestamps:
        return 0
    span_minutes = int((timestamps[-1] - timestamps[0]).total_seconds() // 60) + 1
    if duration_minutes <= 0:
        return max(0, span_minutes)
    return max(0, min(duration_minutes, span_minutes))


def _parse_ts(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
