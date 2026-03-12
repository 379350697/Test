"""Shared promotion gates for backtest, paper soak, and live rollout."""

from __future__ import annotations

from typing import Any, Mapping

BACKTEST_MAX_DRAWDOWN_PCT = 20.0
PAPER_MIN_CONTINUOUS_HOURS = 24.0
LIVE_ALLOWED_RESUME_MODES = {"paper", "reduce_only", "live"}


def evaluate_release_gates(*, backtest: dict[str, object], paper: dict[str, object], live: dict[str, object]) -> dict[str, object]:
    backtest_payload = _as_mapping(backtest)
    paper_payload = _as_mapping(paper)
    live_payload = _as_mapping(live)

    checks = {
        "backtest_quality": {
            "stage": "backtest",
            "ok": _backtest_quality_ok(backtest_payload),
            "value": {
                "ok": bool(backtest_payload.get("ok", False)),
                "max_drawdown_pct": _as_float(backtest_payload, "max_drawdown_pct"),
            },
            "threshold": {"max_drawdown_pct_lte": BACKTEST_MAX_DRAWDOWN_PCT},
        },
        "paper_soak_duration": {
            "stage": "paper",
            "ok": _paper_soak_duration_ok(paper_payload),
            "value": {"continuous_hours": _as_float(paper_payload, "continuous_hours")},
            "threshold": {"continuous_hours_gte": PAPER_MIN_CONTINUOUS_HOURS},
        },
        "paper_alerts": {
            "stage": "paper",
            "ok": _paper_alerts_ok(paper_payload),
            "value": {"alerts_ok": bool(paper_payload.get("alerts_ok", False))},
            "threshold": {"alerts_ok": True},
        },
        "runtime_truth": {
            "stage": "live",
            "ok": bool(live_payload.get("runtime_truth_ok", False)),
            "value": {"runtime_truth_ok": bool(live_payload.get("runtime_truth_ok", False))},
            "threshold": {"runtime_truth_ok": True},
        },
        "resume_mode": {
            "stage": "live",
            "ok": _resume_mode_ok(live_payload),
            "value": {"resume_mode": _as_text(live_payload, "resume_mode", default="blocked")},
            "threshold": {"allowed_values": sorted(LIVE_ALLOWED_RESUME_MODES)},
        },
    }
    failed_gates = [name for name, details in checks.items() if not details["ok"]]
    eligible_stage = _eligible_stage(checks, failed_gates)
    return {
        "eligible_stage": eligible_stage,
        "failed_gates": failed_gates,
        "checks": checks,
        "recommended_next_step": _recommended_next_step(eligible_stage, failed_gates),
    }


def _as_mapping(payload: Mapping[str, object] | None) -> Mapping[str, object]:
    return payload or {}


def _as_float(payload: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = payload.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_text(payload: Mapping[str, object], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    return str(value) if value is not None else default


def _backtest_quality_ok(backtest: Mapping[str, object]) -> bool:
    return bool(backtest.get("ok", False)) and _as_float(backtest, "max_drawdown_pct") <= BACKTEST_MAX_DRAWDOWN_PCT


def _paper_soak_duration_ok(paper: Mapping[str, object]) -> bool:
    return bool(paper.get("ok", False)) and _as_float(paper, "continuous_hours") >= PAPER_MIN_CONTINUOUS_HOURS


def _paper_alerts_ok(paper: Mapping[str, object]) -> bool:
    return bool(paper.get("ok", False)) and bool(paper.get("alerts_ok", False))


def _resume_mode_ok(live: Mapping[str, object]) -> bool:
    return _as_text(live, "resume_mode", default="blocked") in LIVE_ALLOWED_RESUME_MODES


def _eligible_stage(checks: dict[str, dict[str, Any]], failed_gates: list[str]) -> str:
    if any(checks[name]["stage"] == "backtest" for name in failed_gates):
        return "backtest_only"
    if failed_gates:
        return "paper_only"
    return "live_ready"


def _recommended_next_step(eligible_stage: str, failed_gates: list[str]) -> str:
    if eligible_stage == "backtest_only":
        return "improve_backtest_quality"
    if "paper_soak_duration" in failed_gates or "paper_alerts" in failed_gates:
        return "continue_paper_soak"
    if failed_gates:
        return "resolve_live_runtime_blockers"
    return "eligible_for_live_rollout"
