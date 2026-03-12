from __future__ import annotations

from typing import Any


def build_reconcile_report(
    runtime_snapshot: dict[str, Any],
    *,
    qty_tolerance: float = 1e-9,
) -> dict[str, Any]:
    runtime_positions = runtime_snapshot.get('positions', {})
    observed_exchange = runtime_snapshot.get('observed_exchange', {})
    exchange_positions = observed_exchange.get('positions', {}) if isinstance(observed_exchange, dict) else {}
    runtime_ledger = runtime_snapshot.get('ledger', {})
    exchange_account = observed_exchange.get('account', {}) if isinstance(observed_exchange, dict) else {}

    position_mismatches: dict[str, dict[str, Any]] = {}
    for symbol in sorted(set(runtime_positions) | set(exchange_positions)):
        runtime_legs = runtime_positions.get(symbol, {}) if isinstance(runtime_positions, dict) else {}
        exchange_legs = exchange_positions.get(symbol, {}) if isinstance(exchange_positions, dict) else {}
        sides = sorted(set(runtime_legs) | set(exchange_legs))
        for position_side in sides:
            runtime_leg = runtime_legs.get(position_side, {}) if isinstance(runtime_legs, dict) else {}
            exchange_leg = exchange_legs.get(position_side, {}) if isinstance(exchange_legs, dict) else {}
            runtime_qty = float(runtime_leg.get('qty', 0.0) or 0.0)
            exchange_qty = float(exchange_leg.get('qty', 0.0) or 0.0)
            runtime_avg = float(runtime_leg.get('avg_entry_price', 0.0) or 0.0)
            exchange_avg = float(exchange_leg.get('avg_entry_price', 0.0) or 0.0)
            if abs(runtime_qty - exchange_qty) <= qty_tolerance and abs(runtime_avg - exchange_avg) <= qty_tolerance:
                continue
            key = symbol if len(sides) == 1 else f'{symbol}:{position_side}'
            position_mismatches[key] = {
                'symbol': symbol,
                'position_side': position_side,
                'runtime_qty': runtime_qty,
                'exchange_qty': exchange_qty,
                'runtime_avg_entry_price': runtime_avg,
                'exchange_avg_entry_price': exchange_avg,
            }

    account_mismatches: dict[str, dict[str, float]] = {}
    for field in sorted(set(runtime_ledger) & set(exchange_account)):
        runtime_value = runtime_ledger.get(field)
        exchange_value = exchange_account.get(field)
        if runtime_value is None or exchange_value is None:
            continue
        runtime_float = float(runtime_value)
        exchange_float = float(exchange_value)
        if abs(runtime_float - exchange_float) <= qty_tolerance:
            continue
        account_mismatches[field] = {
            'runtime_value': runtime_float,
            'exchange_value': exchange_float,
            'delta': exchange_float - runtime_float,
        }

    severity = 'block' if position_mismatches else 'warn' if account_mismatches else 'ok'
    return {
        'ok': not position_mismatches and not account_mismatches,
        'position_mismatches': position_mismatches,
        'account_mismatches': account_mismatches,
        'severity': severity,
    }
