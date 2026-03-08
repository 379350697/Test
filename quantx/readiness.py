"""Operational readiness checks for live rollout gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alerts import AlertRouter
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
        "rollout_allowed_symbols",
        ctx.live_config.allowed_symbols is not None and len(ctx.live_config.allowed_symbols) > 0,
        "live_config.allowed_symbols must be configured for staged rollout",
    )
    _append_check(
        checks,
        "rollout_max_orders_per_cycle",
        ctx.live_config.max_orders_per_cycle is not None and ctx.live_config.max_orders_per_cycle > 0,
        "live_config.max_orders_per_cycle must be set",
    )
    _append_check(
        checks,
        "rollout_max_notional_per_cycle",
        ctx.live_config.max_notional_per_cycle is not None and ctx.live_config.max_notional_per_cycle > 0,
        "live_config.max_notional_per_cycle must be set",
    )
    _append_check(
        checks,
        "risk_max_symbol_weight",
        0 < ctx.risk_limits.max_symbol_weight <= 1,
        "risk_limits.max_symbol_weight should be in (0, 1]",
    )
    _append_check(
        checks,
        "risk_max_order_notional",
        ctx.risk_limits.max_order_notional > 0,
        "risk_limits.max_order_notional must be positive",
    )
    _append_check(
        checks,
        "alert_channel_registered",
        len(ctx.alert_router.channels) > 0,
        "at least one alert webhook channel should be registered",
    )

    has_oms_store = ctx.oms_store is not None
    _append_check(
        checks,
        "oms_persistence_enabled",
        has_oms_store,
        "JsonlOMSStore should be configured for crash recovery",
    )
    if has_oms_store:
        assert ctx.oms_store is not None
        _append_check(
            checks,
            "oms_store_path_exists",
            ctx.oms_store.path.parent.exists(),
            "OMS store directory should exist",
        )

    passed = sum(1 for c in checks if c["ok"])
    score = int(round((passed / len(checks)) * 100)) if checks else 0
    ok = all(c["ok"] for c in checks)
    return ReadinessReport(ok=ok, score=score, checks=checks)


def _append_check(checks: list[dict[str, Any]], name: str, cond: bool, advice: str) -> None:
    checks.append({"name": name, "ok": cond, "advice": "" if cond else advice})
