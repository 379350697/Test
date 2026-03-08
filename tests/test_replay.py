from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
import json

from quantx.audit import AuditTrail, JsonlAuditStore
from quantx.oms import JsonlOMSStore, OMSOrder, OrderManager
from quantx.replay import build_daily_replay_report
from quantx.system_log import JsonlEventLogger, LogEvent



def test_build_daily_replay_report_with_oms_and_audit(tmp_path: Path):
    logs = tmp_path / "runtime" / "events.jsonl"
    logger = JsonlEventLogger(str(logs))
    logger.log(LogEvent(category="system", event="place_order_retry", level="WARN", stage="execute", payload={"attempt": 1}))
    logger.log(LogEvent(category="trade", event="order_rejected", level="ERROR", stage="execute", payload={"reason": "symbol_not_allowed"}))
    logger.log(LogEvent(category="trade", event="order_accepted", level="INFO", stage="execute", payload={"result": {"ok": True}}))

    oms = JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl"))
    om = OrderManager(initial_cash=1000.0, store=oms)
    om.submit(OMSOrder(order_id="o1", symbol="BTCUSDT", side="BUY", qty=0.2))
    om.fill("o1", fill_qty=0.1, fill_price=100.0)

    audit_store = JsonlAuditStore(str(tmp_path / "audit" / "events.jsonl"))
    t = AuditTrail()
    ev = t.append("system", "startup", {"ok": True})
    audit_store.append(ev)

    rep = build_daily_replay_report(
        event_log_path=str(logs),
        oms_store_path=str(oms.path),
        audit_store_path=str(audit_store.path),
    )

    assert rep["accepted"] == 1
    assert rep["rejected"] == 1
    assert rep["retries"] == 1
    assert rep["audit_ok"] is True
    assert rep["audit_events"] == 1
    assert rep["invalid_event_lines"] == 0
    assert rep["oms_event_counts"]["submit"] == 1


def test_build_daily_replay_report_skips_invalid_lines_and_filters_audit_day(tmp_path: Path):
    logs = tmp_path / "runtime" / "events.jsonl"
    logs.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)

    good_today = {
        "ts": datetime.utcnow().isoformat(),
        "event": "order_accepted",
        "level": "INFO",
        "payload": {"ok": True},
    }
    good_yesterday = {
        "ts": datetime.combine(yesterday, datetime.min.time()).isoformat(),
        "event": "order_rejected",
        "level": "ERROR",
        "payload": {"reason": "old"},
    }
    with logs.open("w", encoding="utf-8") as f:
        f.write(json.dumps(good_today, ensure_ascii=False) + "\n")
        f.write("not-json\n")
        f.write(json.dumps(["not", "object"], ensure_ascii=False) + "\n")
        f.write(json.dumps(good_yesterday, ensure_ascii=False) + "\n")

    audit_store = JsonlAuditStore(str(tmp_path / "audit" / "events.jsonl"))
    t = AuditTrail()
    ev_old = t.append("system", "old", {"ok": False})
    ev_old.ts = datetime.combine(yesterday, datetime.min.time()).isoformat()
    ev_today = t.append("system", "today", {"ok": True})
    audit_store.append(ev_old)
    audit_store.append(ev_today)

    rep = build_daily_replay_report(
        event_log_path=str(logs),
        audit_store_path=str(audit_store.path),
        day=today.isoformat(),
    )

    assert rep["accepted"] == 1
    assert rep["rejected"] == 0
    assert rep["invalid_event_lines"] == 2
    assert rep["audit_events"] == 1
    assert rep["audit_ok"] is False
