from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quantx.live_strategy_runner import LiveStrategyRunner
from quantx.models import Candle


def _bars(base_price: float) -> list[Candle]:
    start = datetime(2026, 3, 12, tzinfo=timezone.utc)
    return [
        Candle(ts=start, open=base_price, high=base_price + 1, low=base_price - 1, close=base_price, volume=10.0),
        Candle(ts=start + timedelta(minutes=1), open=base_price + 0.5, high=base_price + 1.5, low=base_price, close=base_price + 1, volume=10.0),
        Candle(ts=start + timedelta(minutes=2), open=base_price + 1.0, high=base_price + 2.0, low=base_price + 0.5, close=base_price + 1.5, volume=10.0),
        Candle(ts=start + timedelta(minutes=3), open=base_price + 2.0, high=base_price + 5.0, low=base_price + 1.5, close=base_price + 4.5, volume=12.0),
    ]


def test_live_strategy_runner_emits_multi_symbol_net_intents_with_strategy_metadata():
    runner = LiveStrategyRunner(
        strategy_name='cta_strategy',
        watchlist=('BTC-USDT-SWAP', 'ETH-USDT-SWAP'),
        strategy_params={
            'lookback': 3,
            'adx_filter': 0,
            'short_adx_filter': 0,
            'atr_expansion': False,
            'atr_price_threshold': 0,
            'min_vol': 0,
            'entry_margin_pct': 0.1,
            'max_leverage': 3.0,
        },
    )

    intents = runner.on_bar_batch(
        {
            'BTC-USDT-SWAP': _bars(100.0),
            'ETH-USDT-SWAP': _bars(200.0),
        }
    )

    assert intents
    assert {intent.symbol for intent in intents} <= {'BTC-USDT-SWAP', 'ETH-USDT-SWAP'}
    assert all(intent.position_side == 'net' for intent in intents)
    assert all(intent.metadata['strategy_name'] == 'cta_strategy' for intent in intents)
