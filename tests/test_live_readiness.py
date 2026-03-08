from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from quantx.alerts import AlertMessage, AlertRouter, WebhookAlertChannel
from quantx.audit import AuditTrail, JsonlAuditStore
from quantx.exchanges.base import ExchangeOrder, ExchangePosition, SymbolSpec
from quantx.live_service import LiveExecutionConfig, LiveExecutionService
from quantx.oms import JsonlOMSStore, OMSOrder, OrderManager
from quantx.readiness import ReadinessContext, ReadinessError, assert_ready, blockers, evaluate_readiness
from quantx.risk_engine import CircuitBreakerLimits, RiskCircuitBreaker, RiskLimits, check_account_notional
from quantx.system_log import JsonlEventLogger, LogEvent, MemoryEventLogger


class DummyExchange:
    def __init__(self):
        self.place_attempts = 0

    def place_order(self, order: ExchangeOrder) -> dict[str, object]:
        self.place_attempts += 1
        if self.place_attempts == 1:
            raise RuntimeError("temporary")
        return {"ok": True, "clientOrderId": order.client_order_id}

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, object]:
        return {"ok": True, "symbol": symbol, "clientOrderId": client_order_id}

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, object]]:
        return [{"symbol": symbol or "BTCUSDT"}]

    def get_account_positions(self) -> list[ExchangePosition]:
        return [ExchangePosition(symbol="USDT", qty=1000.0)]

    def get_symbol_specs(self, symbols: list[str] | None = None) -> dict[str, SymbolSpec]:
        pool = {
            "BTCUSDT": SymbolSpec(symbol="BTCUSDT", tick_size=0.1, lot_size=0.001, min_qty=0.001, min_notional=5.0),
            "ETHUSDT": SymbolSpec(symbol="ETHUSDT", tick_size=0.1, lot_size=0.001, min_qty=0.001, min_notional=5.0),
        }
        if not symbols:
            return pool
        return {k: v for k, v in pool.items() if k in {s.upper() for s in symbols}}


def test_live_execution_service_build_execute_and_reconcile():
    ex = DummyExchange()
    mem_logger = MemoryEventLogger()
    svc = LiveExecutionService(
        ex,
        config=LiveExecutionConfig(dry_run=False, max_retries=2, retry_backoff_ms=1, client_order_prefix="test"),
        event_logger=mem_logger,
    )
    rules = svc.sync_symbol_rules(["BTCUSDT"])
    assert "BTCUSDT" in rules

    payload = svc.build_rebalance_orders(
        current_positions={"BTCUSDT": 0.01},
        target_weights={"BTCUSDT": 0.2},
        prices={"BTCUSDT": 50000.0},
        total_equity=10000.0,
    )
    assert payload["ok"]
    assert payload["orders"]

    result = svc.execute_orders(payload["orders"])
    assert result["ok"]
    assert ex.place_attempts >= 2

    snap = svc.reconcile("BTCUSDT")
    assert len(snap["open_orders"]) == 1
    assert snap["positions"][0]["symbol"] == "USDT"
    events = {(e.category, e.event) for e in mem_logger.events}
    assert ("system", "sync_symbol_rules") in events
    assert ("trade", "order_accepted") in events
    assert ("system", "reconcile") in events


def test_live_execution_service_pretrade_and_rule_rejects():
    ex = DummyExchange()
    svc = LiveExecutionService(ex, config=LiveExecutionConfig(dry_run=True))
    svc.sync_symbol_rules(["BTCUSDT"])

    blocked = svc.build_rebalance_orders(
        current_positions={"BTCUSDT": 0.0},
        target_weights={"BTCUSDT": 1.5},
        prices={"BTCUSDT": 50000.0},
        total_equity=10000.0,
    )
    assert not blocked["ok"] and blocked["stage"] == "pretrade"

    bad_orders = [{"symbol": "BTCUSDT", "side": "BUY", "qty": 0.0001, "price": 1.0}]
    rej = svc.execute_orders(bad_orders)
    assert not rej["ok"]
    assert rej["rejected"][0]["reason"] in {"below_min_qty", "below_min_notional"}


def test_risk_circuit_breaker_and_account_notional_limits():
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    cb = RiskCircuitBreaker(CircuitBreakerLimits(max_daily_loss=100.0, max_orders_per_day=2), now=now)
    cb.register_order(now)
    cb.register_order(now)
    ok, reason = cb.check(now)
    assert ok and reason == "ok"

    cb.register_order(now)
    ok, reason = cb.check(now)
    assert not ok and reason == "max_orders_per_day_exceeded"

    cb = RiskCircuitBreaker(CircuitBreakerLimits(max_daily_loss=50.0, max_orders_per_day=10), now=now)
    cb.register_fill(-60.0, now)
    ok, reason = cb.check(now)
    assert not ok and reason == "daily_loss_exceeded"

    nxt = now + timedelta(days=1)
    ok, reason = cb.check(nxt)
    assert ok and reason == "ok"

    ok, reason = check_account_notional({"BTC": 900.0, "ETH": -200.0}, max_abs_notional=1000.0)
    assert not ok and reason == "account_notional_exceeded"






def test_oms_jsonl_persistence_and_recovery(tmp_path):
    store = JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl"))
    om = OrderManager(initial_cash=1000.0, store=store)

    om.submit(OMSOrder(order_id="o-1", symbol="BTCUSDT", side="BUY", qty=0.2))
    om.fill("o-1", fill_qty=0.1, fill_price=100.0)
    om.fill("o-1", fill_qty=0.1, fill_price=120.0)

    recovered = OrderManager.recover(store=store, initial_cash=1000.0)
    rec = recovered.get("o-1")
    assert rec.status == "FILLED"
    assert abs(rec.filled_qty - 0.2) < 1e-12
    assert abs(recovered.ledger.positions["BTCUSDT"] - 0.2) < 1e-12
    assert abs(recovered.ledger.cash - (1000.0 - 0.1 * 100.0 - 0.1 * 120.0)) < 1e-12

def test_unified_jsonl_event_logger_covers_trade_system_alert(tmp_path):
    logger = JsonlEventLogger(str(tmp_path / "logs" / "events.jsonl"))

    ex = DummyExchange()
    svc = LiveExecutionService(
        ex,
        config=LiveExecutionConfig(dry_run=True, client_order_prefix="test"),
        event_logger=logger,
    )
    svc.sync_symbol_rules(["BTCUSDT"])
    payload = svc.build_rebalance_orders(
        current_positions={"BTCUSDT": 0.0},
        target_weights={"BTCUSDT": 0.1},
        prices={"BTCUSDT": 50000.0},
        total_equity=10000.0,
    )
    assert payload["ok"]
    svc.execute_orders(payload["orders"])

    router = AlertRouter(event_logger=logger)
    router.send("slack", AlertMessage(level="WARN", title="risk", body="drawdown"))

    p = tmp_path / "logs" / "events.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    cats = {json.loads(line)["category"] for line in lines}
    assert {"trade", "system", "alert"}.issubset(cats)


def test_jsonl_event_logger_rotation_for_personal_runtime(tmp_path):
    logger = JsonlEventLogger(str(tmp_path / "logs" / "events.jsonl"), max_bytes=220, backup_count=2)

    for i in range(20):
        logger.log(
            LogEvent(
                category="system",
                event="heartbeat",
                stage="runtime",
                payload={"i": i, "msg": "x" * 30},
            )
        )

    base = tmp_path / "logs" / "events.jsonl"
    r1 = tmp_path / "logs" / "events.jsonl.1"
    assert base.exists()
    assert r1.exists()
    # backup_count=2 means at most .1 and .2 may exist
    r2 = tmp_path / "logs" / "events.jsonl.2"
    assert not ((tmp_path / "logs" / "events.jsonl.3").exists())
    if r2.exists():
        assert r2.stat().st_size > 0

def test_audit_jsonl_store_roundtrip_and_verify(tmp_path):
    trail = AuditTrail()
    ev1 = trail.append("system", "start", {"mode": "live"})
    ev2 = trail.append("system", "order", {"symbol": "BTCUSDT"})
    assert trail.verify()

    store = JsonlAuditStore(str(tmp_path / "audit" / "events.jsonl"))
    store.append(ev1)
    store.append(ev2)

    loaded = store.load()
    assert len(loaded) == 2
    assert store.verify()

    # tamper
    p = tmp_path / "audit" / "events.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("BTCUSDT", "ETHUSDT")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not store.verify()


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_webhook_alert_channel_retry_and_router(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(_req, timeout=5.0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("temporary")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    channel = WebhookAlertChannel("https://example.com/hook", timeout_s=1.0, max_retries=2, retry_backoff_ms=1)
    status = channel.send(AlertMessage(level="WARN", title="t", body="b"))
    assert status["status"] == "sent"
    assert calls["n"] == 2

    router = AlertRouter()
    router.register_webhook("ops", "https://example.com/hook", timeout_s=1.0, max_retries=0, retry_backoff_ms=1)
    rec = router.send("ops", AlertMessage(level="ERROR", title="risk", body="stop"))
    assert rec["delivery"] == "sent"


def test_rollout_guards_block_non_whitelist_and_excess_cycle():
    ex = DummyExchange()
    svc = LiveExecutionService(
        ex,
        config=LiveExecutionConfig(
            dry_run=True,
            allowed_symbols=("BTCUSDT",),
            max_orders_per_cycle=1,
            max_notional_per_cycle=2000.0,
        ),
    )
    svc.sync_symbol_rules(["BTCUSDT", "ETHUSDT"])

    too_many = [
        {"symbol": "BTCUSDT", "side": "BUY", "qty": 0.01, "price": 50000.0},
        {"symbol": "ETHUSDT", "side": "BUY", "qty": 0.5, "price": 3000.0},
    ]
    res = svc.execute_orders(too_many)
    assert not res["ok"]
    assert "max_orders_per_cycle_exceeded" in res["rejected"][0]["reason"]
    assert res["rejected"][0]["reason"].startswith("QX-EXEC-001:")

    svc2 = LiveExecutionService(
        ex,
        config=LiveExecutionConfig(dry_run=True, allowed_symbols=("BTCUSDT",), max_notional_per_cycle=100000.0),
    )
    svc2.sync_symbol_rules(["BTCUSDT", "ETHUSDT"])
    mix = [
        {"symbol": "BTCUSDT", "side": "BUY", "qty": 0.01, "price": 50000.0},
        {"symbol": "ETHUSDT", "side": "BUY", "qty": 0.5, "price": 3000.0},
    ]
    res2 = svc2.execute_orders(mix)
    assert not res2["ok"]
    assert any(r.get("reason") == "symbol_not_allowed_in_rollout" for r in res2["rejected"])


def test_readiness_evaluator_flags_missing_gates(tmp_path):
    router = AlertRouter()
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(dry_run=False),
        risk_limits=RiskLimits(),
        alert_router=router,
        oms_store=None,
    )

    rep = evaluate_readiness(ctx)
    assert not rep.ok
    assert rep.score < 100
    names = {c["name"]: c for c in rep.checks}
    assert not names["rollout_allowed_symbols"]["ok"]
    assert not names["alert_channel_registered"]["ok"]


def test_readiness_evaluator_passes_when_all_gates_set(tmp_path):
    router = AlertRouter()
    router.register_webhook("ops", "https://example.com/hook")

    store = JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl"))

    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=("BTCUSDT",),
            max_orders_per_cycle=5,
            max_notional_per_cycle=50000.0,
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=10000.0),
        alert_router=router,
        oms_store=store,
    )

    rep = evaluate_readiness(ctx)
    assert rep.ok
    assert rep.score == 100


def test_readiness_assert_ready_and_blockers(tmp_path):
    router = AlertRouter()
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(dry_run=False),
        risk_limits=RiskLimits(),
        alert_router=router,
        oms_store=None,
    )
    rep = evaluate_readiness(ctx)
    failed = blockers(rep)
    assert len(failed) >= 1

    try:
        assert_ready(ctx)
        raise AssertionError("expected ReadinessError")
    except ReadinessError as exc:
        assert "go_live_blocked" in str(exc)
        assert str(exc).startswith("QX-READY-001:")

    router.register_webhook("ops", "https://example.com/hook")
    ok_ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=("BTCUSDT",),
            max_orders_per_cycle=2,
            max_notional_per_cycle=1000.0,
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=1000.0),
        alert_router=router,
        oms_store=JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl")),
    )
    final = assert_ready(ok_ctx)
    assert final.ok
