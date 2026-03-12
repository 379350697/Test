from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable, Protocol

from .models import Candle


class LiveMarketDriver(Protocol):
    venue: str

    def poll_once(self) -> dict[str, list[Candle]]:
        ...

    def stream(self) -> Iterable[tuple[str, list[Candle]]]:
        ...


@dataclass(slots=True)
class OKXKlineMarketDriver:
    client: Any
    watchlist: tuple[str, ...]
    timeframe: str = "5m"
    venue: str = "okx"
    _last_closed_bar_ts: dict[str, str] = field(default_factory=dict)

    def poll_once(self) -> dict[str, list[Candle]]:
        updates: dict[str, list[Candle]] = {}
        for symbol in self.watchlist:
            last_seen_ts = self._last_closed_bar_ts.get(symbol)
            bars = self.client.get_candles(symbol, bar=self.timeframe, limit=200)
            closed_bars: list[Candle] = []
            latest_closed_ts = last_seen_ts
            for row in reversed(bars):
                if not row.get("confirmed"):
                    continue
                bar_ts = str(row["ts"])
                if last_seen_ts is not None and int(bar_ts) <= int(last_seen_ts):
                    continue
                closed_bars.append(_build_candle(row))
                latest_closed_ts = bar_ts
            if closed_bars:
                updates[symbol] = closed_bars
                if latest_closed_ts is not None:
                    self._last_closed_bar_ts[symbol] = latest_closed_ts
        return updates

    def stream(self) -> Iterable[tuple[str, list[Candle]]]:
        for item in self.poll_once().items():
            yield item


def _build_candle(row: dict[str, Any]) -> Candle:
    return Candle(
        ts=datetime.fromtimestamp(int(str(row["ts"])) / 1000, tz=UTC),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )
