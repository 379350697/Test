from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class OrderIntent:
    symbol: str
    side: str
    position_side: str
    qty: float
    price: float | None
    order_type: str
    time_in_force: str
    reduce_only: bool = False
    intent_id: str | None = None
    strategy_id: str | None = None
    signal_id: str | None = None
    reason: str | None = None
    created_ts: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class TrackedOrder:
    client_order_id: str
    symbol: str
    side: str
    position_side: str
    qty: float
    order_type: str
    time_in_force: str
    reduce_only: bool = False
    status: str = 'intent_created'
    exchange_order_id: str | None = None
    price: float | None = None
    filled_qty: float = 0.0
    intent_id: str | None = None
    strategy_id: str | None = None
    signal_id: str | None = None
    reason: str | None = None
    created_ts: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class PositionLeg:
    symbol: str
    position_side: str
    qty: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    fee_total: float = 0.0
    funding_total: float = 0.0

    @property
    def key(self) -> tuple[str, str]:
        return (self.symbol, self.position_side)


@dataclass(slots=True)
class AccountLedger:
    wallet_balance: float = 0.0
    equity: float = 0.0
    available_margin: float = 0.0
    used_margin: float = 0.0
    maintenance_margin: float = 0.0
    risk_ratio: float = 0.0
    positions: dict[tuple[str, str], PositionLeg] = field(default_factory=dict)
