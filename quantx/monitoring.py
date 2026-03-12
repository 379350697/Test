from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


def monitor_equity(equity_curve: list[tuple[str, float]] | list[tuple[object, float]], dd_alert_pct: float = 10.0) -> dict[str, Any]:
    if not equity_curve:
        return {"alerts": [], "max_drawdown_pct": 0.0}
    peak = equity_curve[0][1]
    alerts = []
    max_dd = 0.0
    for ts, v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        if dd >= dd_alert_pct:
            alerts.append({"ts": str(ts), "type": "drawdown", "value_pct": round(dd, 4)})
    return {"alerts": alerts, "max_drawdown_pct": round(max_dd, 4)}


def analyze_logs(logs: list[str]) -> dict[str, Any]:
    c: Counter[str] = Counter()
    for line in logs:
        lower = line.lower()
        if "error" in lower or "exception" in lower:
            c["error"] += 1
        if "kill_switch" in lower:
            c["kill_switch"] += 1
        if "order=" in lower:
            c["order"] += 1
        if "disabled_or_killed" in lower:
            c["reject"] += 1
    return {"summary": dict(c), "lines": len(logs)}


def summarize_replay_incidents(
    events: list[dict[str, Any]],
    *,
    retries: int,
    rejected: int,
    invalid_event_lines: int,
    runtime_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incidents = _collect_replay_incidents(events)
    runtime_health = runtime_summary.get("health", {}) if isinstance(runtime_summary, dict) else {}
    runtime_degraded = bool(runtime_health.get("degraded", False))

    warning_events = sum(1 for incident in incidents if incident["severity"] == "warn")
    error_events = sum(1 for incident in incidents if incident["severity"] == "error") + int(invalid_event_lines)
    highest_severity = "error" if error_events or runtime_degraded else "warn" if warning_events else "info"
    top_incident_types = Counter(incident["event"] for incident in incidents).most_common(5)

    incident_summary = {
        "total_incidents": warning_events + error_events,
        "warning_events": warning_events,
        "error_events": error_events,
        "retries": int(retries),
        "rejected_orders": int(rejected),
        "invalid_event_lines": int(invalid_event_lines),
        "runtime_degraded": runtime_degraded,
        "degraded": runtime_degraded or error_events > 0,
        "highest_severity": highest_severity,
        "top_incident_types": top_incident_types,
    }
    gate_recommendation = _gate_recommendation(incident_summary)

    degrade_windows = _collapse_incident_windows(incidents)
    if invalid_event_lines:
        degrade_windows.append(
            {
                "start_ts": None,
                "end_ts": None,
                "severity": "error",
                "count": int(invalid_event_lines),
                "events": ["invalid_event_line"],
            }
        )
    if runtime_degraded:
        degrade_windows.append(
            {
                "start_ts": None,
                "end_ts": None,
                "severity": "error",
                "count": 1,
                "events": ["runtime_degraded"],
            }
        )

    operator_actions = _operator_actions(
        incident_summary=incident_summary,
        gate_recommendation=gate_recommendation,
    )
    return {
        "incident_summary": incident_summary,
        "degrade_windows": degrade_windows,
        "gate_recommendation": gate_recommendation,
        "operator_actions": operator_actions,
    }


def _collect_replay_incidents(events: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    incidents: list[dict[str, str | None]] = []
    for event in events:
        severity = _replay_incident_severity(event)
        if severity is None:
            continue
        incidents.append(
            {
                "ts": _event_ts(event),
                "severity": severity,
                "event": _event_name(event),
            }
        )
    return incidents


def _replay_incident_severity(event: dict[str, Any]) -> str | None:
    level = _event_level(event)
    kind = str(event.get("kind", ""))
    status = str(event.get("status", ""))
    name = _event_name(event)

    if (kind == "order_event" and status == "rejected") or name == "order_rejected" or level == "ERROR":
        return "error"
    if name == "place_order_retry" or level == "WARN":
        return "warn"
    return None


def _event_level(event: dict[str, Any]) -> str:
    payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
    if "kind" in event:
        return str(payload.get("level", "INFO")).upper()
    return str(event.get("level", payload.get("level", "INFO"))).upper()


def _event_name(event: dict[str, Any]) -> str:
    kind = str(event.get("kind", ""))
    if kind == "order_event":
        status = str(event.get("status", "unknown"))
        return "order_rejected" if status == "rejected" else f"order_{status}"
    if kind:
        return kind
    return str(event.get("event", "unknown"))


def _event_ts(event: dict[str, Any]) -> str | None:
    raw = event.get("ts")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _collapse_incident_windows(incidents: list[dict[str, str | None]]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for incident in incidents:
        if current is None or not _can_extend_window(current, incident):
            current = {
                "start_ts": incident["ts"],
                "end_ts": incident["ts"],
                "severity": incident["severity"],
                "count": 1,
                "events": [incident["event"]],
            }
            windows.append(current)
            continue

        current["end_ts"] = incident["ts"] or current["end_ts"]
        current["count"] += 1
        if incident["event"] not in current["events"]:
            current["events"].append(incident["event"])

    return windows


def _can_extend_window(window: dict[str, Any], incident: dict[str, str | None]) -> bool:
    if window.get("severity") != incident["severity"]:
        return False
    end_dt = _parse_ts(window.get("end_ts"))
    next_dt = _parse_ts(incident["ts"])
    if end_dt is None or next_dt is None:
        return False
    return abs((next_dt - end_dt).total_seconds()) <= 300


def _parse_ts(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _gate_recommendation(incident_summary: dict[str, Any]) -> str:
    if incident_summary["degraded"] or incident_summary["rejected_orders"] or incident_summary["invalid_event_lines"]:
        return "hold_in_paper"
    if incident_summary["warning_events"] or incident_summary["retries"]:
        return "review_before_live"
    return "eligible_for_promotion_review"


def _operator_actions(*, incident_summary: dict[str, Any], gate_recommendation: str) -> list[str]:
    actions: list[str] = []
    if incident_summary["rejected_orders"]:
        actions.append("Review reject reasons and exchange constraints before promoting.")
    if incident_summary["invalid_event_lines"]:
        actions.append("Repair invalid replay log lines and rerun the daily replay review.")
    if incident_summary["retries"]:
        actions.append("Inspect retry bursts for exchange or network instability before promotion.")
    if incident_summary["runtime_degraded"]:
        actions.append("Resolve degraded runtime health and rerun the paper soak before promotion.")
    if gate_recommendation == "review_before_live" and not actions:
        actions.append("Review warning-level incidents before approving live promotion.")
    if gate_recommendation == "eligible_for_promotion_review" and not actions:
        actions.append("No blocking incidents detected; ready for promotion review.")
    return actions
