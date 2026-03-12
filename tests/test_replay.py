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


def test_build_daily_replay_report_from_runtime_event_store_fixture():
    fixture = Path(__file__).with_name('fixtures') / 'runtime_replay_events.jsonl'

    rep = build_daily_replay_report(
        event_log_path=str(fixture),
        day='2026-03-12',
    )

    assert rep['event_counts']['order_event'] == 2
    assert rep['event_counts']['fill_event'] == 1
    assert rep['event_counts']['account_event'] == 1
    assert rep['accepted'] == 1
    assert rep['rejected'] == 1
    assert rep['reject_reason_top'][0][0] == 'lot_too_small'
    assert rep['invalid_event_lines'] == 0


def test_build_daily_replay_report_includes_runtime_parity_drift_metrics():
    fixture = Path(__file__).with_name('fixtures') / 'runtime_replay_events.jsonl'

    rep = build_daily_replay_report(
        event_log_path=str(fixture),
        day='2026-03-12',
    )

    assert rep['runtime_summary']['order_state_sequences']['cid-1'] == ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working', 'filled']
    assert rep['drift_metrics']['paper_vs_live']['order_state_match_rate'] == 1.0
    assert rep['drift_metrics']['paper_vs_live']['equity_drift'] == 0.0


def test_replay_daily_surfaces_incident_summary_and_gate_recommendation(tmp_path: Path):
    logs = tmp_path / 'runtime' / 'events.jsonl'
    logger = JsonlEventLogger(str(logs))
    logger.log(LogEvent(category='system', event='place_order_retry', level='WARN', stage='execute', payload={'attempt': 1}))
    logger.log(LogEvent(category='trade', event='order_rejected', level='ERROR', stage='execute', payload={'reason': 'symbol_not_allowed'}))

    rep = build_daily_replay_report(
        event_log_path=str(logs),
    )

    assert 'incident_summary' in rep
    assert 'gate_recommendation' in rep


def test_build_daily_replay_report_surfaces_runtime_health_and_order_sequence_invariants():
    fixture = Path(__file__).with_name('fixtures') / 'runtime_market_tape.jsonl'

    rep = build_daily_replay_report(
        event_log_path=str(fixture),
        day='2026-03-12',
    )

    runtime_summary = rep['runtime_summary']
    assert runtime_summary['health']['degraded'] is False
    assert runtime_summary['health']['last_error'] is None
    assert 'position_invariants' in runtime_summary
    assert 'ledger_invariants' in runtime_summary


def test_build_daily_replay_report_reruns_paper_on_market_tape():
    fixture = Path(__file__).with_name('fixtures') / 'runtime_market_tape.jsonl'

    rep = build_daily_replay_report(
        event_log_path=str(fixture),
        day='2026-03-12',
    )

    assert rep['runtime_summary']['mode'] == 'live_replay'
    assert rep['paper_summary']['mode'] == 'paper_replay'
    assert 'paper_vs_live' in rep['drift_metrics']
    assert rep['paper_summary'] != rep['runtime_summary']


def test_drift_report_flags_non_zero_fill_price_difference_when_paper_slips():
    fixture = Path(__file__).with_name('fixtures') / 'runtime_market_tape.jsonl'

    rep = build_daily_replay_report(
        event_log_path=str(fixture),
        day='2026-03-12',
    )

    assert rep['drift_metrics']['paper_vs_live']['fill_price_drift'] > 0.0

def test_build_daily_replay_report_reconstructs_live_truth_with_funding_and_observed_exchange():
    fixture = Path(__file__).with_name('fixtures') / 'okx_live_truth_events.jsonl'

    rep = build_daily_replay_report(
        event_log_path=str(fixture),
        day='2026-03-12',
    )

    assert rep['runtime_summary']['order_state_sequences']['cid-1'] == [
        'intent_created',
        'risk_accepted',
        'submitted',
        'acked',
        'working',
        'filled',
    ]
    assert rep['runtime_summary']['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
    assert rep['runtime_summary']['observed_exchange']['positions']['BTC-USDT-SWAP']['long']['qty'] == 1.0
    assert rep['runtime_summary']['observed_exchange']['account']['equity'] == 999.7
    assert rep['drift_metrics']['paper_vs_live']['funding_booking_drift'] >= 0.0

