"""Daily replay summary generator for personal live trading operations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any

from .audit import JsonlAuditStore
from .oms import JsonlOMSStore


@dataclass(slots=True)
class DailyReplayReport:
    day: str
    event_counts: dict[str, int]
    level_counts: dict[str, int]
    reject_reason_top: list[tuple[str, int]]
    retries: int
    accepted: int
    rejected: int
    oms_event_counts: dict[str, int]
    audit_ok: bool
    audit_events: int



def _iter_jsonl(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _same_day(ts: str, day: date) -> bool:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.date() == day


def build_daily_replay_report(
    *,
    event_log_path: str,
    oms_store_path: str | None = None,
    audit_store_path: str | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    target_day = date.fromisoformat(day) if day else datetime.utcnow().date()

    events = [r for r in _iter_jsonl(event_log_path) if _same_day(str(r.get("ts", "")), target_day)]

    event_counter: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    retries = 0
    accepted = 0
    rejected = 0

    for ev in events:
        event_name = str(ev.get("event", "unknown"))
        event_counter[event_name] += 1
        level_counter[str(ev.get("level", "INFO"))] += 1

        payload_raw = ev.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        if event_name == "place_order_retry":
            retries += 1
        if event_name == "order_accepted":
            accepted += 1
        if event_name == "order_rejected":
            rejected += 1
            reason = str(payload.get("reason", "unknown"))
            reason_counter[reason] += 1

    oms_event_counts: dict[str, int] = {}
    if oms_store_path:
        oms_events = JsonlOMSStore(oms_store_path).load()
        c: Counter[str] = Counter()
        for oms_ev in oms_events:
            if _same_day(oms_ev.ts, target_day):
                c[oms_ev.event] += 1
        oms_event_counts = dict(c)

    audit_ok = True
    audit_events = 0
    if audit_store_path:
        store = JsonlAuditStore(audit_store_path)
        audit_ok = store.verify()
        audit_events = len(store.load())

    report = DailyReplayReport(
        day=target_day.isoformat(),
        event_counts=dict(event_counter),
        level_counts=dict(level_counter),
        reject_reason_top=reason_counter.most_common(10),
        retries=retries,
        accepted=accepted,
        rejected=rejected,
        oms_event_counts=oms_event_counts,
        audit_ok=audit_ok,
        audit_events=audit_events,
    )
    return asdict(report)
