from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .events import FillEvent, OrderEvent
from .health import RuntimeHealthState
from .models import OrderIntent
from .order_engine import OrderStateError
from .replay_store import RuntimeReplayStore
from .session import RuntimeSession


@dataclass(slots=True)
class LiveRuntimeCoordinator:
    session: RuntimeSession
    replay_store: RuntimeReplayStore | None = None
    health: RuntimeHealthState = field(default_factory=RuntimeHealthState)

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
        try:
            if self.replay_store is not None:
                self.replay_store.append(event)
            self._prime_event_state(event)
            self.session.apply_events([event])
        except Exception as exc:
            self.health.mark_apply_error(exc, stage='apply_event')
            raise
        return event

    def snapshot(self) -> dict[str, object]:
        return self.session.snapshot()

    def status(self, *, now_ts: str | None = None, stale_after_s: int = 30) -> dict[str, Any]:
        self.health.mark_replay_persistence(bool(self.replay_store is not None and self.replay_store.path.exists()))
        return self.health.snapshot(now_ts=now_ts, stale_after_s=stale_after_s)

    def _prime_event_state(self, event: object) -> None:
        if not isinstance(event, FillEvent):
            return

        client_order_id = event.client_order_id
        if not client_order_id:
            return

        try:
            order = self.session.order_engine.get_order(client_order_id)
        except OrderStateError:
            intent = OrderIntent(
                symbol=event.symbol,
                side=event.side,
                position_side=event.position_side,
                qty=event.qty,
                price=event.price,
                order_type='market',
                time_in_force='ioc',
                reduce_only=False,
                intent_id=client_order_id,
                strategy_id='private_stream',
                reason='private_stream_fill',
                created_ts=event.ts,
                tags=('private_stream',),
            )
            self.session.order_engine.create_intent(client_order_id, intent)
            if client_order_id not in self.session._order_ids:
                self.session._order_ids.append(client_order_id)
            self.session._record_state(client_order_id, 'intent_created')
            order = self.session.order_engine.get_order(client_order_id)

        for status in ('risk_accepted', 'submitted', 'acked'):
            if order.status == status:
                continue
            try:
                self.session.apply_events([
                    OrderEvent(
                        symbol=event.symbol,
                        exchange=event.exchange,
                        ts=event.ts,
                        client_order_id=client_order_id,
                        exchange_order_id=event.exchange_order_id,
                        status=status,
                        payload={},
                    )
                ])
            except OrderStateError:
                break
            order = self.session.order_engine.get_order(client_order_id)

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
