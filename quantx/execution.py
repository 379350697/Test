from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExecutionState:
    mode: str
    enabled: bool = False
    kill_switch: bool = False
    positions: dict[str, float] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


class PaperLiveExecutor:
    def __init__(self, mode: str = "paper"):
        if mode not in {"paper", "live"}:
            raise ValueError("mode must be paper/live")
        self.state = ExecutionState(mode=mode)

    def arm(self):
        self.state.enabled = True
        self.state.logs.append(f"{datetime.utcnow().isoformat()} enabled")

    def set_kill_switch(self, flag: bool = True):
        self.state.kill_switch = flag
        self.state.logs.append(f"{datetime.utcnow().isoformat()} kill_switch={flag}")

    def _apply_fill(self, symbol: str, side: str, qty: float):
        if side == "BUY":
            self.state.positions[symbol] = self.state.positions.get(symbol, 0.0) + qty
        else:
            self.state.positions[symbol] = max(0.0, self.state.positions.get(symbol, 0.0) - qty)

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: float | None = None,
        market_price: float | None = None,
        visible_qty: float | None = None,
        schedule_slices: int = 5,
        broker_quotes: dict[str, float] | None = None,
    ):
        if not self.state.enabled or self.state.kill_switch:
            return {"accepted": False, "reason": "disabled_or_killed"}
        market = market_price if market_price is not None else (limit_price if limit_price is not None else 100.0)

        if order_type == "market":
            fill_qty = qty
            fill_price = market
        elif order_type == "limit":
            if limit_price is None:
                return {"accepted": False, "reason": "missing_limit_price"}
            crossed = (side == "BUY" and market <= limit_price) or (side == "SELL" and market >= limit_price)
            if not crossed:
                rec = {"accepted": True, "filled": False, "symbol": symbol, "side": side, "qty": qty, "type": order_type}
                self.state.logs.append(f"{datetime.utcnow().isoformat()} order={rec}")
                return rec
            fill_qty = qty
            fill_price = limit_price
        elif order_type == "iceberg":
            vis = visible_qty if visible_qty and visible_qty > 0 else max(0.0001, qty * 0.1)
            chunks = int(qty / vis) + (1 if qty % vis else 0)
            self._apply_fill(symbol, side, qty)
            rec = {
                "accepted": True,
                "filled": True,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "type": order_type,
                "visible_qty": vis,
                "chunks": chunks,
                "intent_leakage_risk": "medium",
            }
            self.state.logs.append(f"{datetime.utcnow().isoformat()} order={rec}")
            return rec
        elif order_type in {"twap", "vwap"}:
            slices = max(1, int(schedule_slices))
            slice_qty = qty / slices
            self._apply_fill(symbol, side, qty)
            rec = {
                "accepted": True,
                "filled": True,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "type": order_type,
                "slices": slices,
                "slice_qty": slice_qty,
            }
            self.state.logs.append(f"{datetime.utcnow().isoformat()} order={rec}")
            return rec
        else:
            return {"accepted": False, "reason": "unsupported_order_type"}

        if broker_quotes:
            best_broker = min(broker_quotes.items(), key=lambda x: x[1])[0] if side == "BUY" else max(broker_quotes.items(), key=lambda x: x[1])[0]
        else:
            best_broker = "default"
        self._apply_fill(symbol, side, fill_qty)
        rec = {
            "accepted": True,
            "filled": True,
            "symbol": symbol,
            "side": side,
            "qty": fill_qty,
            "type": order_type,
            "fill_price": fill_price,
            "router": "smart_order_routing",
            "broker": best_broker,
            "estimated_latency_us": 5 if self.state.mode == "paper" else 20,
        }
        self.state.logs.append(f"{datetime.utcnow().isoformat()} order={rec}")
        return rec

    def close_all(self):
        for k in list(self.state.positions.keys()):
            self.state.positions[k] = 0.0
        self.state.logs.append(f"{datetime.utcnow().isoformat()} close_all")
        return {"closed": True, "positions": self.state.positions}
