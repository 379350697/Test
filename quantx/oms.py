"""OMS/PMS primitives with optional JSONL persistence and recovery (P0)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Literal, cast


OrderStatus = Literal["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"]


@dataclass(slots=True)
class OMSOrder:
    order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    filled_qty: float = 0.0
    status: OrderStatus = "NEW"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass(slots=True)
class PortfolioLedger:
    cash: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class OrderEvent:
    ts: str
    event: Literal["submit", "fill", "status"]
    order_id: str
    payload: dict[str, Any]


class JsonlOMSStore:
    """Append-only event store for order lifecycle persistence."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_submit(self, order: OMSOrder) -> None:
        self._append(
            OrderEvent(
                ts=datetime.utcnow().isoformat(),
                event="submit",
                order_id=order.order_id,
                payload={"order": asdict(order)},
            )
        )

    def append_fill(self, order_id: str, fill_qty: float, fill_price: float) -> None:
        self._append(
            OrderEvent(
                ts=datetime.utcnow().isoformat(),
                event="fill",
                order_id=order_id,
                payload={"fill_qty": fill_qty, "fill_price": fill_price},
            )
        )

    def append_status(self, order_id: str, status: OrderStatus) -> None:
        self._append(
            OrderEvent(
                ts=datetime.utcnow().isoformat(),
                event="status",
                order_id=order_id,
                payload={"status": status},
            )
        )

    def load(self) -> list[OrderEvent]:
        if not self.path.exists():
            return []
        events: list[OrderEvent] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                events.append(
                    OrderEvent(
                        ts=str(data["ts"]),
                        event=cast(Literal["submit", "fill", "status"], str(data["event"])),
                        order_id=str(data["order_id"]),
                        payload=dict(data["payload"]),
                    )
                )
        return events

    def _append(self, ev: OrderEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(ev), ensure_ascii=False, separators=(",", ":")) + "\n")


class OrderManager:
    """Order manager with position bookkeeping and optional event persistence."""

    def __init__(self, initial_cash: float = 0.0, store: JsonlOMSStore | None = None):
        self.ledger = PortfolioLedger(cash=initial_cash)
        self._orders: dict[str, OMSOrder] = {}
        self.store = store

    def submit(self, order: OMSOrder) -> OMSOrder:
        if order.qty <= 0:
            raise ValueError("order qty must be positive")
        if order.order_id in self._orders:
            raise ValueError("duplicate order_id")
        self._orders[order.order_id] = order
        if self.store is not None:
            self.store.append_submit(order)
        return order

    def fill(self, order_id: str, fill_qty: float, fill_price: float) -> OMSOrder:
        if fill_qty <= 0 or fill_price <= 0:
            raise ValueError("fill_qty and fill_price must be positive")
        order = self._orders[order_id]
        remain = order.qty - order.filled_qty
        qty = min(fill_qty, remain)
        order.filled_qty += qty

        signed = qty if order.side == "BUY" else -qty
        self.ledger.positions[order.symbol] = self.ledger.positions.get(order.symbol, 0.0) + signed
        self.ledger.cash -= signed * fill_price

        if order.filled_qty < order.qty:
            order.status = "PARTIALLY_FILLED"
        else:
            order.status = "FILLED"

        if self.store is not None:
            self.store.append_fill(order_id=order_id, fill_qty=qty, fill_price=fill_price)
        return order

    def cancel(self, order_id: str) -> OMSOrder:
        order = self._orders[order_id]
        if order.status in {"FILLED", "REJECTED"}:
            return order
        order.status = "CANCELED"
        if self.store is not None:
            self.store.append_status(order_id=order_id, status="CANCELED")
        return order

    def reject(self, order_id: str) -> OMSOrder:
        order = self._orders[order_id]
        if order.status == "FILLED":
            return order
        order.status = "REJECTED"
        if self.store is not None:
            self.store.append_status(order_id=order_id, status="REJECTED")
        return order

    def get(self, order_id: str) -> OMSOrder:
        return self._orders[order_id]


    def list_orders(self) -> list[OMSOrder]:
        return list(self._orders.values())

    def list_working_order_ids(self) -> list[str]:
        return [oid for oid, o in self._orders.items() if o.status in {"NEW", "PARTIALLY_FILLED"}]

    @classmethod
    def recover(cls, store: JsonlOMSStore, initial_cash: float = 0.0) -> OrderManager:
        om = cls(initial_cash=initial_cash, store=store)
        events = store.load()

        # Disable append during replay to avoid duplicating events.
        om.store = None
        for ev in events:
            if ev.event == "submit":
                raw = dict(ev.payload.get("order", {}))
                order = OMSOrder(
                    order_id=str(raw["order_id"]),
                    symbol=str(raw["symbol"]),
                    side=cast(Literal["BUY", "SELL"], str(raw["side"])),
                    qty=float(raw["qty"]),
                    filled_qty=float(raw.get("filled_qty", 0.0)),
                    status=cast(
                        OrderStatus,
                        str(raw.get("status", "NEW")),
                    ),
                    created_at=str(raw.get("created_at", datetime.utcnow().isoformat())),
                )
                om._orders[order.order_id] = order
            elif ev.event == "fill":
                om.fill(order_id=ev.order_id, fill_qty=float(ev.payload["fill_qty"]), fill_price=float(ev.payload["fill_price"]))
            elif ev.event == "status":
                status = str(ev.payload["status"])
                if status == "CANCELED":
                    om.cancel(ev.order_id)
                elif status == "REJECTED":
                    om.reject(ev.order_id)

        om.store = store
        return om
