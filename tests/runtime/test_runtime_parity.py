from __future__ import annotations

from pathlib import Path

from datetime import datetime, timedelta

import pytest

from quantx.backtest import run_backtest, run_event_backtest
from quantx.models import BacktestConfig, Candle
from quantx.replay import build_daily_replay_report
from quantx.runtime.events import MarketEvent
from quantx.runtime.models import OrderIntent
from quantx.runtime.paper_exchange import PaperExchangeConfig, PaperExchangeSimulator
from quantx.runtime.strategy_runtime import BaseEventStrategy
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



def test_bar_backtest_uses_runtime_session_and_reports_low_fidelity():
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

    assert runtime['mode'] == 'bar_backtest'
    assert runtime['fidelity'] == 'low'
    assert len(runtime['orders']) == len(res.trades)
    assert [order['status'] for order in runtime['orders']] == ['filled', 'filled']
    assert runtime['ledger']['equity'] == pytest.approx(res.equity_curve[-1][1])
    assert runtime['positions']['SOLUSDT']['long']['qty'] == pytest.approx(0.0)


from quantx.execution import PaperLiveExecutor


def test_paper_runtime_executor_tracks_short_positions_with_runtime_trace():
    ex = PaperLiveExecutor('paper')
    ex.arm()

    sell = ex.place_order('BTCUSDT', 'SELL', 0.5, order_type='market', market_price=100.0)

    assert sell['accepted'] is True
    assert sell['filled'] is True
    assert ex.state.positions['BTCUSDT'] == pytest.approx(-0.5)
    assert ex.state.runtime['mode'] == 'paper'
    assert ex.state.runtime['orders'][-1]['status'] == 'filled'
    assert ex.state.runtime['orders'][-1]['position_side'] == 'short'


def test_runtime_parity_backtest_and_paper_share_order_state_sequences_and_flat_ledger_invariants():
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

    backtest = run_backtest(candles, 'fixed_flip', {}, cfg)

    ex = PaperLiveExecutor('paper')
    ex.arm()
    ex.place_order('SOLUSDT', 'BUY', 1.0, order_type='market', market_price=101.0, position_side='long')
    ex.place_order('SOLUSDT', 'SELL', 1.0, order_type='market', market_price=103.0, position_side='long', reduce_only=True)

    expected_sequence = ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working', 'filled']
    backtest_sequences = list(backtest.extra['runtime']['order_state_sequences'].values())
    paper_sequences = list(ex.state.runtime['order_state_sequences'].values())

    assert backtest_sequences
    assert paper_sequences
    assert all(sequence == expected_sequence for sequence in backtest_sequences)
    assert all(sequence == expected_sequence for sequence in paper_sequences)
    assert backtest.extra['runtime']['ledger']['used_margin'] == pytest.approx(0.0)
    assert ex.state.runtime['ledger']['used_margin'] == pytest.approx(0.0)
    assert backtest.extra['runtime']['positions']['SOLUSDT']['long']['qty'] == pytest.approx(0.0)
    assert ex.state.runtime['positions']['SOLUSDT']['long']['qty'] == pytest.approx(0.0)


class _ParityEventStrategy(BaseEventStrategy):
    strategy_id = 'parity-event'

    def on_event(self, ctx, event):
        if event.payload['price'] <= 100.0:
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


def test_event_backtest_paper_and_live_replay_share_order_sequences_for_same_intent_family():
    tape = [
        MarketEvent(symbol='BTC-USDT-SWAP', exchange='backtest', channel='mark_price', ts='2026-03-12T00:00:00+00:00', payload={'price': 101.0}),
        MarketEvent(symbol='BTC-USDT-SWAP', exchange='backtest', channel='mark_price', ts='2026-03-12T00:00:01+00:00', payload={'price': 100.0}),
        MarketEvent(symbol='BTC-USDT-SWAP', exchange='backtest', channel='mark_price', ts='2026-03-12T00:00:02+00:00', payload={'price': 102.0}),
    ]
    backtest = run_event_backtest(
        tape,
        _ParityEventStrategy(),
        BacktestConfig(symbol='BTC-USDT-SWAP', timeframe='event', fee_rate=0.0, slippage_pct=0.0),
    )

    paper = PaperExchangeSimulator(initial_cash=1000.0, config=PaperExchangeConfig())
    paper.submit_intents([
        OrderIntent(
            symbol='BTC-USDT-SWAP',
            side='buy',
            position_side='long',
            qty=1.0,
            price=100.0,
            order_type='market',
            time_in_force='ioc',
            reduce_only=False,
        )
    ], exchange_name='paper', ts='2026-03-12T00:00:01+00:00')
    paper.on_market_event(tape[1])

    fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'runtime_market_tape.jsonl'
    replay = build_daily_replay_report(event_log_path=str(fixture), day='2026-03-12')

    backtest_sequences = list(backtest.extra['runtime']['order_state_sequences'].values())
    paper_sequences = list(paper.snapshot()['order_state_sequences'].values())
    live_replay_sequences = list(replay['runtime_summary']['order_state_sequences'].values())

    assert backtest_sequences == paper_sequences == live_replay_sequences
