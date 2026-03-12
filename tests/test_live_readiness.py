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
from quantx.runtime import AccountEvent


def _router_with_webhook() -> AlertRouter:
    router = AlertRouter()
    router.register_webhook('ops', 'https://example.com/hook')
    return router


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


def test_live_service_marks_runtime_degraded_when_runtime_event_application_fails(tmp_path):
    svc = LiveExecutionService(
        DummyExchange(),
        config=LiveExecutionConfig(dry_run=True, exchange='okx', runtime_mode='derivatives'),
        runtime_event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
    )

    svc.ingest_runtime_event(
        AccountEvent(
            exchange='okx',
            ts='2026-03-12T00:00:00+00:00',
            event_type='funding',
            payload={},
        )
    )

    status = svc.runtime_status()

    assert status['degraded'] is True
    assert status['last_error']['stage'] == 'apply_event'
    assert status['execution_mode'] == 'blocked'


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


def test_live_service_blocks_new_orders_when_reconcile_health_is_blocked():
    svc = LiveExecutionService(DummyExchange(), config=LiveExecutionConfig(dry_run=True, exchange='okx'))
    svc.runtime_coordinator.health.mark_reconcile({'ok': False, 'severity': 'block'})

    result = svc.execute_orders([
        {'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'price': 50000.0, 'position_side': 'long'}
    ])

    assert result['ok'] is False
    assert result['rejected'][0]['reason'] == 'runtime_truth_blocked'


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


def test_readiness_blocks_live_when_stream_is_stale_even_if_replay_persists(tmp_path):
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(dry_run=False, exchange='okx', runtime_mode='derivatives', allowed_symbols=('BTC-USDT-SWAP',), max_orders_per_cycle=1, max_notional_per_cycle=1000.0),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=1000.0),
        alert_router=_router_with_webhook(),
        oms_store=JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl')),
        runtime_status={
            'replay_persistence': True,
            'degraded': False,
            'reconcile_ok': True,
            'stream': {'stale': True},
            'execution_mode': 'blocked',
        },
    )

    report = evaluate_readiness(ctx)
    checks = {check['name']: check for check in report.checks}

    assert checks['live_truth_stream_fresh']['ok'] is False
    assert checks['live_truth_execution_mode_allowed']['ok'] is False


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



def test_readiness_blocks_normal_live_when_runtime_truth_is_degraded_or_unrecoverable(tmp_path):
    router = AlertRouter()
    router.register_webhook("ops", "https://example.com/hook")
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=("BTC-USDT-SWAP",),
            max_orders_per_cycle=5,
            max_notional_per_cycle=50000.0,
            runtime_mode='derivatives',
            exchange='okx',
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=10000.0),
        alert_router=router,
        oms_store=JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl")),
        runtime_status={'replay_persistence': False, 'degraded': True, 'reconcile_ok': False},
    )

    report = evaluate_readiness(ctx)
    checks = {check['name']: check for check in report.checks}

    assert checks['live_truth_replay_persistence']['ok'] is False
    assert checks['live_truth_not_degraded']['ok'] is False
    assert checks['live_truth_reconcile_ok']['ok'] is False


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
        runtime_status={'replay_persistence': True, 'degraded': False, 'reconcile_ok': True},
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
        runtime_status={'replay_persistence': True, 'degraded': False, 'reconcile_ok': True},
    )
    final = assert_ready(ok_ctx)
    assert final.ok


class _AlwaysFailExchange(DummyExchange):
    def place_order(self, order: ExchangeOrder) -> dict[str, object]:
        self.place_attempts += 1
        raise RuntimeError("downstream_unavailable")


def test_live_service_auto_switches_to_dry_run_after_consecutive_failures():
    ex = _AlwaysFailExchange()
    logger = MemoryEventLogger()
    svc = LiveExecutionService(
        ex,
        config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=("BTCUSDT",),
            max_retries=0,
            max_consecutive_failures=2,
            auto_switch_to_dry_run_on_failures=True,
        ),
        event_logger=logger,
    )
    svc.sync_symbol_rules(["BTCUSDT"])

    orders = [{"symbol": "BTCUSDT", "side": "BUY", "qty": 0.01, "price": 50000.0}]
    first = svc.execute_orders(orders)
    second = svc.execute_orders(orders)
    third = svc.execute_orders(orders)

    assert not first["ok"]
    assert not second["ok"]
    assert svc.config.dry_run is True
    assert third["ok"]  # degraded to dry-run mode, no exchange call

    events = [e for e in logger.events if e.event == "execution_degraded_to_dry_run"]
    assert len(events) >= 1


def test_runtime_risk_health_checks_cross_margin_account_state():
    from quantx.runtime.models import AccountLedger
    from quantx.runtime.runtime_risk import RuntimeRiskLimits, RuntimeRiskValidator
    from quantx.risk_engine import check_cross_margin_health

    ledger = AccountLedger(
        wallet_balance=1000.0,
        equity=950.0,
        available_margin=-5.0,
        used_margin=955.0,
        maintenance_margin=120.0,
        risk_ratio=0.126,
    )
    validator = RuntimeRiskValidator(
        RuntimeRiskLimits(min_available_margin=0.0, max_risk_ratio=0.1)
    )

    ok, reason = validator.check_account_health(ledger)
    assert not ok and reason == 'available_margin_below_floor'

    ok, reason = check_cross_margin_health(
        available_margin=100.0,
        risk_ratio=0.2,
        min_available_margin=0.0,
        max_risk_ratio=0.1,
    )
    assert not ok and reason == 'risk_ratio_exceeded'




class _FakeOKXPrivateStream:
    def __init__(self, messages: list[dict[str, object]]):
        self.messages = list(messages)
        self.connect_calls = 0
        self.close_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1

    def iter_messages(self):
        for message in self.messages:
            yield message

    def close(self) -> None:
        self.close_calls += 1
class _DummyOKXPerpExchange(DummyExchange):
    def get_raw_open_orders(self, symbol: str | None = None) -> list[dict[str, object]]:
        return [
            {
                'instId': 'BTC-USDT-SWAP',
                'clOrdId': 'cid-1',
                'ordId': 'oid-1',
                'state': 'live',
                'side': 'buy',
                'posSide': 'long',
                'tdMode': 'cross',
                'uTime': '1710201600000',
            }
        ]

    def get_raw_account_positions(self, symbol: str | None = None) -> list[dict[str, object]]:
        return [
            {
                'instId': 'BTC-USDT-SWAP',
                'posSide': 'long',
                'pos': '0.25',
                'avgPx': '100000',
                'mgnMode': 'cross',
                'uTime': '1710201602000',
            }
        ]


def test_live_execution_service_reconcile_uses_okx_runtime_adapter():
    from quantx.exchanges.okx_perp import OKXPerpAdapter

    ex = _DummyOKXPerpExchange()
    svc = LiveExecutionService(ex, config=LiveExecutionConfig(dry_run=True), runtime_adapter=OKXPerpAdapter())

    snap = svc.reconcile('BTC-USDT-SWAP')

    assert snap['positions'][0]['symbol'] == 'BTC-USDT-SWAP'
    assert snap['positions'][0]['position_side'] == 'long'
    assert snap['open_orders'][0]['clientOrderId'] == 'cid-1'
    assert snap['runtime_events'][0]['kind'] == 'order_event'


def test_live_execution_service_updates_runtime_snapshot_from_adapter_events():
    from quantx.exchanges.okx_perp import OKXPerpAdapter

    ex = _DummyOKXPerpExchange()
    svc = LiveExecutionService(
        ex,
        config=LiveExecutionConfig(dry_run=False, max_retries=1, retry_backoff_ms=1),
        runtime_adapter=OKXPerpAdapter(),
    )
    svc.sync_symbol_rules(['BTCUSDT'])

    result = svc.execute_orders([
        {'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'price': 100000.0, 'position_side': 'long'}
    ])

    assert result['runtime_snapshot']['orders']
    assert result['runtime_snapshot']['orders'][0]['status'] in {'acked', 'working', 'filled'}


def test_bootstrap_recovery_prefers_runtime_ledger_snapshot(tmp_path):
    from quantx.bootstrap import bootstrap_recover_and_reconcile
    from quantx.exchanges.okx_perp import OKXPerpAdapter

    class _BootstrapExchange(_DummyOKXPerpExchange):
        def get_raw_open_orders(self, symbol: str | None = None) -> list[dict[str, object]]:
            return []

    store = JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl'))
    om = OrderManager(initial_cash=1000.0, store=store)
    om.submit(OMSOrder(order_id='cid-1', symbol='BTC-USDT-SWAP', side='BUY', qty=0.25))
    om.fill('cid-1', fill_qty=0.25, fill_price=100000.0)

    svc = LiveExecutionService(
        _BootstrapExchange(),
        config=LiveExecutionConfig(dry_run=True),
        runtime_adapter=OKXPerpAdapter(),
    )

    report = bootstrap_recover_and_reconcile(service=svc, oms_store=store, initial_cash=1000.0, symbol='BTC-USDT-SWAP')

    assert 'runtime_positions' in report
    assert report['ok'] is True


def test_deploy_readiness_prefers_okx_before_binance():
    router = AlertRouter()
    router.register_webhook('ops', 'https://example.com/hook')
    store = JsonlOMSStore('tests/fixtures/runtime_replay_events.jsonl')

    okx_ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=('BTC-USDT-SWAP',),
            max_orders_per_cycle=5,
            max_notional_per_cycle=50000.0,
            runtime_mode='derivatives',
            exchange='okx',
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=10000.0),
        alert_router=router,
        oms_store=store,
        runtime_status={'replay_persistence': True, 'degraded': False, 'reconcile_ok': True},
    )
    okx_report = evaluate_readiness(okx_ctx)
    okx_checks = {check['name']: check for check in okx_report.checks}

    assert okx_checks['runtime_execution_path']['ok'] is True
    assert okx_checks['rollout_exchange_order']['ok'] is True

    binance_ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=('BTCUSDT',),
            max_orders_per_cycle=5,
            max_notional_per_cycle=50000.0,
            runtime_mode='derivatives',
            exchange='binance',
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=10000.0),
        alert_router=router,
        oms_store=store,
        runtime_status={'replay_persistence': True, 'degraded': False, 'reconcile_ok': True},
    )
    binance_report = evaluate_readiness(binance_ctx)
    binance_checks = {check['name']: check for check in binance_report.checks}

    assert binance_checks['runtime_execution_path']['ok'] is True
    assert binance_checks['rollout_exchange_order']['ok'] is False


def test_live_execution_service_ingests_private_stream_events_into_runtime_truth(tmp_path):
    from quantx.exchanges.okx_perp import OKXPerpAdapter

    adapter = OKXPerpAdapter()
    svc = LiveExecutionService(
        _DummyOKXPerpExchange(),
        config=LiveExecutionConfig(dry_run=False, max_retries=1, retry_backoff_ms=1, runtime_mode='derivatives', exchange='okx'),
        runtime_adapter=adapter,
        runtime_event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
    )
    svc.sync_symbol_rules(['BTCUSDT'])
    result = svc.execute_orders(
        [
            {'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'price': 100000.0, 'position_side': 'long'}
        ]
    )
    client_order_id = result['accepted'][0]['result']['clientOrderId']

    svc.ingest_runtime_event(
        adapter.normalize_fill_event(
            {
                'instId': 'BTC-USDT-SWAP',
                'clOrdId': client_order_id,
                'ordId': 'oid-1',
                'tradeId': 'tid-1',
                'fillSz': '0.01',
                'fillPx': '100000',
                'fillFee': '-0.1',
                'side': 'buy',
                'posSide': 'long',
                'tdMode': 'cross',
                'fillTime': '1710201601000',
            }
        )
    )
    svc.ingest_runtime_event(
        adapter.normalize_funding_event(
            {'instId': 'BTC-USDT-SWAP', 'posSide': 'long', 'funding': '-0.2', 'ts': '1710230400000'}
        )
    )

    snapshot = svc.runtime_snapshot()

    assert snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 0.01
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
    assert snapshot['observed_exchange']






def test_live_service_private_stream_updates_runtime_health_and_ingests_messages(tmp_path):
    from quantx.exchanges.okx_perp import OKXPerpAdapter

    transport = _FakeOKXPrivateStream(
        messages=[
            {
                'type': 'fill',
                'payload': {
                    'instId': 'BTC-USDT-SWAP',
                    'clOrdId': 'cid-private-1',
                    'ordId': 'oid-private-1',
                    'tradeId': 'tid-private-1',
                    'fillSz': '0.01',
                    'fillPx': '100000',
                    'fillFee': '-0.1',
                    'side': 'buy',
                    'posSide': 'long',
                    'tdMode': 'cross',
                    'fillTime': '1710201601000',
                },
            },
            {
                'type': 'funding',
                'payload': {
                    'instId': 'BTC-USDT-SWAP',
                    'posSide': 'long',
                    'funding': '-0.2',
                    'ts': '1710230400000',
                },
            },
        ]
    )
    svc = LiveExecutionService(
        _DummyOKXPerpExchange(),
        config=LiveExecutionConfig(dry_run=False, exchange='okx', runtime_mode='derivatives'),
        runtime_adapter=OKXPerpAdapter(),
        runtime_event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
        private_stream_transport=transport,
    )

    svc.run_private_stream_once()

    status = svc.runtime_status()
    snapshot = svc.runtime_snapshot()

    assert status['stream']['state'] == 'connected'
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2

