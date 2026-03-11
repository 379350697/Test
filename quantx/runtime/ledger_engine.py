from __future__ import annotations

from dataclasses import dataclass, field

from .events import AccountEvent, FillEvent, MarketEvent
from .models import AccountLedger, PositionLeg


@dataclass(slots=True)
class LedgerEngine:
    wallet_balance: float = 0.0
    initial_margin_ratio: float = 0.1
    maintenance_margin_ratio: float = 0.05
    ledger: AccountLedger = field(init=False)
    _mark_prices: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.ledger = AccountLedger(
            wallet_balance=self.wallet_balance,
            equity=self.wallet_balance,
            available_margin=self.wallet_balance,
        )

    def apply_fill(self, event: FillEvent) -> AccountLedger:
        leg = self._get_leg(event.symbol, event.position_side)
        reducing = self._is_reducing(event.position_side, event.side)

        if reducing:
            if event.qty > leg.qty:
                raise ValueError(
                    f'cannot reduce {event.position_side} leg below zero for {event.symbol}'
                )

            realized = self._realized_pnl(leg, event.price, event.qty)
            leg.qty -= event.qty
            leg.realized_pnl += realized
            self.ledger.wallet_balance += realized - event.fee
            if leg.qty == 0:
                leg.avg_entry_price = 0.0
        else:
            total_qty = leg.qty + event.qty
            if total_qty <= 0:
                raise ValueError(f'invalid resulting quantity for {event.symbol} {event.position_side}')
            leg.avg_entry_price = (
                (leg.avg_entry_price * leg.qty) + (event.price * event.qty)
            ) / total_qty
            leg.qty = total_qty
            self.ledger.wallet_balance -= event.fee

        leg.fee_total += event.fee
        self._refresh_account_state()
        return self.ledger

    def apply_market_event(self, event: MarketEvent) -> AccountLedger:
        if 'price' not in event.payload:
            raise ValueError('market_event payload must include price')
        self._mark_prices[event.symbol] = float(event.payload['price'])
        self._refresh_account_state()
        return self.ledger

    def apply_account_event(self, event: AccountEvent) -> AccountLedger:
        if event.event_type != 'funding':
            raise ValueError(f'unsupported account event {event.event_type}')

        symbol = event.payload['symbol']
        position_side = event.payload['position_side']
        amount = float(event.payload['amount'])
        leg = self._get_leg(symbol, position_side)
        leg.funding_total += amount
        self.ledger.wallet_balance += amount
        self._refresh_account_state()
        return self.ledger

    def _get_leg(self, symbol: str, position_side: str) -> PositionLeg:
        key = (symbol, position_side)
        leg = self.ledger.positions.get(key)
        if leg is None:
            leg = PositionLeg(symbol=symbol, position_side=position_side)
            self.ledger.positions[key] = leg
        return leg

    def _is_reducing(self, position_side: str, side: str) -> bool:
        return (position_side == 'long' and side == 'sell') or (
            position_side == 'short' and side == 'buy'
        )

    def _realized_pnl(self, leg: PositionLeg, fill_price: float, qty: float) -> float:
        if leg.position_side == 'long':
            return (fill_price - leg.avg_entry_price) * qty
        return (leg.avg_entry_price - fill_price) * qty

    def _refresh_account_state(self) -> None:
        total_notional = 0.0
        total_unrealized = 0.0

        for leg in self.ledger.positions.values():
            mark_price = self._mark_prices.get(leg.symbol, leg.avg_entry_price)
            notional = abs(leg.qty * mark_price)
            total_notional += notional

            if leg.qty == 0:
                leg.unrealized_pnl = 0.0
                continue

            if leg.position_side == 'long':
                leg.unrealized_pnl = (mark_price - leg.avg_entry_price) * leg.qty
            else:
                leg.unrealized_pnl = (leg.avg_entry_price - mark_price) * leg.qty
            total_unrealized += leg.unrealized_pnl

        self.ledger.used_margin = total_notional * self.initial_margin_ratio
        self.ledger.maintenance_margin = total_notional * self.maintenance_margin_ratio
        self.ledger.equity = self.ledger.wallet_balance + total_unrealized
        self.ledger.available_margin = self.ledger.equity - self.ledger.used_margin
        self.ledger.risk_ratio = (
            self.ledger.maintenance_margin / self.ledger.equity if self.ledger.equity else float('inf')
        )
