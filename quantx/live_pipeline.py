"""End-to-end rebalance cycle orchestrator (P0 -> P1 -> P2).

This module wires together previously separated system primitives:
- P0: rebalance order generation + exchange rule validation + OMS execution.
- P1: data-quality gate + pre-trade risk checks.
- P2: regime-aware sleeve blending + attribution + audit trail.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

from .attribution import pnl_attribution
from .audit import AuditTrail
from .data_quality import check_ohlcv_integrity
from .exchange_rules import SymbolRule, validate_order
from .meta_portfolio import blend_strategy_weights
from .oms import OMSOrder, OrderManager
from .rebalance import TradingConstraints, generate_rebalance_orders
from .risk_engine import RiskLimits, pretrade_check
from .live_service import LiveExecutionService


@dataclass(slots=True)
class RebalanceCycleConfig:
    """Configuration for a single rebalance cycle."""

    trading_constraints: TradingConstraints = field(default_factory=TradingConstraints)
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    symbol_rules: dict[str, SymbolRule] = field(default_factory=dict)


def run_rebalance_cycle(
    current_positions: dict[str, float],
    target_weights: dict[str, float],
    prices: dict[str, float],
    total_equity: float,
    config: RebalanceCycleConfig | None = None,
    *,
    ohlcv_rows: list[dict[str, Any]] | None = None,
    regime: str | None = None,
    regime_mix: dict[str, dict[str, float]] | None = None,
    sleeve_weights: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Run a robust rebalance cycle using P0/P1/P2 modules.

    Returns a structured result containing risk checks, accepted/rejected orders,
    simulated fills, attribution, and audit verification status.
    """

    cfg = config or RebalanceCycleConfig()
    audit = AuditTrail()

    # ---------- P2 (optional): regime-aware target blend ----------
    effective_target = dict(target_weights)
    if regime and regime_mix and sleeve_weights:
        effective_target = blend_strategy_weights(regime, regime_mix, sleeve_weights)
        audit.append("system", "meta_blend", {"regime": regime, "symbols": list(effective_target)})

    # ---------- P1: data-quality and portfolio-level pretrade checks ----------
    if ohlcv_rows is not None:
        dq = check_ohlcv_integrity(ohlcv_rows)
        audit.append("system", "dq_check", {"ok": dq["ok"], "issues": len(dq["issues"])})
        if not dq["ok"]:
            return {
                "ok": False,
                "stage": "dq",
                "dq": dq,
                "audit_ok": audit.verify(),
                "audit_events": [asdict(e) for e in audit.events],
            }

    gross_order_hint = total_equity * sum(abs(v) for v in effective_target.values())
    ok, reason = pretrade_check(effective_target, gross_order_hint, cfg.risk_limits)
    audit.append("system", "pretrade_check", {"ok": ok, "reason": reason})
    if not ok:
        return {
            "ok": False,
            "stage": "pretrade",
            "reason": reason,
            "audit_ok": audit.verify(),
            "audit_events": [asdict(e) for e in audit.events],
        }

    # ---------- P0: target -> orders -> exchange rule checks -> OMS fills ----------
    bridge = generate_rebalance_orders(
        current_positions=current_positions,
        target_weights=effective_target,
        prices=prices,
        total_equity=total_equity,
        constraints=cfg.trading_constraints,
    )
    audit.append("system", "rebalance_bridge", {"generated": bridge["summary"]["generated_orders"]})

    accepted_orders: list[dict[str, Any]] = []
    rejected_orders: list[dict[str, Any]] = []

    om = OrderManager(initial_cash=total_equity)
    trade_records: list[dict[str, Any]] = []

    for i, od in enumerate(bridge["orders"]):
        symbol = str(od["symbol"])
        side_raw = str(od["side"])
        if side_raw not in {"BUY", "SELL"}:
            rejected_orders.append({"order": od, "reason": "invalid_side"})
            audit.append("system", "order_rejected", {"symbol": symbol, "reason": "invalid_side"})
            continue
        side = cast(Literal["BUY", "SELL"], side_raw)
        qty = float(od["qty"])
        price = float(od["price"])

        rule = cfg.symbol_rules.get(symbol)
        if rule is not None:
            valid, why = validate_order(price, qty, rule)
            if not valid:
                rejected_orders.append({"order": od, "reason": why})
                audit.append("system", "order_rejected", {"symbol": symbol, "reason": why})
                continue

        order_id = f"rb-{i}-{symbol}"
        oms_order = om.submit(OMSOrder(order_id=order_id, symbol=symbol, side=side, qty=qty))
        om.fill(order_id=oms_order.order_id, fill_qty=qty, fill_price=price)

        accepted_orders.append(od)
        trade_records.append(
            {
                "symbol": symbol,
                "reason": "rebalance",
                "fee": 0.0,
                "realized_pnl": 0.0,
            }
        )
        audit.append("system", "order_filled", {"symbol": symbol, "qty": qty, "price": price})

    # ---------- P2: attribution + audit verification ----------
    attr = pnl_attribution(trade_records)
    audit.append("system", "attribution", {"trades": len(trade_records)})

    return {
        "ok": True,
        "target_weights": effective_target,
        "bridge": bridge,
        "accepted_orders": accepted_orders,
        "rejected_orders": rejected_orders,
        "ledger": {
            "cash": om.ledger.cash,
            "positions": dict(om.ledger.positions),
        },
        "attribution": attr,
        "audit_ok": audit.verify(),
        "audit_events": [asdict(e) for e in audit.events],
    }


def run_live_rebalance_cycle(
    service: LiveExecutionService,
    current_positions: dict[str, float],
    target_weights: dict[str, float],
    prices: dict[str, float],
    total_equity: float,
) -> dict[str, Any]:
    """Live mode pipeline: pretrade/order generation + exchange execution + reconcile."""

    plan = service.build_rebalance_orders(
        current_positions=current_positions,
        target_weights=target_weights,
        prices=prices,
        total_equity=total_equity,
    )
    if not plan.get("ok", False):
        return {"ok": False, "stage": plan.get("stage", "unknown"), "reason": plan.get("reason", "unknown")}

    execution = service.execute_orders(plan.get("orders", []))
    snapshot = service.reconcile()
    runtime_events = list(execution.get("runtime_events", [])) + list(snapshot.get("runtime_events", []))
    return {"ok": execution.get("ok", False), "plan": plan, "execution": execution, "snapshot": snapshot, "runtime_events": runtime_events}
