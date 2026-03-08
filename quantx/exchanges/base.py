"""Common exchange client interface for Binance/OKX live execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Any


@dataclass(slots=True)
class ExchangeOrder:
    client_order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    price: float | None = None


@dataclass(slots=True)
class ExchangePosition:
    symbol: str
    qty: float


@dataclass(slots=True)
class SymbolSpec:
    symbol: str
    tick_size: float
    lot_size: float
    min_qty: float
    min_notional: float


@runtime_checkable
class ExchangeClient(Protocol):
    """Interface required by live trading service."""

    def place_order(self, order: ExchangeOrder) -> dict[str, Any]:
        ...

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        ...

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        ...

    def get_account_positions(self) -> list[ExchangePosition]:
        ...

    def get_symbol_specs(self, symbols: list[str] | None = None) -> dict[str, SymbolSpec]:
        ...
