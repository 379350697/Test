from __future__ import annotations

from quantx.backtest import run_event_backtest
from quantx.models import BacktestConfig
from quantx.runtime.events import MarketEvent
from quantx.runtime.models import OrderIntent
from quantx.runtime.strategy_runtime import BaseEventStrategy


class DummyImpulseEventStrategy(BaseEventStrategy):
    strategy_id = 'impulse'
    version = '0.1.0'

    def on_event(self, ctx, event):
        if event.kind.value == 'market_event' and event.payload['price'] <= 100.0:
            return [
                OrderIntent(
                    symbol=event.symbol,
                    side='buy',
                    position_side='long',
                    qty=1.0,
                    price=event.payload['price'],
                    order_type='market',
                    time_in_force='ioc',
                    reduce_only=False,
                )
            ]
        return []


def make_market_tape(prices: list[float]) -> list[MarketEvent]:
    return [
        MarketEvent(
            symbol='SOLUSDT',
            exchange='backtest',
            channel='mark_price',
            ts=f'2026-03-12T00:00:0{i}+00:00',
            payload={'price': price},
        )
        for i, price in enumerate(prices)
    ]


def test_event_backtest_replays_market_tape_through_runtime_session():
    tape = make_market_tape([101.0, 100.0, 103.0])

    res = run_event_backtest(
        tape,
        DummyImpulseEventStrategy(),
        BacktestConfig(symbol='SOLUSDT', timeframe='event', fee_rate=0.0, slippage_pct=0.0),
    )

    assert res.extra['runtime']['mode'] == 'event_backtest'
    assert res.extra['runtime']['fidelity'] == 'high'
    assert res.extra['runtime']['orders'][0]['status'] == 'filled'
    assert res.extra['runtime']['orders'][0]['strategy_id'] == 'impulse'
