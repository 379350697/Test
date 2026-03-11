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
from .runtime.replay_store import RuntimeReplayStore


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
    invalid_event_lines: int



def _iter_jsonl(path: str) -> tuple[list[dict[str, Any]], int]:
    p = Path(path)
    if not p.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    invalid = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
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

    all_events, invalid_event_lines = RuntimeReplayStore(event_log_path).load()
    events = [r for r in all_events if _same_day(str(r.get("ts", "")), target_day)]

    event_counter: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    retries = 0
    accepted_ids: set[str] = set()
    rejected_ids: set[str] = set()
    accepted_legacy = 0
    rejected_legacy = 0

    for ev in events:
        payload_raw = ev.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        kind = str(ev.get("kind", ""))
        if kind in {"market_event", "order_event", "fill_event", "account_event"}:
            event_counter[kind] += 1
            level_counter[str(payload.get("level", "INFO"))] += 1
            if kind == "order_event":
                status = str(ev.get("status", "unknown"))
                client_order_id = str(ev.get("client_order_id", "unknown"))
                if status in {"acked", "working", "partially_filled", "filled"}:
                    accepted_ids.add(client_order_id)
                if status == "rejected":
                    rejected_ids.add(client_order_id)
                    reason = str(payload.get("reason", "unknown"))
                    reason_counter[reason] += 1
            continue

        event_name = str(ev.get("event", "unknown"))
        event_counter[event_name] += 1
        level_counter[str(ev.get("level", "INFO"))] += 1
        if event_name == "place_order_retry":
            retries += 1
        if event_name == "order_accepted":
            accepted_legacy += 1
        if event_name == "order_rejected":
            rejected_legacy += 1
            reason = str(payload.get("reason", "unknown"))
            reason_counter[reason] += 1

    accepted = accepted_legacy + len(accepted_ids)
    rejected = rejected_legacy + len(rejected_ids)

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
        audit_events = sum(1 for ev in store.load() if _same_day(ev.ts, target_day))

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
        invalid_event_lines=invalid_event_lines,
    )
    return asdict(report)
