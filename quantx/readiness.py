"""Operational readiness checks for live rollout gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alerts import AlertRouter
from .error_codes import QX_READY_BLOCKED, with_code
from .live_service import LiveExecutionConfig
from .oms import JsonlOMSStore
from .risk_engine import RiskLimits

LIVE_PROMOTION_STAGES = {'live_ready', 'live'}
LIVE_PROMOTION_CHECKS = (
    'backtest_quality',
    'paper_soak_duration',
    'paper_alerts',
    'runtime_truth',
    'resume_mode',
)
LIVE_BOOTSTRAP_RESUME_MODES = {'reduce_only', 'live'}


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
    runtime_status: dict[str, Any] | None = None
    promotion_gates: dict[str, Any] | None = None


def evaluate_readiness(ctx: ReadinessContext) -> ReadinessReport:
    checks: list[dict[str, Any]] = []
    runtime_status = ctx.runtime_status or {}
    stream_status = runtime_status.get('stream', {}) if isinstance(runtime_status.get('stream'), dict) else {}
    execution_mode = str(runtime_status.get('execution_mode', 'live'))
    promotion_stage_ok = _promotion_stage_ready(ctx.promotion_gates or {})
    bootstrap_resume_mode_ok = _bootstrap_resume_mode_ready(runtime_status)
    requires_live_promotion_contract = _live_promotion_contract_required(ctx)

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
    _append_check(
        checks,
        'replay_closure_ready',
        ctx.live_config.runtime_mode == 'derivatives',
        'replay closure requires the shared runtime path',
    )
    _append_check(
        checks,
        'paper_closure_ready',
        _paper_closure_ready(ctx),
        'paper closure requires rollout symbols and cycle limits to be configured',
    )

    if requires_live_promotion_contract or ctx.promotion_gates is not None:
        _append_check(
            checks,
            'promotion_stage_gate',
            promotion_stage_ok,
            'shared promotion gates must confirm backtest quality, paper soak, and runtime truth before live capital is enabled',
        )
    if requires_live_promotion_contract or ctx.promotion_gates is not None or 'resume_mode' in runtime_status:
        _append_check(
            checks,
            'bootstrap_resume_mode_gate',
            bootstrap_resume_mode_ok,
            'bootstrap recovery must resume in reduce_only or live mode before enabling live capital',
        )

    _append_check(
        checks,
        'live_truth_replay_persistence',
        bool(runtime_status.get('replay_persistence')),
        'runtime truth replay persistence must be available before live rollout',
    )
    _append_check(
        checks,
        'live_truth_not_degraded',
        not bool(runtime_status.get('degraded')),
        'runtime truth must not be in degraded recovery mode',
    )
    _append_check(
        checks,
        'live_truth_reconcile_ok',
        bool(runtime_status.get('reconcile_ok', True)),
        'runtime truth reconciliation must be healthy before live rollout',
    )
    _append_check(
        checks,
        'live_truth_stream_fresh',
        not bool(stream_status.get('stale')),
        'runtime truth private stream must be fresh before live rollout',
    )
    _append_check(
        checks,
        'live_truth_execution_mode_allowed',
        execution_mode in {'live', 'reduce_only'},
        'runtime truth execution mode must allow new live risk',
    )
    _append_check(
        checks,
        'micro_live_ready',
        (not ctx.live_config.dry_run)
        and _paper_closure_ready(ctx)
        and len(ctx.alert_router.channels) > 0
        and ctx.oms_store is not None
        and bool(runtime_status.get('replay_persistence'))
        and (not bool(runtime_status.get('degraded')))
        and bool(runtime_status.get('reconcile_ok', True))
        and (not bool(stream_status.get('stale')))
        and execution_mode in {'live', 'reduce_only'}
        and promotion_stage_ok
        and bootstrap_resume_mode_ok,
        'micro-live requires promotion gates, bootstrap resume approval, alerts, OMS persistence, and healthy runtime truth state',
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


def _paper_closure_ready(ctx: ReadinessContext) -> bool:
    return (
        ctx.live_config.runtime_mode == 'derivatives'
        and ctx.live_config.allowed_symbols is not None
        and len(ctx.live_config.allowed_symbols) > 0
        and ctx.live_config.max_orders_per_cycle is not None
        and ctx.live_config.max_orders_per_cycle > 0
        and ctx.live_config.max_notional_per_cycle is not None
        and ctx.live_config.max_notional_per_cycle > 0
    )


def _live_promotion_contract_required(ctx: ReadinessContext) -> bool:
    return not ctx.live_config.dry_run


def _promotion_stage_ready(promotion_gates: dict[str, Any]) -> bool:
    if not promotion_gates:
        return False
    eligible_stage = str(promotion_gates.get('eligible_stage', 'backtest_only'))
    failed_gates = promotion_gates.get('failed_gates', [])
    return (
        eligible_stage in LIVE_PROMOTION_STAGES
        and len(failed_gates) == 0
        and all(_promotion_gate_check_ok(promotion_gates, check_name) for check_name in LIVE_PROMOTION_CHECKS)
    )


def _promotion_gate_check_ok(promotion_gates: dict[str, Any], check_name: str) -> bool:
    checks = promotion_gates.get('checks', {}) if isinstance(promotion_gates.get('checks'), dict) else {}
    details = checks.get(check_name, {}) if isinstance(checks, dict) else {}
    return bool(details.get('ok', False)) if isinstance(details, dict) else False


def _bootstrap_resume_mode_ready(runtime_status: dict[str, Any]) -> bool:
    return str(runtime_status.get('resume_mode', 'blocked')) in LIVE_BOOTSTRAP_RESUME_MODES


def rollout_stage(ctx: ReadinessContext) -> str:
    if ctx.live_config.dry_run:
        return 'paper_closure'
    if ctx.live_config.max_notional_per_cycle is not None and ctx.live_config.max_notional_per_cycle <= 1000.0:
        return 'micro_live'
    return 'normal_live'


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
