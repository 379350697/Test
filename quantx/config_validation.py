"""Schema validation helpers for strategy YAML configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


class ConfigValidationError(ValueError):
    """Raised when strategy config violates required schema constraints."""


def _type_name(expected_type: type[Any] | tuple[type[Any], ...]) -> str:
    if isinstance(expected_type, tuple):
        return "_or_".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def _require_path(
    obj: dict[str, Any], keys: list[str], expected_type: type[Any] | tuple[type[Any], ...], path: str
) -> Any:
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            raise ConfigValidationError(f"missing_required:{path}")
        cur = cur[key]
    if not isinstance(cur, expected_type):
        raise ConfigValidationError(f"invalid_type:{path}:expected_{_type_name(expected_type)}")
    return cur


def validate_cta_strategy_config(payload: dict[str, Any]) -> dict[str, Any]:
    strategy = _require_path(payload, ["strategy"], str, "strategy")
    timeframe = _require_path(payload, ["timeframe"], str, "timeframe")
    symbols = _require_path(payload, ["universe", "symbols"], list, "universe.symbols")
    lookback = _require_path(payload, ["signal", "lookback"], int, "signal.lookback")
    risk_per_trade = _require_path(payload, ["risk_trade_level", "risk_per_trade"], (int, float), "risk_trade_level.risk_per_trade")
    leverage_clip = _require_path(payload, ["portfolio", "leverage_clip"], list, "portfolio.leverage_clip")

    if strategy != "cta_strategy":
        raise ConfigValidationError("invalid_value:strategy:cta_strategy_required")
    if timeframe not in {"1h", "4h", "1d"}:
        raise ConfigValidationError("invalid_value:timeframe")
    if len(symbols) == 0 or not all(isinstance(x, str) and x for x in symbols):
        raise ConfigValidationError("invalid_value:universe.symbols")
    if lookback < 50:
        raise ConfigValidationError("invalid_value:signal.lookback")
    if not (0 < float(risk_per_trade) <= 0.02):
        raise ConfigValidationError("invalid_value:risk_trade_level.risk_per_trade")
    if len(leverage_clip) != 2:
        raise ConfigValidationError("invalid_value:portfolio.leverage_clip_len")
    lo = float(leverage_clip[0])
    hi = float(leverage_clip[1])
    if not (0.0 <= lo < hi <= 10.0):
        raise ConfigValidationError("invalid_value:portfolio.leverage_clip_range")

    return payload


def load_and_validate_cta_strategy_config(path: str) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigValidationError("invalid_root:dict_required")
    return validate_cta_strategy_config(payload)
