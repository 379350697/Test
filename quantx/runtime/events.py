from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    MARKET = 'market_event'
    ORDER = 'order_event'
    FILL = 'fill_event'
    ACCOUNT = 'account_event'


@dataclass(slots=True)
class MarketEvent:
    symbol: str
    exchange: str
    channel: str
    ts: str
    payload: dict[str, Any]
    kind: EventKind = field(init=False, default=EventKind.MARKET)


@dataclass(slots=True)
class OrderEvent:
    symbol: str
    exchange: str
    ts: str
    client_order_id: str
    exchange_order_id: str | None
    status: str
    payload: dict[str, Any]
    kind: EventKind = field(init=False, default=EventKind.ORDER)


@dataclass(slots=True)
class FillEvent:
    symbol: str
    exchange: str
    ts: str
    client_order_id: str
    exchange_order_id: str | None
    trade_id: str
    side: str
    position_side: str
    qty: float
    price: float
    fee: float
    payload: dict[str, Any]
    kind: EventKind = field(init=False, default=EventKind.FILL)


@dataclass(slots=True)
class AccountEvent:
    exchange: str
    ts: str
    event_type: str
    payload: dict[str, Any]
    kind: EventKind = field(init=False, default=EventKind.ACCOUNT)
