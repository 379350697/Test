from __future__ import annotations

from dataclasses import dataclass, field

from .events import FillEvent, OrderEvent
from .models import OrderIntent, TrackedOrder


class OrderStateError(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    'intent_created': {'risk_accepted', 'rejected'},
    'risk_accepted': {'submitted', 'rejected', 'expired'},
    'submitted': {'acked', 'rejected', 'expired'},
    'acked': {'working', 'canceled', 'expired', 'rejected'},
    'working': {'canceled', 'expired', 'rejected'},
    'partially_filled': {'canceled', 'expired'},
    'filled': set(),
    'rejected': set(),
    'canceled': set(),
    'expired': set(),
}


@dataclass(slots=True)
class OrderEngine:
    orders: dict[str, TrackedOrder] = field(default_factory=dict)
    _seen_trades: dict[str, set[str]] = field(default_factory=dict)

    def create_intent(self, client_order_id: str, intent: OrderIntent) -> TrackedOrder:
        if client_order_id in self.orders:
            raise OrderStateError(f'order {client_order_id} already exists')

        order = TrackedOrder(
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            position_side=intent.position_side,
            qty=intent.qty,
            price=intent.price,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            reduce_only=intent.reduce_only,
            intent_id=intent.intent_id,
            strategy_id=intent.strategy_id,
            signal_id=intent.signal_id,
            reason=intent.reason,
            created_ts=intent.created_ts,
            tags=intent.tags,
        )
        self.orders[client_order_id] = order
        self._seen_trades[client_order_id] = set()
        return order

    def get_order(self, client_order_id: str) -> TrackedOrder:
        try:
            return self.orders[client_order_id]
        except KeyError as exc:
            raise OrderStateError(f'unknown order {client_order_id}') from exc

    def apply_order_event(self, event: OrderEvent) -> TrackedOrder:
        order = self.get_order(event.client_order_id)
        if event.exchange_order_id is not None:
            order.exchange_order_id = event.exchange_order_id

        if order.status == event.status:
            return order

        allowed = _ALLOWED_TRANSITIONS.get(order.status, set())
        if event.status not in allowed:
            raise OrderStateError(
                f'invalid transition from {order.status} to {event.status} for {event.client_order_id}'
            )

        order.status = event.status
        return order

    def apply_fill_event(self, event: FillEvent) -> TrackedOrder:
        order = self.get_order(event.client_order_id)
        trades = self._seen_trades[event.client_order_id]

        if event.trade_id in trades:
            return order

        if order.status not in {'acked', 'working', 'partially_filled'}:
            raise OrderStateError(
                f'cannot apply fill while order {event.client_order_id} is {order.status}'
            )

        trades.add(event.trade_id)
        if event.exchange_order_id is not None:
            order.exchange_order_id = event.exchange_order_id

        order.filled_qty = min(order.qty, order.filled_qty + event.qty)
        order.status = 'filled' if order.filled_qty >= order.qty else 'partially_filled'
        return order
