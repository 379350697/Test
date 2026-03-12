from __future__ import annotations

from datetime import datetime, timezone

from quantx.models import Candle
from quantx.runtime.events import MarketEvent
from quantx.runtime.models import OrderIntent
from quantx.strategies import BaseStrategy

from quantx.runtime.strategy_runtime import (
    BaseEventStrategy,
    LegacySignalBarStrategyAdapter,
    StrategyRuntime,
)


class DummyEventStrategy(BaseEventStrategy):
    strategy_id = 'dummy-event'

    def on_event(self, ctx, event):
        return [
            OrderIntent(
                symbol=event.symbol,
                side='buy',
                position_side='long',
                qty=1.0,
                price=event.payload['price'],
                order_type='limit',
                time_in_force='gtc',
                reduce_only=False,
            )
        ]


class DummyLegacySignalStrategy(BaseStrategy):
    name = 'dummy_legacy_signal'

    def signal(self, candles, i):
        if i == 1:
            return 1
        if i == 2:
            return -1
        return 0


def make_market_event(price: float) -> MarketEvent:
    return MarketEvent(
        symbol='SOLUSDT',
        exchange='paper',
        channel='mark_price',
        ts='2026-03-12T00:00:00+00:00',
        payload={'price': price},
    )


def make_bar(close: float) -> Candle:
    return Candle(
        ts=datetime(2026, 3, 12, tzinfo=timezone.utc),
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=10.0,
    )


def test_strategy_runtime_stamps_intents_from_event_strategy():
    runtime = StrategyRuntime(strategy=DummyEventStrategy())

    intents = runtime.on_event(make_market_event(price=100.0))

    assert len(intents) == 1
    assert intents[0].strategy_id == 'dummy-event'
    assert intents[0].intent_id.startswith('dummy-event-')
    assert intents[0].created_ts == '2026-03-12T00:00:00+00:00'


def test_legacy_signal_strategy_adapts_to_bar_contract():
    legacy = DummyLegacySignalStrategy()
    adapter = LegacySignalBarStrategyAdapter(legacy, symbol='SOLUSDT')
    bars = [make_bar(100.0), make_bar(101.0)]

    intents = adapter.on_bar(bars, bar_index=1)

    assert len(intents) == 1
    assert intents[0].side == 'buy'
    assert intents[0].position_side == 'long'
    assert intents[0].symbol == 'SOLUSDT'

class DummyEventStrategyWithMetadata(BaseEventStrategy):
    strategy_id = 'dummy-event-metadata'

    def on_event(self, ctx, event):
        return [
            OrderIntent(
                symbol=event.symbol,
                side='buy',
                position_side='net',
                qty=1.0,
                price=event.payload['price'],
                order_type='limit',
                time_in_force='gtc',
                reduce_only=False,
                metadata={'strategy_name': 'dummy-event-metadata'},
            )
        ]


def test_strategy_runtime_preserves_metadata_for_multi_symbol_net_intents():
    runtime = StrategyRuntime(strategy=DummyEventStrategyWithMetadata())

    intents = runtime.on_event(make_market_event(price=100.0))

    assert intents[0].position_side == 'net'
    assert intents[0].metadata['strategy_name'] == 'dummy-event-metadata'
