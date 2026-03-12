from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

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
    _observed_exchange_positions: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    _observed_exchange_account: dict[str, float] = field(default_factory=dict)
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
            elif isinstance(event, AccountEvent):
                if event.event_type == 'funding':
                    self.ledger_engine.apply_account_event(event)
                elif event.event_type == 'position_snapshot':
                    self._store_position_snapshot(event)
                elif event.event_type == 'account_snapshot':
                    self._store_account_snapshot(event)
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
            'observed_exchange': {
                'positions': {
                    symbol: {position_side: dict(values) for position_side, values in legs.items()}
                    for symbol, legs in self._observed_exchange_positions.items()
                },
                'account': dict(self._observed_exchange_account),
            },
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

    def _store_position_snapshot(self, event: AccountEvent) -> None:
        symbol = str(event.payload['symbol'])
        position_side = str(event.payload['position_side'])
        values = self._coerce_numeric_fields(event.payload, exclude={'symbol', 'position_side'})
        self._observed_exchange_positions.setdefault(symbol, {})[position_side] = values

    def _store_account_snapshot(self, event: AccountEvent) -> None:
        values = self._coerce_numeric_fields(event.payload, exclude={'currency'})
        self._observed_exchange_account = values

        ledger = self.ledger_engine.ledger
        if 'wallet_balance' in values:
            ledger.wallet_balance = values['wallet_balance']
        elif 'equity' in values and 'unrealized_pnl' in values:
            ledger.wallet_balance = values['equity'] - values['unrealized_pnl']
        if 'equity' in values:
            ledger.equity = values['equity']
        if 'available_margin' in values:
            ledger.available_margin = values['available_margin']
        if 'used_margin' in values:
            ledger.used_margin = values['used_margin']
        if 'maintenance_margin' in values:
            ledger.maintenance_margin = values['maintenance_margin']
        if 'risk_ratio' in values:
            ledger.risk_ratio = values['risk_ratio']

    def _coerce_numeric_fields(
        self,
        payload: dict[str, Any],
        *,
        exclude: set[str] | None = None,
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        excluded = exclude or set()
        for key, raw in payload.items():
            if key in excluded:
                continue
            try:
                values[key] = float(raw)
            except (TypeError, ValueError):
                continue
        return values

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
