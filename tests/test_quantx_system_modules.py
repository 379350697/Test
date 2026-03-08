from quantx.alerts import AlertMessage, AlertRouter
from quantx.attribution import pnl_attribution
from quantx.audit import AuditTrail
from quantx.data_quality import check_ohlcv_integrity
from quantx.exchange_rules import SymbolRule, validate_order
from quantx.live_pipeline import RebalanceCycleConfig, run_rebalance_cycle
from quantx.meta_portfolio import blend_strategy_weights
from quantx.oms import OMSOrder, OrderManager
from quantx.rebalance import TradingConstraints, generate_rebalance_orders
from quantx.risk_engine import RiskLimits, exposure_by_symbol, portfolio_var_gaussian, pretrade_check


def test_p0_rebalance_bridge_generates_orders_with_constraints():
    payload = generate_rebalance_orders(
        current_positions={"BTC": 0.1, "ETH": 1.0},
        target_weights={"BTC": 0.3, "ETH": 0.1},
        prices={"BTC": 50000.0, "ETH": 2500.0},
        total_equity=20000.0,
        constraints=TradingConstraints(min_notional=50.0, lot_size=0.001, max_turnover_pct=0.5),
    )
    assert payload["summary"]["generated_orders"] >= 1
    assert payload["summary"]["turnover_pct"] > 0


def test_p0_oms_and_exchange_rules_flow():
    ok, reason = validate_order(100.0, 0.2, SymbolRule(tick_size=0.1, lot_size=0.1, min_qty=0.1, min_notional=10))
    assert ok and reason == "ok"

    om = OrderManager(initial_cash=10000)
    order = om.submit(OMSOrder(order_id="o1", symbol="BTCUSDT", side="BUY", qty=0.2))
    assert order.status == "NEW"
    om.fill("o1", fill_qty=0.1, fill_price=100.0)
    assert om.get("o1").status == "PARTIALLY_FILLED"
    om.fill("o1", fill_qty=0.1, fill_price=100.0)
    assert om.get("o1").status == "FILLED"


def test_p1_data_quality_risk_engine_and_alerts():
    dq = check_ohlcv_integrity(
        [
            {"ts": "2024-01-01", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
            {"ts": "2024-01-02", "open": 11, "high": 10, "low": 9, "close": 12, "volume": -1},
        ]
    )
    assert not dq["ok"]
    assert len(dq["issues"]) >= 1

    ok, reason = pretrade_check({"BTC": 0.8}, 1000, limits=RiskLimits(max_symbol_weight=0.5))
    assert not ok and reason == "symbol_weight_exceeded"

    var = portfolio_var_gaussian([0.5, 0.5], [[0.04, 0.01], [0.01, 0.09]])
    assert var > 0

    exp = exposure_by_symbol({"BTC": 0.2}, {"BTC": 50000.0}, equity=20000.0)
    assert exp["weights"]["BTC"] > 0

    router = AlertRouter()
    sent = router.send("slack", AlertMessage(level="WARN", title="DD alert", body="drawdown exceeded"))
    assert sent["channel"] == "slack"


def test_p2_meta_attribution_audit():
    blended = blend_strategy_weights(
        regime="trend",
        regime_mix={"trend": {"cta": 0.7, "mr": 0.3}},
        strategy_weights={"cta": {"BTC": 0.6, "ETH": 0.4}, "mr": {"ETH": 1.0}},
    )
    assert abs(sum(abs(v) for v in blended.values()) - 1.0) < 1e-9

    attr = pnl_attribution(
        [
            {"symbol": "BTC", "reason": "exit", "realized_pnl": 120.0, "fee": 2.0},
            {"symbol": "ETH", "reason": "stop", "realized_pnl": -20.0, "fee": 1.0},
        ]
    )
    assert attr["total_realized_pnl"] == 100.0

    audit = AuditTrail()
    audit.append(actor="alice", action="deploy", payload={"strategy": "cta"})
    audit.append(actor="bob", action="rebalance", payload={"count": 3})
    assert audit.verify()


def test_rebalance_cycle_end_to_end_p0_p2():
    cfg = RebalanceCycleConfig(
        symbol_rules={
            "BTC": SymbolRule(tick_size=0.1, lot_size=0.001, min_qty=0.001, min_notional=10),
            "ETH": SymbolRule(tick_size=0.1, lot_size=0.001, min_qty=0.001, min_notional=10),
        }
    )

    result = run_rebalance_cycle(
        current_positions={"BTC": 0.05, "ETH": 0.4},
        target_weights={"BTC": 0.3, "ETH": 0.2},
        prices={"BTC": 50000.0, "ETH": 2500.0},
        total_equity=20000.0,
        config=cfg,
        ohlcv_rows=[
            {"ts": "2024-01-01", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
            {"ts": "2024-01-02", "open": 10.5, "high": 11.2, "low": 10.1, "close": 11.0, "volume": 120},
        ],
    )

    assert result["ok"]
    assert result["audit_ok"]
    assert len(result["accepted_orders"]) >= 1


def test_rebalance_cycle_pretrade_blocked():
    cfg = RebalanceCycleConfig(risk_limits=RiskLimits(max_symbol_weight=0.3))

    result = run_rebalance_cycle(
        current_positions={"BTC": 0.0},
        target_weights={"BTC": 0.8},
        prices={"BTC": 50000.0},
        total_equity=20000.0,
        config=cfg,
    )

    assert not result["ok"]
    assert result["stage"] == "pretrade"
