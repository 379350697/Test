"""Pre-trade and portfolio risk checks (P1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from typing import Any


@dataclass(slots=True)
class RiskLimits:
    max_symbol_weight: float = 0.35
    max_gross_leverage: float = 2.0
    max_order_notional: float = 20_000.0


@dataclass(slots=True)
class CircuitBreakerLimits:
    max_daily_loss: float = 1_000.0
    max_orders_per_day: int = 500


@dataclass(slots=True)
class CircuitBreakerState:
    day: str
    realized_pnl: float = 0.0
    order_count: int = 0


class RiskCircuitBreaker:
    """Stateful intraday risk guard for daily loss and order throttling."""

    def __init__(self, limits: CircuitBreakerLimits | None = None, now: datetime | None = None):
        ts = now or datetime.now(tz=timezone.utc)
        self.limits = limits or CircuitBreakerLimits()
        self.state = CircuitBreakerState(day=ts.date().isoformat())

    def register_fill(self, realized_pnl: float, now: datetime | None = None) -> None:
        self._roll_day(now)
        self.state.realized_pnl += realized_pnl

    def register_order(self, now: datetime | None = None) -> None:
        self._roll_day(now)
        self.state.order_count += 1

    def check(self, now: datetime | None = None) -> tuple[bool, str]:
        self._roll_day(now)
        if -self.state.realized_pnl > self.limits.max_daily_loss + 1e-12:
            return False, "daily_loss_exceeded"
        if self.state.order_count > self.limits.max_orders_per_day:
            return False, "max_orders_per_day_exceeded"
        return True, "ok"

    def _roll_day(self, now: datetime | None = None) -> None:
        ts = now or datetime.now(tz=timezone.utc)
        day = ts.date().isoformat()
        if day != self.state.day:
            self.state = CircuitBreakerState(day=day)


def pretrade_check(
    target_weights: dict[str, float],
    order_notional: float,
    limits: RiskLimits | None = None,
) -> tuple[bool, str]:
    """Check position concentration, leverage and single-order notional."""

    lim = limits or RiskLimits()
    gross = sum(abs(v) for v in target_weights.values())
    if gross > lim.max_gross_leverage + 1e-12:
        return False, "gross_leverage_exceeded"
    if any(abs(v) > lim.max_symbol_weight + 1e-12 for v in target_weights.values()):
        return False, "symbol_weight_exceeded"
    if order_notional > lim.max_order_notional + 1e-12:
        return False, "order_notional_exceeded"
    return True, "ok"


def check_account_notional(exposure_notionals: dict[str, float], max_abs_notional: float) -> tuple[bool, str]:
    gross = sum(abs(v) for v in exposure_notionals.values())
    if gross > max_abs_notional + 1e-12:
        return False, "account_notional_exceeded"
    return True, "ok"


def portfolio_var_gaussian(weights: list[float], cov: list[list[float]], z_score: float = 2.33) -> float:
    """Compute one-period Gaussian VaR approximation."""

    if not weights or not cov:
        return 0.0
    quad = 0.0
    for i in range(len(weights)):
        for j in range(len(weights)):
            quad += weights[i] * cov[i][j] * weights[j]
    vol = sqrt(max(quad, 0.0))
    return z_score * vol


def exposure_by_symbol(positions: dict[str, float], prices: dict[str, float], equity: float) -> dict[str, Any]:
    """Return symbol notionals and normalized exposure weights."""

    notionals = {s: positions.get(s, 0.0) * prices.get(s, 0.0) for s in set(positions) | set(prices)}
    if equity <= 0:
        return {"equity": equity, "notionals": notionals, "weights": {k: 0.0 for k in notionals}}
    weights = {k: v / equity for k, v in notionals.items()}
    return {"equity": equity, "notionals": notionals, "weights": weights}
