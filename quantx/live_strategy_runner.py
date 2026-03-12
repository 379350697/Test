from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .models import Candle
from .runtime.models import OrderIntent
from .runtime.strategy_runtime import LegacySignalBarStrategyAdapter, StrategyRuntime
from .strategies import get_strategy_class


@dataclass(slots=True)
class LiveStrategyRunner:
    strategy_name: str
    watchlist: tuple[str, ...]
    strategy_params: dict[str, object] = field(default_factory=dict)
    _runtimes: dict[str, StrategyRuntime] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        strategy_cls = get_strategy_class(self.strategy_name)
        self.watchlist = tuple(symbol.upper() for symbol in self.watchlist)
        for symbol in self.watchlist:
            strategy = strategy_cls(**self.strategy_params)
            adapter = LegacySignalBarStrategyAdapter(
                legacy_strategy=strategy,
                symbol=symbol,
                live_position_mode='net',
            )
            self._runtimes[symbol] = StrategyRuntime(strategy=adapter)

    def on_bar_batch(self, bars_by_symbol: dict[str, Sequence[Candle]]) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        for symbol in self.watchlist:
            bars = list(bars_by_symbol.get(symbol, ()))
            if not bars:
                continue
            ts = bars[-1].ts.isoformat() if hasattr(bars[-1].ts, 'isoformat') else str(bars[-1].ts)
            intents.extend(self._runtimes[symbol].on_bar(bars, bar_index=len(bars) - 1, ts=ts))
        return intents
