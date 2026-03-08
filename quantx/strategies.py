from __future__ import annotations

import inspect
from statistics import mean, pstdev
import math
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


def adx(candles: list[Candle], i: int, period: int = 14) -> float | None:
    if i < period + 1:
        return None
    trs, pdms, ndms = [], [], []
    for k in range(i - period + 1, i + 1):
        cur = candles[k]
        prev = candles[k - 1]
        up_move = cur.high - prev.high
        down_move = prev.low - cur.low
        pdm = up_move if (up_move > down_move and up_move > 0) else 0.0
        ndm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        trs.append(tr)
        pdms.append(pdm)
        ndms.append(ndm)
    atr = mean(trs) if trs else 0.0
    if atr <= 1e-12:
        return 0.0
    pdi = 100.0 * (mean(pdms) / atr)
    ndi = 100.0 * (mean(ndms) / atr)
    denom = pdi + ndi
    if denom <= 1e-12:
        return 0.0
    dx = 100.0 * abs(pdi - ndi) / denom
    return dx


def realized_vol(closes: list[float], window: int = 20) -> float | None:
    if len(closes) < window + 1:
        return None
    rets = []
    seg = closes[-(window + 1) :]
    for a, b in zip(seg[:-1], seg[1:]):
        if a <= 0:
            continue
        rets.append(math.log(b / a))
    if len(rets) < 2:
        return None
    mu = mean(rets)
    var = mean([(r - mu) ** 2 for r in rets])
    return math.sqrt(max(var, 0.0))


def _atr(candles: list[Candle], i: int, period: int = 14) -> float | None:
    if i < period:
        return None
    trs = []
    for k in range(i - period + 1, i + 1):
        prev_close = candles[k - 1].close if k > 0 else candles[k].close
        tr = max(
            candles[k].high - candles[k].low,
            abs(candles[k].high - prev_close),
            abs(candles[k].low - prev_close),
        )
        trs.append(tr)
    return mean(trs) if trs else None


def pass_common_filters(candles: list[Candle], i: int, params: dict[str, Any]) -> bool:
    adx_gate = float(params.get("adx_filter", 0) or 0)
    if adx_gate > 0:
        av = adx(candles, i, int(params.get("adx_period", 14) or 14))
        if av is None or av < adx_gate:
            return False

    closes = [c.close for c in candles[: i + 1]]
    vol = realized_vol(closes, int(params.get("vol_window", 20) or 20))
    min_vol = float(params.get("min_vol", 0) or 0)
    max_vol = float(params.get("max_vol", 0) or 0)
    if vol is not None:
        if min_vol > 0 and vol < min_vol:
            return False
        if max_vol > 0 and vol > max_vol:
            return False

    # volatility expansion filter: ATR > ATR_MA
    cur_atr = None
    if bool(params.get("atr_expansion", False)):
        atr_p = int(params.get("atr_period", 14) or 14)
        atr_ma_p = int(params.get("atr_ma_period", 50) or 50)
        cur_atr = _atr(candles, i, atr_p)
        if cur_atr is None:
            return False
        atr_hist = []
        for k in range(i - atr_ma_p + 1, i + 1):
            if k < 0:
                continue
            v = _atr(candles, k, atr_p)
            if v is not None:
                atr_hist.append(v)
        if len(atr_hist) < max(5, atr_ma_p // 2):
            return False
        atr_ma = mean(atr_hist)
        if cur_atr <= atr_ma:
            return False

    # ATR/price threshold (volatility breakout strength)
    atr_price_threshold = float(params.get("atr_price_threshold", 0) or 0)
    if atr_price_threshold > 0:
        if cur_atr is None:
            atr_p = int(params.get("atr_period", 14) or 14)
            cur_atr = _atr(candles, i, atr_p)
        if cur_atr is None or candles[i].close <= 0:
            return False
        if (cur_atr / candles[i].close) <= atr_price_threshold:
            return False

    return True


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
        sig = 1 if f > s else -1
        if not pass_common_filters(candles, i, self.params):
            return 0
        return sig


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
        sig = 1 if macd_series[-1] > signal_line else -1
        if not pass_common_filters(candles, i, self.params):
            return 0
        return sig


class BreakoutStrategy(BaseStrategy):
    name = "cta_strategy"
    category = "trend"
    author = "quantx"
    description = "CTA趋势策略（原Breakout/Donchian）"
    default_params = {
        "lookback": 200,
        "donchian_exit_lookback": 50,
        "adx_filter": 20,
        "short_adx_filter": 25,
        "adx_period": 14,
        "atr_expansion": True,
        "atr_period": 14,
        "atr_ma_period": 50,
        "atr_price_threshold": 0.004,
        "atr_floor_mult": 0.8,
        "min_vol": 0.0015,
        "max_vol": 0.03,
        "vol_window": 20,
        "short_ma_period": 200,
        "short_require_price_below_ma": True,
        "risk_per_trade": 0.005,
        "stop_atr_mult": 2.0,
        "trail_atr_mult": 1.8,
        "max_hold_bars": 18,
        "max_position_pct": 0.0,
    }
    tags = ["breakout", "trend", "cta"]

    def signal(self, candles: list[Candle], i: int) -> int:
        lookback = int(self.params.get("lookback", self.default_params["lookback"]))
        if i < lookback:
            return 0
        chunk = candles[i - lookback : i]
        hi = max(c.high for c in chunk)
        lo = min(c.low for c in chunk)
        px = candles[i].close
        sig = 0
        if px > hi:
            sig = 1
        elif px < lo:
            sig = -1
        if sig == 0:
            return 0
        if not pass_common_filters(candles, i, self.params):
            return 0
        return sig


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
        sig = 0
        if rv < oversold:
            sig = 1
        elif rv > overbought:
            sig = -1
        if sig == 0:
            return 0
        if not pass_common_filters(candles, i, self.params):
            return 0
        return sig


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
        sig = 0
        if c < dn:
            sig = 1
        elif c > up:
            sig = -1
        if sig == 0:
            return 0
        if not pass_common_filters(candles, i, self.params):
            return 0
        return sig


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


class TsmomStrategy(BaseStrategy):
    name = "tsmom"
    category = "trend"
    author = "quantx"
    description = "Time-Series Momentum（时序动量）"
    default_params = {"momentum_window": 120, "threshold": 0.05}
    tags = ["momentum", "trend", "tsmom"]

    def signal(self, candles: list[Candle], i: int) -> int:
        w = int(self.params.get("momentum_window", 120))
        th = float(self.params.get("threshold", 0.05))
        if i < w:
            return 0
        now_px = candles[i].close
        prev_px = candles[i - w].close
        if prev_px <= 0:
            return 0
        mom = now_px / prev_px - 1.0

        sig = 0
        if mom > th:
            sig = 1
        elif mom < -th:
            sig = -1

        if sig == 0:
            return 0
        if not pass_common_filters(candles, i, self.params):
            return 0
        return sig


class BreakoutMomentumOverlayStrategy(BaseStrategy):
    name = "breakout_momo"
    category = "trend"
    author = "quantx"
    description = "Breakout + Momentum Overlay"
    default_params = {"lookback": 200, "momentum_window": 120, "momentum_threshold": 0.0}
    tags = ["breakout", "momentum", "trend"]

    def signal(self, candles: list[Candle], i: int) -> int:
        lookback = int(self.params.get("lookback", 200))
        mwin = int(self.params.get("momentum_window", 120))
        mth = float(self.params.get("momentum_threshold", 0.0))
        if i < max(lookback, mwin):
            return 0

        chunk = candles[i - lookback : i]
        hi = max(c.high for c in chunk)
        lo = min(c.low for c in chunk)
        px = candles[i].close

        prev_px = candles[i - mwin].close
        if prev_px <= 0:
            return 0
        mom = px / prev_px - 1.0

        long_sig = px > hi and mom > mth
        short_sig = px < lo and mom < -mth

        if not long_sig and not short_sig:
            return 0
        if not pass_common_filters(candles, i, self.params):
            return 0
        return 1 if long_sig else -1


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
    TsmomStrategy,
    BreakoutMomentumOverlayStrategy,
]:
    register_strategy_class(_builtin)

# backward compatibility alias
STRATEGY_REGISTRY["breakout"] = BreakoutStrategy
