from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any

from .live_margin_allocator import MarginAllocator
from .live_runtime_store import LiveRuntimeStore
from .live_strategy_runner import LiveStrategyRunner
from .live_supervisor import LiveSupervisor
from .runtime.models import OrderIntent


@dataclass(slots=True)
class LiveRuntimeConfig:
    watchlist: tuple[str, ...]
    strategy_name: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    total_margin: float = 0.0
    max_symbol_weight: float = 0.5
    healthy_recovery_cycles: int = 3


class LiveRuntime:
    def __init__(
        self,
        *,
        config: LiveRuntimeConfig,
        market_driver: Any,
        private_stream_transport: Any,
        service: Any,
        store: LiveRuntimeStore | None = None,
        supervisor: LiveSupervisor | None = None,
        strategy_runner: LiveStrategyRunner | None = None,
        allocator: MarginAllocator | None = None,
    ) -> None:
        self.config = config
        self.market_driver = market_driver
        self.private_stream_transport = private_stream_transport
        self.service = service
        self.store = store
        self.supervisor = supervisor or LiveSupervisor(required_healthy_cycles=config.healthy_recovery_cycles)
        self.strategy_runner = strategy_runner or LiveStrategyRunner(
            strategy_name=config.strategy_name,
            watchlist=config.watchlist,
            strategy_params=config.strategy_params,
        )
        self.allocator = allocator or MarginAllocator(
            total_margin=config.total_margin,
            max_symbol_weight=config.max_symbol_weight,
        )
        self._bootstrapped = False
        self._started_at: str | None = None
        self._updated_at: str | None = None
        self._last_market_iteration_at: str | None = None
        self._last_health_iteration_at: str | None = None
        self._restore_persisted_state()

    def bootstrap_once(self) -> dict[str, Any]:
        self._started_at = self._utc_now()
        self._apply_symbol_budgets()
        if hasattr(self.private_stream_transport, 'connect'):
            self.private_stream_transport.connect()
        self.supervisor.mark_bootstrap_ready()
        self.supervisor.mark_live_active()
        self._sync_execution_mode()
        self._bootstrapped = True
        self._persist_status()
        return self.status()

    def run_market_iteration(self) -> dict[str, Any]:
        self._last_market_iteration_at = self._utc_now()
        bars_by_symbol = self.market_driver.poll_once()
        intents = self.strategy_runner.on_bar_batch(bars_by_symbol) if bars_by_symbol else []
        orders = [self._intent_to_order(intent) for intent in intents]
        execution = self.service.execute_orders(orders) if orders and hasattr(self.service, 'execute_orders') else {'ok': True, 'accepted': [], 'rejected': []}
        result = {
            'bars_by_symbol': bars_by_symbol,
            'intents': intents,
            'orders': orders,
            'execution': execution,
            'supervisor': self.status()['supervisor'],
        }
        self._persist_status()
        return result

    def run_health_iteration(
        self,
        *,
        force_gap: bool = False,
        force_healthy: bool = False,
        cycle_boundary: bool = False,
    ) -> dict[str, Any]:
        self._last_health_iteration_at = self._utc_now()
        if force_gap:
            self.supervisor.on_stream_gap_detected(reason='stream_gap')
        else:
            healthy = bool(force_healthy)
            if hasattr(self.service, 'run_private_stream_once'):
                self.service.run_private_stream_once()
            self.supervisor.record_health_cycle(healthy=healthy, cycle_boundary=cycle_boundary)
        self._sync_execution_mode()
        self._persist_status()
        return self.status()

    def run_forever(self, *, stop_event: Any | None = None) -> None:
        if not self._bootstrapped:
            self.bootstrap_once()
        while stop_event is None or not bool(stop_event.is_set()):
            self.run_market_iteration()
            self.run_health_iteration()
            time.sleep(1)

    def status(self) -> dict[str, Any]:
        payload = {
            'process': {
                'started_at': self._started_at,
            },
            'runtime': {
                'updated_at': self._updated_at,
                'last_market_iteration_at': self._last_market_iteration_at,
                'last_health_iteration_at': self._last_health_iteration_at,
                'execution_mode': self.supervisor.execution_mode(),
            },
            'supervisor': {
                'state': self.supervisor.state,
                'execution_mode': self.supervisor.execution_mode(),
                'last_degrade_reason': self.supervisor.last_degrade_reason,
            },
            'healthy_cycle_count': self.supervisor.consecutive_healthy_cycles,
            'watchlist': list(self.config.watchlist),
            'last_closed_bar_ts': self._last_closed_bar_ts(),
        }
        if hasattr(self.service, 'circuit_breaker_snapshot'):
            payload['pilot_risk'] = self.service.circuit_breaker_snapshot()
        return payload

    def _apply_symbol_budgets(self) -> None:
        if not hasattr(self.service, 'set_symbol_budgets'):
            return
        scores = {symbol: 1.0 for symbol in self.config.watchlist}
        self.service.set_symbol_budgets(self.allocator.allocate(watchlist=self.config.watchlist, target_scores=scores))

    def _sync_execution_mode(self) -> None:
        if hasattr(self.service, 'set_execution_mode'):
            self.service.set_execution_mode(self.supervisor.execution_mode())

    def _persist_status(self) -> None:
        if self.store is None:
            return
        self._updated_at = self._utc_now()
        payload = self.store.read_status()
        for key, value in self.status().items():
            if isinstance(value, dict) and isinstance(payload.get(key), dict):
                merged = dict(payload[key])
                merged.update(value)
                payload[key] = merged
                continue
            payload[key] = value
        self.store.write_status(payload)

    def _restore_persisted_state(self) -> None:
        if self.store is None:
            return
        payload = self.store.read_status()
        if not payload:
            return
        self.supervisor.consecutive_healthy_cycles = int(payload.get('healthy_cycle_count', 0) or 0)
        if hasattr(self.market_driver, '_last_closed_bar_ts'):
            restored = payload.get('last_closed_bar_ts', {})
            if isinstance(restored, dict):
                self.market_driver._last_closed_bar_ts = {str(symbol): str(ts) for symbol, ts in restored.items()}

    def _last_closed_bar_ts(self) -> dict[str, str]:
        raw = getattr(self.market_driver, '_last_closed_bar_ts', {})
        if not isinstance(raw, dict):
            return {}
        return {str(symbol): str(ts) for symbol, ts in raw.items()}

    def _intent_to_order(self, intent: OrderIntent) -> dict[str, Any]:
        return {
            'symbol': intent.symbol,
            'side': intent.side.upper(),
            'qty': float(intent.qty),
            'price': float(intent.price or 0.0),
            'position_side': intent.position_side,
            'reduce_only': bool(intent.reduce_only),
            'metadata': dict(intent.metadata),
        }

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
