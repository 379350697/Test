from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class Trade:
    ts: datetime
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    reason: str


@dataclass(slots=True)
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    last_trade_ts: datetime | None = None


@dataclass(slots=True)
class RiskConfig:
    max_position_pct: float = 0.3
    max_drawdown_pct: float = 0.25
    cooldown_bars: int = 0
    max_orders_per_day: int = 30


@dataclass(slots=True)
class BacktestConfig:
    symbol: str
    timeframe: str
    initial_cash: float = 10_000.0
    fee_rate: float = 0.001
    slippage_pct: float = 0.0005
    risk: RiskConfig = field(default_factory=RiskConfig)


@dataclass(slots=True)
class RunMetadata:
    strategy_name: str
    strategy_version: str
    strategy_spec_hash: str
    strategy_source_hash: str
    param_hash: str
    data_hash: str
    python_version: str
    created_at: str


@dataclass(slots=True)
class BacktestResult:
    config: BacktestConfig
    metadata: RunMetadata
    equity_curve: list[tuple[datetime, float]]
    drawdown_curve: list[tuple[datetime, float]]
    trades: list[Trade]
    metrics: dict[str, float]
    score_breakdown: dict[str, float]
    score_total: float
    extra: dict[str, Any] = field(default_factory=dict)
