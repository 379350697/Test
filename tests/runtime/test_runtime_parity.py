from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from quantx.backtest import run_backtest
from quantx.models import BacktestConfig, Candle
from quantx.strategies import BaseStrategy, STRATEGY_REGISTRY, register_strategy_class


class FixedFlipStrategy(BaseStrategy):
    name = 'fixed_flip'
    version = '0.1.0'
    category = 'test'
    author = 'runtime'
    description = 'deterministic open-close strategy'
    default_params = {}
    tags = ['runtime', 'parity']

    def signal(self, candles, i):
        if i == 1:
            return 1
        if i == 3:
            return -1
        return 0


register_strategy_class(FixedFlipStrategy)



def test_backtest_runtime_trace_records_order_flow_and_ledger_state():
    candles = [
        Candle(
            ts=datetime(2024, 1, 1) + timedelta(hours=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=10.0 + i,
        )
        for i in range(12)
    ]
    cfg = BacktestConfig(symbol='SOLUSDT', timeframe='1h', fee_rate=0.0, slippage_pct=0.0)

    res = run_backtest(candles, 'fixed_flip', {}, cfg)
    runtime = res.extra['runtime']

    assert runtime['mode'] == 'backtest'
    assert len(runtime['orders']) == len(res.trades)
    assert [order['status'] for order in runtime['orders']] == ['filled', 'filled']
    assert runtime['ledger']['equity'] == pytest.approx(res.equity_curve[-1][1])
    assert runtime['positions']['long']['qty'] == pytest.approx(0.0)


from quantx.execution import PaperLiveExecutor


def test_paper_runtime_executor_tracks_short_positions_with_runtime_trace():
    ex = PaperLiveExecutor('paper')
    ex.arm()

    sell = ex.place_order('BTCUSDT', 'SELL', 0.5, order_type='market', market_price=100.0)

    assert sell['accepted'] is True
    assert sell['filled'] is True
    assert ex.state.positions['BTCUSDT'] == pytest.approx(-0.5)
    assert ex.state.runtime['orders'][-1]['status'] == 'filled'
    assert ex.state.runtime['orders'][-1]['position_side'] == 'short'
