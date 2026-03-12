from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..models import Candle
from ..strategies import BaseStrategy
from .events import AccountEvent, FillEvent, MarketEvent, OrderEvent
from .models import OrderIntent

RuntimeEvent = MarketEvent | OrderEvent | FillEvent | AccountEvent


@dataclass(slots=True)
class StrategyContext:
    strategy_id: str
    state: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


class BaseEventStrategy:
    strategy_id = 'event'

    def on_event(self, ctx: StrategyContext, event: RuntimeEvent) -> list[OrderIntent]:
        raise NotImplementedError


class BaseBarStrategy:
    strategy_id = 'bar'

    def on_bar(self, bars: Sequence[Candle], *, bar_index: int, ctx: StrategyContext | None = None) -> list[OrderIntent]:
        raise NotImplementedError


@dataclass(slots=True)
class StrategyRuntime:
    strategy: BaseEventStrategy | BaseBarStrategy
    _intent_seq: int = 0

    def on_event(self, event: RuntimeEvent) -> list[OrderIntent]:
        raw = self.strategy.on_event(self._ctx(), event)
        return self._stamp(raw, ts=getattr(event, 'ts', None))

    def on_bar(self, bars: Sequence[Candle], *, bar_index: int, ts: str | None = None) -> list[OrderIntent]:
        raw = self.strategy.on_bar(bars, bar_index=bar_index, ctx=self._ctx())
        return self._stamp(raw, ts=ts)

    def _ctx(self) -> StrategyContext:
        return StrategyContext(strategy_id=self.strategy_id)

    @property
    def strategy_id(self) -> str:
        return str(getattr(self.strategy, 'strategy_id', 'strategy'))

    def _stamp(self, intents: Sequence[OrderIntent], *, ts: str | None) -> list[OrderIntent]:
        stamped: list[OrderIntent] = []
        for intent in intents:
            self._intent_seq += 1
            stamped.append(
                replace(
                    intent,
                    strategy_id=intent.strategy_id or self.strategy_id,
                    intent_id=intent.intent_id or f'{self.strategy_id}-{self._intent_seq}',
                    created_ts=intent.created_ts or ts,
                )
            )
        return stamped


@dataclass(slots=True)
class LegacySignalBarStrategyAdapter(BaseBarStrategy):
    legacy_strategy: BaseStrategy
    symbol: str
    strategy_id: str | None = None

    def __post_init__(self) -> None:
        if self.strategy_id is None:
            self.strategy_id = str(getattr(self.legacy_strategy, 'name', 'legacy-bar'))

    def on_bar(self, bars: Sequence[Candle], *, bar_index: int, ctx: StrategyContext | None = None) -> list[OrderIntent]:
        signal = self.legacy_strategy.signal(list(bars), bar_index)
        if signal == 0:
            return []

        bar = bars[bar_index]
        if signal > 0:
            return [
                OrderIntent(
                    symbol=self.symbol,
                    side='buy',
                    position_side='long',
                    qty=1.0,
                    price=bar.close,
                    order_type='market',
                    time_in_force='ioc',
                    reduce_only=False,
                    reason=f'legacy_signal:{signal}',
                    tags=('bar', 'legacy_signal'),
                )
            ]

        return [
            OrderIntent(
                symbol=self.symbol,
                side='sell',
                position_side='short',
                qty=1.0,
                price=bar.close,
                order_type='market',
                time_in_force='ioc',
                reduce_only=False,
                reason=f'legacy_signal:{signal}',
                tags=('bar', 'legacy_signal'),
            )
        ]
