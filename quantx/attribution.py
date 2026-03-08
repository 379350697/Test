"""Simple PnL attribution utilities (P2)."""

from __future__ import annotations

from typing import Any


def pnl_attribution(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate realized pnl and fees by symbol and reason."""

    by_symbol: dict[str, float] = {}
    by_reason: dict[str, float] = {}
    total_fee = 0.0
    total_pnl = 0.0

    for t in trades:
        symbol = str(t.get("symbol", "UNKNOWN"))
        reason = str(t.get("reason", "unknown"))
        pnl = float(t.get("realized_pnl", 0.0))
        fee = float(t.get("fee", 0.0))

        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + pnl
        by_reason[reason] = by_reason.get(reason, 0.0) + pnl
        total_fee += fee
        total_pnl += pnl

    return {
        "total_realized_pnl": round(total_pnl, 8),
        "total_fee": round(total_fee, 8),
        "by_symbol": {k: round(v, 8) for k, v in by_symbol.items()},
        "by_reason": {k: round(v, 8) for k, v in by_reason.items()},
    }
