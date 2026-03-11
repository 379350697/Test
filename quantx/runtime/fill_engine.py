from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import AccountEvent, FillEvent, MarketEvent, OrderEvent
from .models import TrackedOrder


@dataclass(slots=True)
class FillEngineConfig:
    queue_delay_ticks: int = 1
    cancel_delay_ticks: int = 1
    partial_fill_ratio: float = 1.0
    slippage_bps: float = 0.0


@dataclass(slots=True)
class _ActiveOrder:
    order: TrackedOrder
    exchange: str
    queue_ticks: int = 0
    cancel_ticks: int = 0
    cancel_requested: bool = False
    partial_fill_done: bool = False

    @property
    def remaining_qty(self) -> float:
        return max(self.order.qty - self.order.filled_qty, 0.0)


@dataclass(slots=True)
class FillEngine:
    config: FillEngineConfig = field(default_factory=FillEngineConfig)
    _active_orders: dict[str, _ActiveOrder] = field(default_factory=dict)
    _trade_seq: int = 0

    def submit_order(self, order: TrackedOrder, exchange: str, ts: str) -> list[OrderEvent]:
        self._active_orders[order.client_order_id] = _ActiveOrder(order=order, exchange=exchange)
        return [
            self._make_order_event(order, exchange, ts, 'submitted'),
            self._make_order_event(order, exchange, ts, 'acked'),
            self._make_order_event(order, exchange, ts, 'working'),
        ]

    def request_cancel(self, client_order_id: str, symbol: str, exchange: str, ts: str) -> list[OrderEvent]:
        active = self._active_orders[client_order_id]
        active.cancel_requested = True
        active.cancel_ticks = 0
        return []

    def on_market_event(self, event: MarketEvent) -> list[object]:
        emitted: list[object] = []

        for client_order_id, active in list(self._active_orders.items()):
            if active.order.symbol != event.symbol:
                continue

            if active.cancel_requested:
                active.cancel_ticks += 1
                if active.cancel_ticks >= self.config.cancel_delay_ticks:
                    emitted.append(self._make_order_event(active.order, active.exchange, event.ts, 'canceled'))
                    del self._active_orders[client_order_id]
                continue

            if not self._is_marketable(active.order, float(event.payload['price'])):
                continue

            active.queue_ticks += 1
            if active.queue_ticks < self.config.queue_delay_ticks:
                continue

            fill_qty = self._next_fill_qty(active)
            fill_price = self._apply_slippage(active.order.side, float(event.payload['price']))
            active.order.filled_qty += fill_qty
            self._trade_seq += 1

            fill_event = FillEvent(
                symbol=active.order.symbol,
                exchange=active.exchange,
                ts=event.ts,
                client_order_id=active.order.client_order_id,
                exchange_order_id=active.order.exchange_order_id,
                trade_id=f'sim-{self._trade_seq}',
                side=active.order.side,
                position_side=active.order.position_side,
                qty=fill_qty,
                price=fill_price,
                fee=0.0,
                payload={'source': 'fill_engine'},
            )
            emitted.append(fill_event)

            status = 'filled' if active.order.filled_qty >= active.order.qty else 'partially_filled'
            emitted.append(self._make_order_event(active.order, active.exchange, event.ts, status))
            emitted.append(
                AccountEvent(
                    exchange=active.exchange,
                    ts=event.ts,
                    event_type='simulated_fill',
                    payload={
                        'client_order_id': active.order.client_order_id,
                        'symbol': active.order.symbol,
                        'qty': fill_qty,
                        'price': fill_price,
                    },
                )
            )

            if status == 'filled':
                del self._active_orders[client_order_id]
            else:
                active.partial_fill_done = True

        return emitted

    def _is_marketable(self, order: TrackedOrder, market_price: float) -> bool:
        if order.order_type == 'market' or order.price is None:
            return True
        if order.side == 'buy':
            return market_price <= order.price
        return market_price >= order.price

    def _next_fill_qty(self, active: _ActiveOrder) -> float:
        remaining = active.remaining_qty
        if remaining <= 0:
            return 0.0
        if active.partial_fill_done or self.config.partial_fill_ratio >= 1.0:
            return remaining
        return min(remaining, active.order.qty * self.config.partial_fill_ratio)

    def _apply_slippage(self, side: str, market_price: float) -> float:
        slip = self.config.slippage_bps / 10_000.0
        if side == 'buy':
            return market_price * (1 + slip)
        return market_price * (1 - slip)

    def _make_order_event(self, order: TrackedOrder, exchange: str, ts: str, status: str) -> OrderEvent:
        return OrderEvent(
            symbol=order.symbol,
            exchange=exchange,
            ts=ts,
            client_order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
            status=status,
            payload={'source': 'fill_engine'},
        )
