"""Live execution service bridging rebalance intents to exchange clients (P0/P1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import time
from typing import Any

from .error_codes import QX_EXEC_AUTO_DEGRADED, QX_EXEC_CYCLE_LIMIT, QX_EXEC_PLACE_ORDER_EMPTY, with_code
from .exchange_rules import SymbolRule, validate_order
from .runtime.live_coordinator import LiveRuntimeCoordinator
from .runtime.models import OrderIntent
from .runtime.replay_store import RuntimeReplayStore
from .runtime.session import RuntimeSession
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
    max_consecutive_failures: int | None = 5
    auto_switch_to_dry_run_on_failures: bool = True
    runtime_mode: str = "derivatives"
    exchange: str = "okx"
    enable_binance: bool = False

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
        runtime_adapter: Any | None = None,
        runtime_event_log_path: str | None = None,
    ):
        self.client = client
        self.risk_limits = risk_limits or RiskLimits()
        self.trading_constraints = trading_constraints or TradingConstraints()
        self.config = config or LiveExecutionConfig()
        self.symbol_rules: dict[str, SymbolRule] = {}
        self.event_logger = event_logger
        self.runtime_adapter = runtime_adapter
        replay_store = RuntimeReplayStore(runtime_event_log_path) if runtime_event_log_path else None
        self.runtime_coordinator = LiveRuntimeCoordinator(
            session=RuntimeSession(mode='live', wallet_balance=0.0),
            replay_store=replay_store,
        )
        self.runtime_session = self.runtime_coordinator.session
        self._consecutive_failures = 0

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
        runtime_events: list[dict[str, Any]] = []

        total_notional = sum(abs(float(od.get("qty", 0.0)) * float(od.get("price", 0.0))) for od in orders)
        if self.config.max_orders_per_cycle is not None and len(orders) > self.config.max_orders_per_cycle:
            reason = with_code(QX_EXEC_CYCLE_LIMIT, "max_orders_per_cycle_exceeded")
            self._log("system", "cycle_blocked", level="WARN", stage="execute", payload={"reason": reason, "orders": len(orders)})
            return {"accepted": [], "rejected": [{"reason": reason, "orders": len(orders)}], "ok": False, "runtime_snapshot": self.runtime_snapshot()}

        if self.config.max_notional_per_cycle is not None and total_notional > self.config.max_notional_per_cycle + 1e-12:
            reason = with_code(QX_EXEC_CYCLE_LIMIT, "max_notional_per_cycle_exceeded")
            self._log(
                "system",
                "cycle_blocked",
                level="WARN",
                stage="execute",
                payload={"reason": reason, "notional": total_notional},
            )
            return {"accepted": [], "rejected": [{"reason": reason, "notional": total_notional}], "ok": False, "runtime_snapshot": self.runtime_snapshot()}

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
            position_side = str(od.get("position_side", "long" if side == "BUY" else "short")).lower()
            margin_mode = str(od.get("margin_mode", "cross")).lower()
            order = ExchangeOrder(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="LIMIT" if price > 0 else "MARKET",
                price=price if price > 0 else None,
                position_side=position_side,
                margin_mode=margin_mode,
                reduce_only=bool(od.get("reduce_only", False)),
            )

            if self.config.dry_run:
                dry_run_res = {"accepted": True, "dry_run": True, "clientOrderId": client_order_id}
                accepted.append({"order": od, "result": dry_run_res})
                self._log("trade", "order_accepted", symbol=symbol, client_order_id=client_order_id, stage="execute", payload={"dry_run": True, "exchange": type(self.client).__name__, "order": od})
                continue

            try:
                ts = datetime.now(tz=timezone.utc).isoformat()
                res = self._place_with_retry(order)
                accepted.append({"order": od, "result": res})
                if self.runtime_adapter is not None:
                    self.runtime_coordinator.submit_intents([self._runtime_intent(order, ts=ts)], exchange=self.config.exchange, ts=ts)
                    runtime_event = self.runtime_adapter.normalize_place_order_response(order, res, ts=ts)
                    runtime_events.append(asdict(runtime_event))
                    self._apply_runtime_event(runtime_event)
                self._consecutive_failures = 0
                self._log("trade", "order_accepted", symbol=symbol, client_order_id=client_order_id, stage="execute", payload={"dry_run": False, "exchange": type(self.client).__name__, "result": res})
            except Exception as exc:  # noqa: BLE001
                self._consecutive_failures += 1
                reason = f"place_failed:{exc}"
                rejected.append({"order": od, "reason": reason})
                self._log(
                    "trade",
                    "order_rejected",
                    level="ERROR",
                    symbol=symbol,
                    client_order_id=client_order_id,
                    stage="execute",
                    payload={
                        "reason": reason,
                        "error_code": "QX-EXEC-PLACE",
                        "exchange": type(self.client).__name__,
                        "consecutive_failures": self._consecutive_failures,
                        "order": od,
                    },
                )

                limit = self.config.max_consecutive_failures
                if (
                    self.config.auto_switch_to_dry_run_on_failures
                    and limit is not None
                    and limit > 0
                    and self._consecutive_failures >= limit
                    and not self.config.dry_run
                ):
                    self.config.dry_run = True
                    self._log(
                        "system",
                        "execution_degraded_to_dry_run",
                        level="ERROR",
                        symbol=symbol,
                        client_order_id=client_order_id,
                        stage="execute",
                        payload={
                            "reason": with_code(QX_EXEC_AUTO_DEGRADED, "consecutive_failures_threshold_reached"),
                            "threshold": limit,
                            "consecutive_failures": self._consecutive_failures,
                        },
                    )

        return {"accepted": accepted, "rejected": rejected, "runtime_events": runtime_events, "runtime_snapshot": self.runtime_snapshot(), "ok": len(rejected) == 0}

    def reconcile(self, symbol: str | None = None) -> dict[str, Any]:
        runtime_events: list[dict[str, Any]] = []
        if self.runtime_adapter is not None and hasattr(self.client, "get_raw_open_orders"):
            raw_open_orders = self.client.get_raw_open_orders(symbol)
            open_orders = self.runtime_adapter.normalize_open_orders(raw_open_orders)
            for row in raw_open_orders:
                runtime_event = self.runtime_adapter.normalize_order_event(row)
                runtime_events.append(asdict(runtime_event))
                self._apply_runtime_event(runtime_event)
        else:
            open_orders = self.client.get_open_orders(symbol)

        if self.runtime_adapter is not None and hasattr(self.client, "get_raw_account_positions"):
            raw_positions = self.client.get_raw_account_positions(symbol)
            positions = self.runtime_adapter.normalize_positions(raw_positions)
            for row in raw_positions:
                runtime_event = self.runtime_adapter.normalize_position_event(row)
                runtime_events.append(asdict(runtime_event))
                self._apply_runtime_event(runtime_event)
        else:
            positions = [{"symbol": p.symbol, "qty": p.qty} for p in self.client.get_account_positions()]

        snapshot = {
            "open_orders": open_orders,
            "positions": positions,
            "runtime_positions": positions,
            "runtime_events": runtime_events,
            "runtime_snapshot": self.runtime_snapshot(),
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

    def ingest_runtime_event(self, event: Any) -> None:
        self._apply_runtime_event(event)

    def runtime_snapshot(self) -> dict[str, Any]:
        return self.runtime_coordinator.snapshot()

    def _runtime_intent(self, order: ExchangeOrder, *, ts: str) -> OrderIntent:
        return OrderIntent(
            symbol=order.symbol,
            side=order.side.lower(),
            position_side=(order.position_side or 'long').lower(),
            qty=order.qty,
            price=order.price,
            order_type=(order.order_type or 'MARKET').lower(),
            time_in_force='gtc' if order.price is not None else 'ioc',
            reduce_only=order.reduce_only,
            intent_id=order.client_order_id,
            strategy_id='live_execution',
            reason='live_execute',
            created_ts=ts,
            tags=('live', 'runtime'),
        )

    def _apply_runtime_event(self, event: Any) -> None:
        try:
            self.runtime_coordinator.apply_event(event)
        except Exception:
            return

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




