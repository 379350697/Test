"""Portfolio-to-orders bridge for CTA rebalancing (P0)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class TradingConstraints:
    """Execution constraints for generating rebalance orders."""

    min_qty: float = 0.0
    min_notional: float = 10.0
    lot_size: float = 0.0001
    max_turnover_pct: float = 1.0


@dataclass(slots=True)
class RebalanceOrder:
    """Normalized rebalance order intent."""

    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    reason: str = "rebalance"


def generate_rebalance_orders(
    current_positions: dict[str, float],
    target_weights: dict[str, float],
    prices: dict[str, float],
    total_equity: float,
    constraints: TradingConstraints | None = None,
) -> dict[str, Any]:
    """Convert target portfolio weights into executable rebalance orders.

    Args:
        current_positions: symbol -> current quantity.
        target_weights: symbol -> desired portfolio weight.
        prices: symbol -> latest price.
        total_equity: portfolio equity in quote currency.
        constraints: order generation constraints.

    Returns:
        Dict with order list and diagnostics.
    """

    c = constraints or TradingConstraints()
    if total_equity <= 0:
        raise ValueError("total_equity must be positive")

    symbols = sorted(set(current_positions) | set(target_weights))
    orders: list[RebalanceOrder] = []
    skipped: list[dict[str, Any]] = []

    gross_target_notional = 0.0
    gross_delta_notional = 0.0

    for symbol in symbols:
        px = prices.get(symbol)
        if px is None or px <= 0:
            skipped.append({"symbol": symbol, "reason": "missing_or_invalid_price"})
            continue

        cur_qty = current_positions.get(symbol, 0.0)
        cur_notional = cur_qty * px

        tw = target_weights.get(symbol, 0.0)
        tgt_notional = tw * total_equity
        delta_notional = tgt_notional - cur_notional

        gross_target_notional += abs(tgt_notional)
        gross_delta_notional += abs(delta_notional)

        raw_qty = delta_notional / px
        qty = _round_lot(raw_qty, c.lot_size)
        if abs(qty) < max(c.min_qty, c.lot_size):
            skipped.append({"symbol": symbol, "reason": "below_min_qty"})
            continue

        notional = abs(qty * px)
        if notional < c.min_notional:
            skipped.append({"symbol": symbol, "reason": "below_min_notional"})
            continue

        side = "BUY" if qty > 0 else "SELL"
        orders.append(
            RebalanceOrder(
                symbol=symbol,
                side=side,
                qty=abs(qty),
                price=px,
                notional=notional,
            )
        )

    turnover_pct = gross_delta_notional / total_equity if total_equity > 0 else 0.0
    if turnover_pct > c.max_turnover_pct and turnover_pct > 0:
        scale = c.max_turnover_pct / turnover_pct
        scaled_orders: list[RebalanceOrder] = []
        for od in orders:
            scaled_qty = _round_lot(od.qty * scale, c.lot_size)
            scaled_notional = abs(scaled_qty * od.price)
            if scaled_qty >= c.min_qty and scaled_notional >= c.min_notional:
                scaled_orders.append(
                    RebalanceOrder(
                        symbol=od.symbol,
                        side=od.side,
                        qty=scaled_qty,
                        price=od.price,
                        notional=scaled_notional,
                        reason="rebalance_scaled_by_turnover",
                    )
                )
        orders = scaled_orders

    return {
        "orders": [asdict(od) for od in orders],
        "summary": {
            "symbols": len(symbols),
            "generated_orders": len(orders),
            "gross_target_notional": round(gross_target_notional, 8),
            "gross_delta_notional": round(gross_delta_notional, 8),
            "turnover_pct": round(turnover_pct, 8),
        },
        "skipped": skipped,
    }


def _round_lot(qty: float, lot_size: float) -> float:
    if lot_size <= 0:
        return qty
    return round(qty / lot_size) * lot_size
