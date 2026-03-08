"""Exchange rule checks: tick/lot/notional validation (P0)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SymbolRule:
    tick_size: float
    lot_size: float
    min_qty: float
    min_notional: float


def validate_order(price: float, qty: float, rule: SymbolRule) -> tuple[bool, str]:
    """Validate order by exchange constraints."""

    if price <= 0 or qty <= 0:
        return False, "non_positive_price_or_qty"
    if qty < rule.min_qty:
        return False, "below_min_qty"
    if price * qty < rule.min_notional:
        return False, "below_min_notional"
    if not _is_multiple(price, rule.tick_size):
        return False, "invalid_tick_size"
    if not _is_multiple(qty, rule.lot_size):
        return False, "invalid_lot_size"
    return True, "ok"


def _is_multiple(value: float, step: float, eps: float = 1e-9) -> bool:
    if step <= 0:
        return True
    k = round(value / step)
    return abs(value - k * step) <= eps * max(1.0, abs(value))
