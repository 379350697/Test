from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .events import OrderEvent
from .models import OrderIntent
from .replay_store import RuntimeReplayStore
from .session import RuntimeSession


@dataclass(slots=True)
class LiveRuntimeCoordinator:
    session: RuntimeSession
    replay_store: RuntimeReplayStore | None = None
    degraded: bool = False

    def submit_intents(self, intents: Iterable[OrderIntent], *, exchange: str, ts: str) -> list[OrderEvent]:
        intent_list = list(intents)
        start = len(self.session._order_ids)
        emitted = self.session.submit_intents(intent_list, exchange=exchange, ts=ts)
        new_order_ids = self.session._order_ids[start:]
        events_by_client: dict[str, list[OrderEvent]] = {}
        for event in emitted:
            events_by_client.setdefault(event.client_order_id, []).append(event)

        persisted: list[OrderEvent] = []
        for intent, client_order_id in zip(intent_list, new_order_ids):
            persisted.append(self._intent_created_event(intent, exchange=exchange, ts=ts, client_order_id=client_order_id))
            persisted.extend(events_by_client.get(client_order_id, []))

        if self.replay_store is not None:
            self.replay_store.append_all(persisted)
        return emitted

    def apply_event(self, event: object) -> object:
        if self.replay_store is not None:
            self.replay_store.append(event)
        self.session.apply_events([event])
        return event

    def snapshot(self) -> dict[str, object]:
        return self.session.snapshot()

    def status(self) -> dict[str, bool]:
        return {
            'replay_persistence': bool(self.replay_store is not None and self.replay_store.path.exists()),
            'degraded': self.degraded,
        }

    def _intent_created_event(
        self,
        intent: OrderIntent,
        *,
        exchange: str,
        ts: str,
        client_order_id: str,
    ) -> OrderEvent:
        return OrderEvent(
            symbol=intent.symbol,
            exchange=exchange,
            ts=ts,
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,
            status='intent_created',
            payload={
                'side': intent.side,
                'position_side': intent.position_side,
                'qty': intent.qty,
                'price': intent.price,
                'order_type': intent.order_type,
                'time_in_force': intent.time_in_force,
                'reduce_only': intent.reduce_only,
                'intent_id': intent.intent_id,
                'strategy_id': intent.strategy_id,
                'signal_id': intent.signal_id,
                'reason': intent.reason,
                'created_ts': intent.created_ts,
                'tags': list(intent.tags),
            },
        )

