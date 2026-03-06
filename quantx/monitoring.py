from __future__ import annotations

from collections import Counter


def monitor_equity(equity_curve: list[tuple[str, float]] | list[tuple[object, float]], dd_alert_pct: float = 10.0) -> dict:
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


def analyze_logs(logs: list[str]) -> dict:
    c = Counter()
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
