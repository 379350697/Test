from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .events import AccountEvent, FillEvent, MarketEvent, OrderEvent
from .ledger_engine import LedgerEngine
from .models import OrderIntent
from .order_engine import OrderEngine
from .runtime_risk import RuntimeRiskValidator


@dataclass(slots=True)
class RuntimeSession:
    mode: str
    wallet_balance: float = 0.0
    order_engine: OrderEngine = field(default_factory=OrderEngine)
    risk_validator: RuntimeRiskValidator = field(default_factory=RuntimeRiskValidator)
    ledger_engine: LedgerEngine = field(init=False)
    _order_ids: list[str] = field(default_factory=list)
    _order_state_sequences: dict[str, list[str]] = field(default_factory=dict)
    _sequence: int = 0

    def __post_init__(self) -> None:
        self.ledger_engine = LedgerEngine(wallet_balance=self.wallet_balance)

    def submit_intents(self, intents: Iterable[OrderIntent], *, exchange: str, ts: str) -> list[OrderEvent]:
        emitted: list[OrderEvent] = []
        for intent in intents:
            client_order_id = self._next_client_order_id(intent)
            order = self.order_engine.create_intent(client_order_id, intent)
            self._order_ids.append(client_order_id)
            self._record_state(client_order_id, order.status)

            ok, reason = self.risk_validator.validate_intent(intent, self.ledger_engine.ledger)
            if not ok:
                reject = self._make_order_event(
                    order.symbol,
                    exchange,
                    ts,
                    client_order_id,
                    'rejected',
                    reason=reason,
                )
                self.order_engine.apply_order_event(reject)
                self._record_state(client_order_id, 'rejected')
                emitted.append(reject)
                continue

            for status in ('risk_accepted', 'submitted'):
                event = self._make_order_event(
                    order.symbol,
                    exchange,
                    ts,
                    client_order_id,
                    status,
                    reason=intent.reason,
                )
                self.order_engine.apply_order_event(event)
                self._record_state(client_order_id, status)
                emitted.append(event)
        return emitted

    def apply_events(self, events: Iterable[object]) -> list[object]:
        applied: list[object] = []
        for event in events:
            if isinstance(event, OrderEvent):
                order = self.order_engine.apply_order_event(event)
                self._record_state(order.client_order_id, order.status)
            elif isinstance(event, FillEvent):
                order = self.order_engine.apply_fill_event(event)
                self.ledger_engine.apply_fill(event)
                self._record_state(order.client_order_id, order.status)
            elif isinstance(event, MarketEvent):
                self.ledger_engine.apply_market_event(event)
            elif isinstance(event, AccountEvent) and event.event_type == 'funding':
                self.ledger_engine.apply_account_event(event)
            applied.append(event)
        return applied

    def snapshot(self) -> dict[str, object]:
        positions: dict[str, dict[str, dict[str, float]]] = {}
        for (symbol, position_side), leg in self.ledger_engine.ledger.positions.items():
            positions.setdefault(symbol, {})[position_side] = {
                'qty': leg.qty,
                'avg_entry_price': leg.avg_entry_price,
                'realized_pnl': leg.realized_pnl,
                'unrealized_pnl': leg.unrealized_pnl,
                'fee_total': leg.fee_total,
                'funding_total': leg.funding_total,
            }

        return {
            'mode': self.mode,
            'orders': [
                {
                    'client_order_id': self.order_engine.get_order(order_id).client_order_id,
                    'status': self.order_engine.get_order(order_id).status,
                    'filled_qty': self.order_engine.get_order(order_id).filled_qty,
                    'side': self.order_engine.get_order(order_id).side,
                    'position_side': self.order_engine.get_order(order_id).position_side,
                    'intent_id': self.order_engine.get_order(order_id).intent_id,
                    'strategy_id': self.order_engine.get_order(order_id).strategy_id,
                }
                for order_id in self._order_ids
            ],
            'order_state_sequences': {
                order_id: list(self._order_state_sequences.get(order_id, [])) for order_id in self._order_ids
            },
            'ledger': {
                'wallet_balance': self.ledger_engine.ledger.wallet_balance,
                'equity': self.ledger_engine.ledger.equity,
                'available_margin': self.ledger_engine.ledger.available_margin,
                'used_margin': self.ledger_engine.ledger.used_margin,
                'maintenance_margin': self.ledger_engine.ledger.maintenance_margin,
                'risk_ratio': self.ledger_engine.ledger.risk_ratio,
            },
            'positions': positions,
        }

    def _next_client_order_id(self, intent: OrderIntent) -> str:
        self._sequence += 1
        if intent.intent_id:
            return intent.intent_id
        return f'{self.mode}-{self._sequence}'

    def _record_state(self, client_order_id: str, status: str) -> None:
        sequence = self._order_state_sequences.setdefault(client_order_id, [])
        if not sequence or sequence[-1] != status:
            sequence.append(status)

    def _make_order_event(
        self,
        symbol: str,
        exchange: str,
        ts: str,
        client_order_id: str,
        status: str,
        *,
        reason: str | None,
    ) -> OrderEvent:
        return OrderEvent(
            symbol=symbol,
            exchange=exchange,
            ts=ts,
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,
            status=status,
            payload={'reason': reason} if reason else {},
        )
