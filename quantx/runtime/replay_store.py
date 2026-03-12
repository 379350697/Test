from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable

from .events import AccountEvent, FillEvent, MarketEvent, OrderEvent
from .models import OrderIntent
from .order_engine import OrderStateError
from .session import RuntimeSession


class RuntimeReplayStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def append(self, event: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize_event(event)
        with self.path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def append_all(self, events: Iterable[Any]) -> None:
        for event in events:
            self.append(event)

    def load(self) -> tuple[list[dict[str, Any]], int]:
        if not self.path.exists():
            return [], 0
        rows: list[dict[str, Any]] = []
        invalid = 0
        with self.path.open('r', encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if isinstance(raw, dict):
                    rows.append(raw)
                else:
                    invalid += 1
        return rows, invalid

    def rebuild_session(self, *, wallet_balance: float = 0.0, mode: str = 'live') -> RuntimeSession:
        rows, _ = self.load()
        session = RuntimeSession(mode=mode, wallet_balance=wallet_balance)
        for row in rows:
            self._replay_row(session, row)
        return session

    @staticmethod
    def market_tape(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if str(row.get('kind', '')) == 'market_event']

    @staticmethod
    def execution_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {'order_event', 'fill_event', 'account_event'}
        return [row for row in rows if str(row.get('kind', '')) in allowed]

    def _replay_row(self, session: RuntimeSession, row: dict[str, Any]) -> None:
        kind = str(row.get('kind', ''))
        payload = row.get('payload', {}) if isinstance(row.get('payload'), dict) else {}
        symbol = str(row.get('symbol', '')).upper()
        exchange = str(row.get('exchange', 'replay'))
        ts = str(row.get('ts', ''))
        client_order_id = str(row.get('client_order_id', ''))

        if kind == 'order_event':
            self._ensure_order(session, client_order_id, symbol, payload)
            status = str(row.get('status', ''))
            if status == 'intent_created':
                return
            self._prime_order_state(session, client_order_id, symbol, exchange, ts, status)
            event = OrderEvent(
                symbol=symbol,
                exchange=exchange,
                ts=ts,
                client_order_id=client_order_id,
                exchange_order_id=self._none_if_empty(row.get('exchange_order_id')),
                status=status,
                payload=payload,
            )
            try:
                session.apply_events([event])
            except (OrderStateError, ValueError):
                return
            return

        if kind == 'fill_event':
            event = FillEvent(
                symbol=symbol,
                exchange=exchange,
                ts=ts,
                client_order_id=client_order_id,
                exchange_order_id=self._none_if_empty(row.get('exchange_order_id')),
                trade_id=str(row.get('trade_id', '')),
                side=str(row.get('side', 'buy')).lower(),
                position_side=str(row.get('position_side', 'long')).lower(),
                qty=float(row.get('qty', 0.0) or 0.0),
                price=float(row.get('price', 0.0) or 0.0),
                fee=float(row.get('fee', 0.0) or 0.0),
                payload=payload,
            )
            self._ensure_order(session, client_order_id, symbol, payload, fill_event=event)
            self._prime_order_state(session, client_order_id, symbol, exchange, ts, 'acked')
            try:
                session.apply_events([event])
            except (OrderStateError, ValueError):
                return
            return

        if kind == 'account_event':
            event = AccountEvent(
                exchange=exchange,
                ts=ts,
                event_type=str(row.get('event_type', 'funding')),
                payload=payload,
            )
            session.apply_events([event])
            return

        if kind == 'market_event':
            event = MarketEvent(
                symbol=symbol,
                exchange=exchange,
                channel=str(row.get('channel', 'market')),
                ts=ts,
                payload=payload,
            )
            session.apply_events([event])

    def _ensure_order(
        self,
        session: RuntimeSession,
        client_order_id: str,
        symbol: str,
        payload: dict[str, Any],
        *,
        fill_event: FillEvent | None = None,
    ) -> None:
        if not client_order_id:
            return
        try:
            order = session.order_engine.get_order(client_order_id)
        except OrderStateError:
            price = self._float_or_none(payload.get('price'))
            qty = float(payload.get('qty', fill_event.qty if fill_event is not None else 0.0) or 0.0)
            intent = OrderIntent(
                symbol=symbol,
                side=str(payload.get('side', fill_event.side if fill_event is not None else 'buy')).lower(),
                position_side=str(payload.get('position_side', fill_event.position_side if fill_event is not None else 'long')).lower(),
                qty=qty,
                price=price if price is not None else (fill_event.price if fill_event is not None else None),
                order_type=str(payload.get('order_type', 'market')).lower(),
                time_in_force=str(payload.get('time_in_force', 'gtc')).lower(),
                reduce_only=bool(payload.get('reduce_only', False)),
                intent_id=client_order_id,
                strategy_id=payload.get('strategy_id'),
                signal_id=payload.get('signal_id'),
                reason=payload.get('reason'),
                created_ts=payload.get('created_ts'),
                tags=tuple(payload.get('tags', [])) if isinstance(payload.get('tags'), list) else (),
            )
            session.order_engine.create_intent(client_order_id, intent)
            if client_order_id not in session._order_ids:
                session._order_ids.append(client_order_id)
            session._record_state(client_order_id, 'intent_created')
            order = session.order_engine.get_order(client_order_id)

        if fill_event is not None and order.qty <= 0.0:
            order.qty = fill_event.qty
            order.price = fill_event.price
            order.side = fill_event.side
            order.position_side = fill_event.position_side

    def _prime_order_state(
        self,
        session: RuntimeSession,
        client_order_id: str,
        symbol: str,
        exchange: str,
        ts: str,
        target_status: str,
    ) -> None:
        prerequisites = {
            'risk_accepted': ['risk_accepted'],
            'submitted': ['risk_accepted', 'submitted'],
            'acked': ['risk_accepted', 'submitted', 'acked'],
            'working': ['risk_accepted', 'submitted', 'acked', 'working'],
            'partially_filled': ['risk_accepted', 'submitted', 'acked', 'working'],
            'filled': ['risk_accepted', 'submitted', 'acked', 'working'],
            'canceled': ['risk_accepted', 'submitted', 'acked', 'working'],
            'expired': ['risk_accepted', 'submitted'],
            'rejected': [],
        }
        for status in prerequisites.get(target_status, []):
            try:
                order = session.order_engine.get_order(client_order_id)
            except OrderStateError:
                return
            if order.status == status:
                continue
            try:
                session.apply_events([
                    OrderEvent(
                        symbol=symbol,
                        exchange=exchange,
                        ts=ts,
                        client_order_id=client_order_id,
                        exchange_order_id=client_order_id,
                        status=status,
                        payload={},
                    )
                ])
            except (OrderStateError, ValueError):
                return

    def _serialize_event(self, event: Any) -> dict[str, Any]:
        if is_dataclass(event):
            payload = asdict(event)
        elif isinstance(event, dict):
            payload = dict(event)
        else:
            raise TypeError('runtime replay events must be dataclasses or dicts')
        return self._serialize_value(payload)

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(v) for v in value]
        if isinstance(value, tuple):
            return [self._serialize_value(v) for v in value]
        return value

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if value is None or value == '':
            return None
        return float(value)

    @staticmethod
    def _none_if_empty(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text or None
