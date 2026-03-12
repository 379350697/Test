"""Operational readiness checks for live rollout gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alerts import AlertRouter
from .error_codes import QX_READY_BLOCKED, with_code
from .live_service import LiveExecutionConfig
from .oms import JsonlOMSStore
from .risk_engine import RiskLimits


@dataclass(slots=True)
class ReadinessReport:
    ok: bool
    score: int
    checks: list[dict[str, Any]]


@dataclass(slots=True)
class ReadinessContext:
    live_config: LiveExecutionConfig
    risk_limits: RiskLimits
    alert_router: AlertRouter
    oms_store: JsonlOMSStore | None = None


def evaluate_readiness(ctx: ReadinessContext) -> ReadinessReport:
    checks: list[dict[str, Any]] = []

    _append_check(
        checks,
        'runtime_execution_path',
        ctx.live_config.runtime_mode == 'derivatives',
        'live_config.runtime_mode must stay on the shared derivatives runtime core',
    )
    _append_check(
        checks,
        'rollout_exchange_supported',
        ctx.live_config.exchange in {'okx', 'binance'},
        'live_config.exchange must be one of okx/binance for perpetual rollout',
    )
    _append_check(
        checks,
        'rollout_exchange_order',
        ctx.live_config.exchange == 'okx' or ctx.live_config.enable_binance,
        'Binance rollout stays gated until OKX-first rollout has been explicitly enabled',
    )
    _append_check(
        checks,
        'rollout_allowed_symbols',
        ctx.live_config.allowed_symbols is not None and len(ctx.live_config.allowed_symbols) > 0,
        'live_config.allowed_symbols must be configured for staged rollout',
    )
    _append_check(
        checks,
        'rollout_max_orders_per_cycle',
        ctx.live_config.max_orders_per_cycle is not None and ctx.live_config.max_orders_per_cycle > 0,
        'live_config.max_orders_per_cycle must be set',
    )
    _append_check(
        checks,
        'rollout_max_notional_per_cycle',
        ctx.live_config.max_notional_per_cycle is not None and ctx.live_config.max_notional_per_cycle > 0,
        'live_config.max_notional_per_cycle must be set',
    )
    _append_check(
        checks,
        'risk_max_symbol_weight',
        0 < ctx.risk_limits.max_symbol_weight <= 1,
        'risk_limits.max_symbol_weight should be in (0, 1]',
    )
    _append_check(
        checks,
        'risk_max_order_notional',
        ctx.risk_limits.max_order_notional > 0,
        'risk_limits.max_order_notional must be positive',
    )
    _append_check(
        checks,
        'alert_channel_registered',
        len(ctx.alert_router.channels) > 0,
        'at least one alert webhook channel should be registered',
    )

    has_oms_store = ctx.oms_store is not None
    _append_check(
        checks,
        'oms_persistence_enabled',
        has_oms_store,
        'JsonlOMSStore should be configured for crash recovery',
    )
    if has_oms_store and ctx.oms_store is not None:
        _append_check(
            checks,
            'oms_store_path_exists',
            ctx.oms_store.path.parent.exists(),
            'OMS store directory should exist',
        )

    passed = sum(1 for check in checks if check['ok'])
    score = int(round((passed / len(checks)) * 100)) if checks else 0
    ok = all(check['ok'] for check in checks)
    return ReadinessReport(ok=ok, score=score, checks=checks)


def _append_check(checks: list[dict[str, Any]], name: str, cond: bool, advice: str) -> None:
    checks.append({'name': name, 'ok': cond, 'advice': '' if cond else advice})


class ReadinessError(RuntimeError):
    """Raised when go-live readiness checks fail."""


def blockers(report: ReadinessReport) -> list[dict[str, Any]]:
    """Return all failed checks for quick operator inspection."""

    return [check for check in report.checks if not check['ok']]


def assert_ready(ctx: ReadinessContext) -> ReadinessReport:
    """Evaluate readiness and raise detailed error when not ready."""

    report = evaluate_readiness(ctx)
    if report.ok:
        return report

    failed = blockers(report)
    names = ', '.join(check['name'] for check in failed)
    raise ReadinessError(with_code(QX_READY_BLOCKED, f'go_live_blocked:{names}'))
