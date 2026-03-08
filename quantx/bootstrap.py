"""Bootstrap helpers for safe restart takeover in live trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .live_service import LiveExecutionService
from .oms import JsonlOMSStore, OrderManager


@dataclass(slots=True)
class BootstrapTakeoverReport:
    ok: bool
    recovered_orders: int
    recovered_working_orders: int
    local_positions: dict[str, float]
    exchange_positions: dict[str, float]
    position_diffs: dict[str, float]
    local_working_order_ids: list[str]
    exchange_open_order_ids: list[str]
    missing_on_exchange: list[str]
    unmanaged_on_exchange: list[str]
    notes: list[str]


_ID_KEYS = ("clientOrderId", "client_order_id", "origClientOrderId", "clOrdId", "orderId", "id")


def _normalize_positions(raw: dict[str, float], qty_tolerance: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for symbol, qty in raw.items():
        s = str(symbol).upper()
        q = float(qty)
        if abs(q) > qty_tolerance:
            out[s] = q
    return out


def _extract_order_id(payload: dict[str, Any]) -> str:
    for key in _ID_KEYS:
        val = payload.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def bootstrap_recover_and_reconcile(
    *,
    service: LiveExecutionService,
    oms_store: JsonlOMSStore,
    initial_cash: float = 0.0,
    symbol: str | None = None,
    qty_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Recover OMS state and reconcile against exchange snapshot.

    This function is intended to run at process start after crash/restart. It does not
    auto-place/cancel orders; it only produces a deterministic takeover report so callers
    can decide whether to resume live execution.
    """

    om = OrderManager.recover(store=oms_store, initial_cash=initial_cash)
    local_positions = _normalize_positions(om.ledger.positions, qty_tolerance)

    local_working = sorted(om.list_working_order_ids())

    snapshot = service.reconcile(symbol)
    remote_positions_raw: dict[str, float] = {}
    for row in snapshot.get("positions", []):
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        remote_positions_raw[sym] = remote_positions_raw.get(sym, 0.0) + float(row.get("qty", 0.0))
    exchange_positions = _normalize_positions(remote_positions_raw, qty_tolerance)

    position_diffs: dict[str, float] = {}
    for sym in sorted(set(local_positions) | set(exchange_positions)):
        diff = local_positions.get(sym, 0.0) - exchange_positions.get(sym, 0.0)
        if abs(diff) > qty_tolerance:
            position_diffs[sym] = diff

    exchange_open_order_ids = sorted({
        oid
        for oid in (_extract_order_id(od) for od in snapshot.get("open_orders", []))
        if oid
    })

    local_set = set(local_working)
    exchange_set = set(exchange_open_order_ids)
    missing_on_exchange = sorted(local_set - exchange_set)
    unmanaged_on_exchange = sorted(exchange_set - local_set)

    notes: list[str] = []
    if position_diffs:
        notes.append("position_mismatch_detected")
    if missing_on_exchange:
        notes.append("local_working_orders_missing_on_exchange")
    if unmanaged_on_exchange:
        notes.append("exchange_open_orders_not_tracked_locally")

    report = BootstrapTakeoverReport(
        ok=(len(notes) == 0),
        recovered_orders=len(om.list_orders()),
        recovered_working_orders=len(local_working),
        local_positions=local_positions,
        exchange_positions=exchange_positions,
        position_diffs=position_diffs,
        local_working_order_ids=local_working,
        exchange_open_order_ids=exchange_open_order_ids,
        missing_on_exchange=missing_on_exchange,
        unmanaged_on_exchange=unmanaged_on_exchange,
        notes=notes,
    )
    return asdict(report)
