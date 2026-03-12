from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .runtime.events import FillEvent, MarketEvent, OrderEvent
from .runtime.fill_engine import FillEngine, FillEngineConfig
from .runtime.ledger_engine import LedgerEngine
from .runtime.models import OrderIntent
from .runtime.order_engine import OrderEngine


@dataclass
class ExecutionState:
    mode: str
    enabled: bool = False
    kill_switch: bool = False
    positions: dict[str, float] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    runtime: dict[str, object] = field(default_factory=dict)


class PaperLiveExecutor:
    def __init__(self, mode: str = 'paper', initial_cash: float = 10_000.0):
        if mode not in {'paper', 'live'}:
            raise ValueError('mode must be paper/live')
        self.state = ExecutionState(
            mode=mode,
            runtime={'mode': mode, 'orders': [], 'order_state_sequences': {}, 'ledger': {}, 'positions': {}},
        )
        self._order_engine = OrderEngine()
        self._ledger_engine = LedgerEngine(wallet_balance=initial_cash)
        self._fill_engine = FillEngine(FillEngineConfig(queue_delay_ticks=1, partial_fill_ratio=1.0, slippage_bps=0.0))
        self._market_prices: dict[str, float] = {}
        self._order_ids: list[str] = []
        self._order_state_sequences: dict[str, list[str]] = {}
        self._sequence = 0

    def arm(self):
        self.state.enabled = True
        self.state.logs.append(f'{self._now()} enabled')

    def set_kill_switch(self, flag: bool = True):
        self.state.kill_switch = flag
        self.state.logs.append(f'{self._now()} kill_switch={flag}')

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = 'market',
        limit_price: float | None = None,
        market_price: float | None = None,
        visible_qty: float | None = None,
        schedule_slices: int = 5,
        broker_quotes: dict[str, float] | None = None,
        position_side: str | None = None,
        reduce_only: bool = False,
    ):
        if not self.state.enabled or self.state.kill_switch:
            return {'accepted': False, 'reason': 'disabled_or_killed'}

        market = market_price if market_price is not None else (limit_price if limit_price is not None else 100.0)
        self._market_prices[symbol] = market
        resolved_position_side = position_side or ('long' if side == 'BUY' else 'short')

        extra: dict[str, object] = {}
        runtime_type = order_type if order_type == 'limit' else 'market'
        if order_type == 'iceberg':
            vis = visible_qty if visible_qty and visible_qty > 0 else max(0.0001, qty * 0.1)
            extra.update({'visible_qty': vis, 'chunks': int(qty / vis) + (1 if qty % vis else 0), 'intent_leakage_risk': 'medium'})
        elif order_type in {'twap', 'vwap'}:
            slices = max(1, int(schedule_slices))
            extra.update({'slices': slices, 'slice_qty': qty / slices})
        elif order_type not in {'market', 'limit'}:
            return {'accepted': False, 'reason': 'unsupported_order_type'}

        order, emitted = self._execute_runtime_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=runtime_type,
            limit_price=limit_price,
            market_price=market,
            position_side=resolved_position_side,
            reduce_only=reduce_only,
        )

        best_broker = (
            min(broker_quotes.items(), key=lambda x: x[1])[0]
            if broker_quotes and side == 'BUY'
            else max(broker_quotes.items(), key=lambda x: x[1])[0]
            if broker_quotes
            else 'default'
        )
        filled = any(isinstance(event, FillEvent) for event in emitted)
        fill_price = next((event.price for event in emitted if isinstance(event, FillEvent)), None)

        rec = {
            'accepted': True,
            'filled': filled,
            'symbol': symbol,
            'side': side,
            'qty': qty if filled else qty,
            'type': order_type,
            'fill_price': fill_price,
            'router': 'runtime_paper',
            'broker': best_broker,
            'estimated_latency_us': 5 if self.state.mode == 'paper' else 20,
            'position_side': resolved_position_side,
            **extra,
        }
        self.state.logs.append(f'{self._now()} order={rec}')
        return rec

    def close_all(self):
        for symbol, sides in list(self.state.runtime.get('positions', {}).items()):
            long_leg = sides.get('long', {}) if isinstance(sides, dict) else {}
            short_leg = sides.get('short', {}) if isinstance(sides, dict) else {}
            long_qty = float(long_leg.get('qty', 0.0) or 0.0)
            short_qty = float(short_leg.get('qty', 0.0) or 0.0)
            if long_qty > 0:
                self._execute_runtime_order(
                    symbol=symbol,
                    side='SELL',
                    qty=long_qty,
                    order_type='market',
                    limit_price=None,
                    market_price=self._market_prices.get(symbol, float(long_leg.get('avg_entry_price', 100.0) or 100.0)),
                    position_side='long',
                    reduce_only=True,
                )
            if short_qty > 0:
                self._execute_runtime_order(
                    symbol=symbol,
                    side='BUY',
                    qty=short_qty,
                    order_type='market',
                    limit_price=None,
                    market_price=self._market_prices.get(symbol, float(short_leg.get('avg_entry_price', 100.0) or 100.0)),
                    position_side='short',
                    reduce_only=True,
                )
        self.state.logs.append(f'{self._now()} close_all')
        return {'closed': True, 'positions': self.state.positions}

    def _record_order_state(self, client_order_id: str, status: str) -> None:
        sequence = self._order_state_sequences.setdefault(client_order_id, [])
        if not sequence or sequence[-1] != status:
            sequence.append(status)

    def _execute_runtime_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        order_type: str,
        limit_price: float | None,
        market_price: float,
        position_side: str,
        reduce_only: bool,
    ) -> tuple[object, list[object]]:
        self._sequence += 1
        client_order_id = f'{self.state.mode}-{self._sequence}'
        ts = self._now()
        intent = OrderIntent(
            symbol=symbol,
            side=side.lower(),
            position_side=position_side,
            qty=qty,
            price=limit_price if order_type == 'limit' else market_price,
            order_type=order_type,
            time_in_force='ioc' if order_type == 'market' else 'gtc',
            reduce_only=reduce_only,
        )
        order = self._order_engine.create_intent(client_order_id, intent)
        self._record_order_state(client_order_id, order.status)
        self._order_ids.append(client_order_id)
        emitted: list[object] = []

        order = self._order_engine.apply_order_event(
            OrderEvent(
                symbol=symbol,
                exchange=self.state.mode,
                ts=ts,
                client_order_id=client_order_id,
                exchange_order_id=client_order_id,
                status='risk_accepted',
                payload={'source': 'runtime_executor'},
            )
        )
        self._record_order_state(client_order_id, order.status)
        submit_events = self._fill_engine.submit_order(order, exchange=self.state.mode, ts=ts)
        for event in submit_events:
            order = self._order_engine.apply_order_event(event)
            self._record_order_state(client_order_id, order.status)
            emitted.append(event)

        market_events = self._fill_engine.on_market_event(
            MarketEvent(
                symbol=symbol,
                exchange=self.state.mode,
                channel='mark_price',
                ts=ts,
                payload={'price': market_price},
            )
        )
        for event in market_events:
            if isinstance(event, FillEvent):
                order = self._order_engine.apply_fill_event(event)
                self._record_order_state(client_order_id, order.status)
                self._ledger_engine.apply_fill(event)
            elif isinstance(event, OrderEvent):
                order = self._order_engine.apply_order_event(event)
                self._record_order_state(client_order_id, order.status)
            emitted.append(event)

        self._ledger_engine.apply_market_event(
            MarketEvent(
                symbol=symbol,
                exchange=self.state.mode,
                channel='mark_price',
                ts=ts,
                payload={'price': market_price},
            )
        )
        self._sync_state()
        return order, emitted

    def _sync_state(self) -> None:
        net_positions: dict[str, float] = {}
        runtime_positions: dict[str, dict[str, dict[str, float]]] = {}
        for (symbol, position_side), leg in self._ledger_engine.ledger.positions.items():
            runtime_positions.setdefault(symbol, {})[position_side] = {
                'qty': leg.qty,
                'avg_entry_price': leg.avg_entry_price,
                'realized_pnl': leg.realized_pnl,
                'unrealized_pnl': leg.unrealized_pnl,
                'fee_total': leg.fee_total,
                'funding_total': leg.funding_total,
            }
            net_positions.setdefault(symbol, 0.0)
            net_positions[symbol] += leg.qty if position_side == 'long' else -leg.qty
        self.state.positions = net_positions
        self.state.runtime = {
            'mode': self.state.mode,
            'orders': [
                {
                    'client_order_id': self._order_engine.get_order(order_id).client_order_id,
                    'status': self._order_engine.get_order(order_id).status,
                    'filled_qty': self._order_engine.get_order(order_id).filled_qty,
                    'side': self._order_engine.get_order(order_id).side,
                    'position_side': self._order_engine.get_order(order_id).position_side,
                }
                for order_id in self._order_ids
            ],
            'order_state_sequences': {
                order_id: list(self._order_state_sequences.get(order_id, [])) for order_id in self._order_ids
            },
            'ledger': {
                'wallet_balance': self._ledger_engine.ledger.wallet_balance,
                'equity': self._ledger_engine.ledger.equity,
                'available_margin': self._ledger_engine.ledger.available_margin,
                'used_margin': self._ledger_engine.ledger.used_margin,
                'maintenance_margin': self._ledger_engine.ledger.maintenance_margin,
                'risk_ratio': self._ledger_engine.ledger.risk_ratio,
            },
            'positions': runtime_positions,
        }

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
