"""Bootstrap helpers for safe restart takeover in live trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .live_service import LiveExecutionService
from .oms import JsonlOMSStore, OrderManager
from .runtime.health import RuntimeHealthState
from .runtime.replay_store import RuntimeReplayStore


LIVE_BOOTSTRAP_RESUME_MODES = {'reduce_only', 'live'}


@dataclass(slots=True)
class BootstrapTakeoverReport:
    ok: bool
    recovery_mode: str
    resume_mode: str
    runtime_status: dict[str, Any]
    recovered_orders: int
    recovered_working_orders: int
    local_positions: dict[str, float]
    runtime_positions: dict[str, Any]
    exchange_positions: dict[str, float]
    position_diffs: dict[str, float]
    local_working_order_ids: list[str]
    exchange_open_order_ids: list[str]
    missing_on_exchange: list[str]
    unmanaged_on_exchange: list[str]
    notes: list[str]
    promotion_policy: dict[str, Any]


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


def _normalize_runtime_snapshot_positions(raw: dict[str, Any], qty_tolerance: float) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for symbol, sides in raw.items():
        sym = str(symbol).upper()
        if not isinstance(sides, dict):
            continue
        long_qty = float((sides.get('long', {}) or {}).get('qty', 0.0) or 0.0)
        short_qty = float((sides.get('short', {}) or {}).get('qty', 0.0) or 0.0)
        net_qty = long_qty - short_qty
        if abs(net_qty) > qty_tolerance:
            normalized[sym] = net_qty
    return normalized


def _normalize_runtime_position_rows(rows: list[dict[str, Any]], qty_tolerance: float) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for row in rows:
        sym = str(row.get('symbol', '')).upper()
        if not sym:
            continue
        normalized[sym] = normalized.get(sym, 0.0) + float(row.get('qty', 0.0) or 0.0)
    return _normalize_positions(normalized, qty_tolerance)


def _warm_runtime_snapshot(runtime_event_log_path: str | None, initial_cash: float) -> dict[str, Any] | None:
    if not runtime_event_log_path:
        return None
    replay_store = RuntimeReplayStore(runtime_event_log_path)
    rows, _ = replay_store.load()
    if not rows:
        return None
    return replay_store.rebuild_session(wallet_balance=initial_cash, mode='live').snapshot()


def _derive_resume_mode(
    *,
    recovery_mode: str,
    position_diffs: dict[str, float],
    missing_on_exchange: list[str],
    unmanaged_on_exchange: list[str],
) -> str:
    if recovery_mode == 'cold':
        return 'blocked'
    if position_diffs:
        return 'read_only'
    if missing_on_exchange or unmanaged_on_exchange:
        return 'reduce_only'
    return 'live'


def _build_promotion_policy(*, recovery_mode: str, resume_mode: str, runtime_status: dict[str, Any]) -> dict[str, Any]:
    runtime_truth_ok = (
        bool(runtime_status.get('replay_persistence'))
        and not bool(runtime_status.get('degraded'))
        and bool(runtime_status.get('reconcile_ok', True))
    )
    live_capital_allowed = (
        recovery_mode == 'warm'
        and resume_mode in LIVE_BOOTSTRAP_RESUME_MODES
        and runtime_truth_ok
    )
    return {
        'recovery_mode': recovery_mode,
        'resume_mode': resume_mode,
        'runtime_truth_ok': runtime_truth_ok,
        'requires_paper_soak': True,
        'live_capital_allowed': live_capital_allowed,
    }


def bootstrap_recover_and_reconcile(
    *,
    service: LiveExecutionService,
    oms_store: JsonlOMSStore,
    initial_cash: float = 0.0,
    symbol: str | None = None,
    qty_tolerance: float = 1e-9,
    runtime_event_log_path: str | None = None,
) -> dict[str, Any]:
    """Recover OMS state and reconcile against exchange snapshot.

    This function is intended to run at process start after crash/restart. It does not
    auto-place/cancel orders; it only produces a deterministic takeover report so callers
    can decide whether to resume live execution.
    """

    om = OrderManager.recover(store=oms_store, initial_cash=initial_cash)
    local_positions = _normalize_positions(om.ledger.positions, qty_tolerance)
    local_working = sorted(om.list_working_order_ids())

    warm_snapshot = _warm_runtime_snapshot(runtime_event_log_path, initial_cash)
    recovery_mode = 'warm' if warm_snapshot is not None else 'cold'

    snapshot = service.reconcile(symbol)
    service_runtime_snapshot_positions = snapshot.get('runtime_snapshot', {}).get('positions', {})
    if isinstance(service_runtime_snapshot_positions, dict) and service_runtime_snapshot_positions:
        exchange_positions = _normalize_runtime_snapshot_positions(service_runtime_snapshot_positions, qty_tolerance)
    else:
        exchange_positions = _normalize_runtime_position_rows(snapshot.get('runtime_positions') or snapshot.get('positions', []), qty_tolerance)

    if warm_snapshot is not None:
        runtime_positions = warm_snapshot.get('positions', {})
    elif isinstance(service_runtime_snapshot_positions, dict) and service_runtime_snapshot_positions:
        runtime_positions = service_runtime_snapshot_positions
    else:
        runtime_positions = {'rows': snapshot.get('runtime_positions') or snapshot.get('positions', [])}

    position_diffs: dict[str, float] = {}
    for sym in sorted(set(local_positions) | set(exchange_positions)):
        diff = local_positions.get(sym, 0.0) - exchange_positions.get(sym, 0.0)
        if abs(diff) > qty_tolerance:
            position_diffs[sym] = diff

    exchange_open_order_ids = sorted({
        oid
        for oid in (_extract_order_id(od) for od in snapshot.get('open_orders', []))
        if oid
    })

    local_set = set(local_working)
    exchange_set = set(exchange_open_order_ids)
    missing_on_exchange = sorted(local_set - exchange_set)
    unmanaged_on_exchange = sorted(exchange_set - local_set)

    notes: list[str] = []
    if runtime_event_log_path and warm_snapshot is None:
        notes.append('cold_recovery_degraded')
    if position_diffs:
        notes.append('position_mismatch_detected')
    if missing_on_exchange:
        notes.append('local_working_orders_missing_on_exchange')
    if unmanaged_on_exchange:
        notes.append('exchange_open_orders_not_tracked_locally')

    resume_mode = _derive_resume_mode(
        recovery_mode=recovery_mode,
        position_diffs=position_diffs,
        missing_on_exchange=missing_on_exchange,
        unmanaged_on_exchange=unmanaged_on_exchange,
    )

    health = RuntimeHealthState()
    health.mark_replay_persistence(warm_snapshot is not None)
    health.mark_recovery_mode(recovery_mode)
    health.mark_resume_mode(resume_mode)
    if position_diffs:
        health.mark_reconcile({'ok': False, 'severity': 'block'})
    runtime_status = health.snapshot()
    promotion_policy = _build_promotion_policy(
        recovery_mode=recovery_mode,
        resume_mode=resume_mode,
        runtime_status=runtime_status,
    )

    report = BootstrapTakeoverReport(
        ok=(len(notes) == 0),
        recovery_mode=recovery_mode,
        resume_mode=resume_mode,
        runtime_status=runtime_status,
        recovered_orders=len(om.list_orders()),
        recovered_working_orders=len(local_working),
        local_positions=local_positions,
        runtime_positions=runtime_positions,
        exchange_positions=exchange_positions,
        position_diffs=position_diffs,
        local_working_order_ids=local_working,
        exchange_open_order_ids=exchange_open_order_ids,
        missing_on_exchange=missing_on_exchange,
        unmanaged_on_exchange=unmanaged_on_exchange,
        notes=notes,
        promotion_policy=promotion_policy,
    )
    return asdict(report)
