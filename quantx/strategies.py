from __future__ import annotations

import inspect
from statistics import mean, pstdev
from typing import Any

from .models import Candle
from .repro import stable_hash


class BaseStrategy:
    name = "base"
    version = "1.0.0"
    category = "custom"
    author = "unknown"
    description = ""
    default_params: dict[str, Any] = {}
    tags: list[str] = []
    risk_profile = "medium"

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    def signal(self, candles: list[Candle], i: int) -> int:
        raise NotImplementedError

    @classmethod
    def profile(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "version": cls.version,
            "category": cls.category,
            "author": cls.author,
            "description": cls.description,
            "default_params": cls.default_params,
            "tags": cls.tags,
            "risk_profile": cls.risk_profile,
            "module": cls.__module__,
        }

    @classmethod
    def source_hash(cls) -> str:
        try:
            src = inspect.getsource(cls)
            return stable_hash(src)
        except (OSError, TypeError):
            return "unknown"


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return mean(values[-period:])


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = values[-period]
    for v in values[-period + 1 :]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for idx in range(-period, 0):
        diff = values[idx] - values[idx - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class DcaStrategy(BaseStrategy):
    name = "dca"
    category = "passive"
    author = "quantx"
    description = "固定间隔定投"
    default_params = {"buy_interval": 24, "buy_amount_usdt": 100}
    tags = ["dca", "passive"]
    risk_profile = "low"

    def signal(self, candles: list[Candle], i: int) -> int:
        interval = int(self.params.get("buy_interval", 24))
        return 1 if i % max(interval, 1) == 0 else 0


class MaCrossoverStrategy(BaseStrategy):
    name = "ma_crossover"
    category = "trend"
    author = "quantx"
    description = "均线交叉策略"
    default_params = {"fast_period": 10, "slow_period": 30, "ma_type": "sma"}
    tags = ["ma", "trend"]

    def signal(self, candles: list[Candle], i: int) -> int:
        fast_p = int(self.params.get("fast_period", 10))
        slow_p = int(self.params.get("slow_period", 30))
        mtype = self.params.get("ma_type", "sma")
        closes = [c.close for c in candles[: i + 1]]
        fn = ema if mtype == "ema" else sma
        f = fn(closes, fast_p)
        s = fn(closes, slow_p)
        if f is None or s is None:
            return 0
        return 1 if f > s else -1


class MacdStrategy(BaseStrategy):
    name = "macd"
    category = "trend"
    author = "quantx"
    description = "MACD金叉/死叉"
    default_params = {"fast_period": 12, "slow_period": 26, "signal_period": 9}
    tags = ["macd", "trend"]

    def signal(self, candles: list[Candle], i: int) -> int:
        fast = int(self.params.get("fast_period", 12))
        slow = int(self.params.get("slow_period", 26))
        sig = int(self.params.get("signal_period", 9))
        closes = [c.close for c in candles[: i + 1]]
        if len(closes) < slow + sig:
            return 0
        macd_series = []
        for j in range(slow, len(closes) + 1):
            part = closes[:j]
            ef = ema(part, fast)
            es = ema(part, slow)
            if ef is None or es is None:
                continue
            macd_series.append(ef - es)
        if len(macd_series) < sig:
            return 0
        signal_line = ema(macd_series, sig)
        if signal_line is None:
            return 0
        return 1 if macd_series[-1] > signal_line else -1


class BreakoutStrategy(BaseStrategy):
    name = "breakout"
    category = "trend"
    author = "quantx"
    description = "唐奇安通道突破"
    default_params = {"lookback": 20}
    tags = ["breakout", "trend"]

    def signal(self, candles: list[Candle], i: int) -> int:
        lookback = int(self.params.get("lookback", 20))
        if i < lookback:
            return 0
        chunk = candles[i - lookback : i]
        hi = max(c.high for c in chunk)
        lo = min(c.low for c in chunk)
        px = candles[i].close
        if px > hi:
            return 1
        if px < lo:
            return -1
        return 0


class RsiReversalStrategy(BaseStrategy):
    name = "rsi_reversal"
    category = "mean_reversion"
    author = "quantx"
    description = "RSI反转"
    default_params = {"rsi_period": 14, "oversold": 30, "overbought": 70}
    tags = ["rsi", "mean-reversion"]

    def signal(self, candles: list[Candle], i: int) -> int:
        period = int(self.params.get("rsi_period", 14))
        oversold = float(self.params.get("oversold", 30))
        overbought = float(self.params.get("overbought", 70))
        closes = [c.close for c in candles[: i + 1]]
        rv = rsi(closes, period)
        if rv is None:
            return 0
        if rv < oversold:
            return 1
        if rv > overbought:
            return -1
        return 0


class BollingerBandsStrategy(BaseStrategy):
    name = "bollinger_bands"
    category = "mean_reversion"
    author = "quantx"
    description = "布林带突破/回归"
    default_params = {"bb_period": 20, "bb_std": 2.0}
    tags = ["bollinger", "mean-reversion"]

    def signal(self, candles: list[Candle], i: int) -> int:
        p = int(self.params.get("bb_period", 20))
        std = float(self.params.get("bb_std", 2.0))
        closes = [c.close for c in candles[: i + 1]]
        if len(closes) < p:
            return 0
        m = mean(closes[-p:])
        sd = pstdev(closes[-p:])
        up = m + std * sd
        dn = m - std * sd
        c = closes[-1]
        if c < dn:
            return 1
        if c > up:
            return -1
        return 0


class GridStrategy(BaseStrategy):
    name = "grid"
    category = "mean_reversion"
    author = "quantx"
    description = "网格交易"
    default_params = {"grid_count": 10, "grid_spacing_pct": 0.01}
    tags = ["grid", "mean-reversion"]

    def signal(self, candles: list[Candle], i: int) -> int:
        count = int(self.params.get("grid_count", 10))
        spacing = float(self.params.get("grid_spacing_pct", 0.01))
        if i < count:
            return 0
        anchor = candles[i - count].close
        if anchor <= 0:
            return 0
        diff = (candles[i].close - anchor) / anchor
        if diff <= -spacing:
            return 1
        if diff >= spacing:
            return -1
        return 0


STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy_class(strategy_cls: type[BaseStrategy]) -> None:
    if not issubclass(strategy_cls, BaseStrategy):
        raise TypeError("strategy must inherit BaseStrategy")
    if not strategy_cls.name or strategy_cls.name == "base":
        raise ValueError("strategy must define a non-empty name")
    STRATEGY_REGISTRY[strategy_cls.name] = strategy_cls


def get_strategy_class(name: str) -> type[BaseStrategy]:
    if name not in STRATEGY_REGISTRY:
        raise KeyError(f"unknown strategy: {name}")
    return STRATEGY_REGISTRY[name]


def list_strategies() -> list[dict[str, Any]]:
    return [cls.profile() for _, cls in sorted(STRATEGY_REGISTRY.items(), key=lambda x: x[0])]


for _builtin in [
    DcaStrategy,
    MaCrossoverStrategy,
    MacdStrategy,
    BreakoutStrategy,
    RsiReversalStrategy,
    BollingerBandsStrategy,
    GridStrategy,
]:
    register_strategy_class(_builtin)
