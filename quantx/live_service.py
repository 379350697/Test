"""Live execution service bridging rebalance intents to exchange clients (P0/P1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

from .error_codes import QX_EXEC_CYCLE_LIMIT, QX_EXEC_PLACE_ORDER_EMPTY, with_code
from .exchange_rules import SymbolRule, validate_order
from .exchanges.base import ExchangeClient, ExchangeOrder, SymbolSpec
from .rebalance import TradingConstraints, generate_rebalance_orders
from .risk_engine import RiskLimits, pretrade_check
from .system_log import EventLogger, LogCategory, LogEvent, LogLevel


@dataclass(slots=True)
class LiveExecutionConfig:
    dry_run: bool = True
    max_retries: int = 2
    retry_backoff_ms: int = 25
    client_order_prefix: str = "qxlive"
    allowed_symbols: tuple[str, ...] | None = None
    max_orders_per_cycle: int | None = None
    max_notional_per_cycle: float | None = None


class LiveExecutionService:
    """Production-style execution adapter with retries and reconciliation."""

    def __init__(
        self,
        client: ExchangeClient,
        *,
        risk_limits: RiskLimits | None = None,
        trading_constraints: TradingConstraints | None = None,
        config: LiveExecutionConfig | None = None,
        event_logger: EventLogger | None = None,
    ):
        self.client = client
        self.risk_limits = risk_limits or RiskLimits()
        self.trading_constraints = trading_constraints or TradingConstraints()
        self.config = config or LiveExecutionConfig()
        self.symbol_rules: dict[str, SymbolRule] = {}
        self.event_logger = event_logger

    def _log(
        self,
        category: LogCategory,
        event: str,
        *,
        level: LogLevel = "INFO",
        symbol: str | None = None,
        client_order_id: str | None = None,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.event_logger is None:
            return
        self.event_logger.log(
            LogEvent(
                category=category,
                event=event,
                level=level,
                symbol=symbol,
                client_order_id=client_order_id,
                stage=stage,
                payload=payload or {},
            )
        )

    def sync_symbol_rules(self, symbols: list[str] | None = None) -> dict[str, SymbolRule]:
        specs: dict[str, SymbolSpec] = self.client.get_symbol_specs(symbols)
        self.symbol_rules = {
            sym: SymbolRule(
                tick_size=sp.tick_size,
                lot_size=sp.lot_size,
                min_qty=sp.min_qty,
                min_notional=sp.min_notional,
            )
            for sym, sp in specs.items()
        }
        self._log("system", "sync_symbol_rules", stage="bootstrap", payload={"count": len(self.symbol_rules)})
        return self.symbol_rules

    def build_rebalance_orders(
        self,
        *,
        current_positions: dict[str, float],
        target_weights: dict[str, float],
        prices: dict[str, float],
        total_equity: float,
    ) -> dict[str, Any]:
        gross_order_hint = total_equity * sum(abs(v) for v in target_weights.values())
        ok, reason = pretrade_check(target_weights, gross_order_hint, self.risk_limits)
        if not ok:
            self._log("system", "pretrade_blocked", level="WARN", stage="pretrade", payload={"reason": reason})
            return {"ok": False, "stage": "pretrade", "reason": reason, "orders": []}

        payload = generate_rebalance_orders(
            current_positions=current_positions,
            target_weights=target_weights,
            prices=prices,
            total_equity=total_equity,
            constraints=self.trading_constraints,
        )
        self._log("system", "rebalance_orders_built", stage="orders", payload={"count": len(payload.get("orders", []))})
        return {"ok": True, "stage": "orders", **payload}

    def execute_orders(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        total_notional = sum(abs(float(od.get("qty", 0.0)) * float(od.get("price", 0.0))) for od in orders)
        if self.config.max_orders_per_cycle is not None and len(orders) > self.config.max_orders_per_cycle:
            reason = with_code(QX_EXEC_CYCLE_LIMIT, "max_orders_per_cycle_exceeded")
            self._log("system", "cycle_blocked", level="WARN", stage="execute", payload={"reason": reason, "orders": len(orders)})
            return {"accepted": [], "rejected": [{"reason": reason, "orders": len(orders)}], "ok": False}

        if self.config.max_notional_per_cycle is not None and total_notional > self.config.max_notional_per_cycle + 1e-12:
            reason = with_code(QX_EXEC_CYCLE_LIMIT, "max_notional_per_cycle_exceeded")
            self._log(
                "system",
                "cycle_blocked",
                level="WARN",
                stage="execute",
                payload={"reason": reason, "notional": total_notional},
            )
            return {"accepted": [], "rejected": [{"reason": reason, "notional": total_notional}], "ok": False}

        allowed = {s.upper() for s in self.config.allowed_symbols} if self.config.allowed_symbols else None

        for idx, od in enumerate(orders):
            symbol = str(od["symbol"]).upper()
            side = str(od["side"]).upper()
            qty = float(od["qty"])
            price = float(od["price"])

            if allowed is not None and symbol not in allowed:
                reason = "symbol_not_allowed_in_rollout"
                rejected.append({"order": od, "reason": reason})
                self._log("trade", "order_rejected", level="WARN", symbol=symbol, stage="rollout", payload={"reason": reason})
                continue

            rule = self.symbol_rules.get(symbol)
            if rule:
                valid, why = validate_order(price=price, qty=qty, rule=rule)
                if not valid:
                    rejected.append({"order": od, "reason": why})
                    self._log("trade", "order_rejected", level="WARN", symbol=symbol, stage="validate", payload={"reason": why, "order": od})
                    continue

            client_order_id = self._client_order_id(symbol=symbol, idx=idx)
            order = ExchangeOrder(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="LIMIT" if price > 0 else "MARKET",
                price=price if price > 0 else None,
            )

            if self.config.dry_run:
                accepted.append({"order": od, "result": {"accepted": True, "dry_run": True, "clientOrderId": client_order_id}})
                self._log("trade", "order_accepted", symbol=symbol, client_order_id=client_order_id, stage="execute", payload={"dry_run": True, "order": od})
                continue

            try:
                res = self._place_with_retry(order)
                accepted.append({"order": od, "result": res})
                self._log("trade", "order_accepted", symbol=symbol, client_order_id=client_order_id, stage="execute", payload={"dry_run": False, "result": res})
            except Exception as exc:  # noqa: BLE001
                reason = f"place_failed:{exc}"
                rejected.append({"order": od, "reason": reason})
                self._log("trade", "order_rejected", level="ERROR", symbol=symbol, client_order_id=client_order_id, stage="execute", payload={"reason": reason, "order": od})

        return {"accepted": accepted, "rejected": rejected, "ok": len(rejected) == 0}

    def reconcile(self, symbol: str | None = None) -> dict[str, Any]:
        snapshot = {
            "open_orders": self.client.get_open_orders(symbol),
            "positions": [{"symbol": p.symbol, "qty": p.qty} for p in self.client.get_account_positions()],
            "symbol_rules": {
                k: {
                    "tick_size": v.tick_size,
                    "lot_size": v.lot_size,
                    "min_qty": v.min_qty,
                    "min_notional": v.min_notional,
                }
                for k, v in self.symbol_rules.items()
            },
        }
        self._log("system", "reconcile", stage="reconcile", payload={"open_orders": len(snapshot["open_orders"]), "positions": len(snapshot["positions"])})
        return snapshot

    def _place_with_retry(self, order: ExchangeOrder) -> dict[str, Any]:
        last_err: Exception | None = None
        attempts = max(1, self.config.max_retries + 1)
        for attempt in range(attempts):
            try:
                return self.client.place_order(order)
            except Exception as exc:  # noqa: BLE001
                self._log("system", "place_order_retry", level="WARN", symbol=order.symbol, client_order_id=order.client_order_id, stage="execute", payload={"attempt": attempt + 1, "error": str(exc)})
                last_err = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self.config.retry_backoff_ms / 1000)
        if last_err is None:
            raise RuntimeError(with_code(QX_EXEC_PLACE_ORDER_EMPTY, "place_order_failed_without_exception"))
        raise last_err

    def _client_order_id(self, symbol: str, idx: int) -> str:
        ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        return f"{self.config.client_order_prefix}-{symbol}-{ts}-{idx}"
