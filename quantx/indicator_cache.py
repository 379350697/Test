from __future__ import annotations

from dataclasses import dataclass, field
from math import log, sqrt
from statistics import mean

from .models import Candle


@dataclass(slots=True)
class IndicatorCache:
    candles: list[Candle]
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]
    _series: dict[tuple[str, int], list[float | None]] = field(default_factory=dict)

    @classmethod
    def from_candles(cls, candles: list[Candle]) -> "IndicatorCache":
        rows = list(candles)
        return cls(
            candles=rows,
            closes=[c.close for c in rows],
            highs=[c.high for c in rows],
            lows=[c.low for c in rows],
            volumes=[c.volume for c in rows],
        )

    def sma(self, period: int) -> list[float | None]:
        return self._series_for(("sma", period), self._build_sma, period)

    def ema(self, period: int) -> list[float | None]:
        return self._series_for(("ema", period), self._build_ema, period)

    def rsi(self, period: int = 14) -> list[float | None]:
        return self._series_for(("rsi", period), self._build_rsi, period)

    def atr(self, period: int = 14) -> list[float | None]:
        return self._series_for(("atr", period), self._build_atr, period)

    def adx(self, period: int = 14) -> list[float | None]:
        return self._series_for(("adx", period), self._build_adx, period)

    def realized_vol(self, window: int = 20) -> list[float | None]:
        return self._series_for(("realized_vol", window), self._build_realized_vol, window)

    def _series_for(self, key: tuple[str, int], builder, period: int) -> list[float | None]:
        if key not in self._series:
            self._series[key] = builder(period)
        return self._series[key]

    def _build_sma(self, period: int) -> list[float | None]:
        series: list[float | None] = [None] * len(self.closes)
        if period <= 0:
            return series
        for i in range(period - 1, len(self.closes)):
            series[i] = mean(self.closes[i - period + 1 : i + 1])
        return series

    def _build_ema(self, period: int) -> list[float | None]:
        series: list[float | None] = [None] * len(self.closes)
        if period <= 0:
            return series
        k = 2 / (period + 1)
        for i in range(period - 1, len(self.closes)):
            window = self.closes[i - period + 1 : i + 1]
            e = window[0]
            for value in window[1:]:
                e = value * k + e * (1 - k)
            series[i] = e
        return series

    def _build_rsi(self, period: int) -> list[float | None]:
        series: list[float | None] = [None] * len(self.closes)
        if period <= 0:
            return series
        for i in range(period, len(self.closes)):
            gains: list[float] = []
            losses: list[float] = []
            for j in range(i - period + 1, i + 1):
                diff = self.closes[j] - self.closes[j - 1]
                gains.append(max(0.0, diff))
                losses.append(max(0.0, -diff))
            avg_gain = mean(gains)
            avg_loss = mean(losses)
            if avg_loss == 0:
                series[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                series[i] = 100 - (100 / (1 + rs))
        return series

    def _build_atr(self, period: int) -> list[float | None]:
        series: list[float | None] = [None] * len(self.candles)
        if period <= 0:
            return series
        for i in range(period, len(self.candles)):
            trs: list[float] = []
            for k in range(i - period + 1, i + 1):
                prev_close = self.closes[k - 1] if k > 0 else self.closes[k]
                tr = max(
                    self.highs[k] - self.lows[k],
                    abs(self.highs[k] - prev_close),
                    abs(self.lows[k] - prev_close),
                )
                trs.append(tr)
            series[i] = mean(trs) if trs else None
        return series

    def _build_adx(self, period: int) -> list[float | None]:
        series: list[float | None] = [None] * len(self.candles)
        if period <= 0:
            return series
        for i in range(period + 1, len(self.candles)):
            trs: list[float] = []
            pdms: list[float] = []
            ndms: list[float] = []
            for k in range(i - period + 1, i + 1):
                cur = self.candles[k]
                prev = self.candles[k - 1]
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
                series[i] = 0.0
                continue
            pdi = 100.0 * (mean(pdms) / atr)
            ndi = 100.0 * (mean(ndms) / atr)
            denom = pdi + ndi
            series[i] = 100.0 * abs(pdi - ndi) / denom if denom > 1e-12 else 0.0
        return series

    def _build_realized_vol(self, window: int) -> list[float | None]:
        series: list[float | None] = [None] * len(self.closes)
        if window <= 0:
            return series
        for i in range(window, len(self.closes)):
            seg = self.closes[i - window : i + 1]
            rets = []
            for a, b in zip(seg[:-1], seg[1:]):
                if a <= 0:
                    continue
                rets.append(log(b / a))
            if len(rets) < 2:
                continue
            mu = mean(rets)
            var = mean([(r - mu) ** 2 for r in rets])
            series[i] = sqrt(max(var, 0.0))
        return series