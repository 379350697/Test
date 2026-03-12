from __future__ import annotations

from quantx.runtime.events import MarketEvent
from quantx.runtime.models import OrderIntent
from quantx.runtime.paper_exchange import PaperExchangeConfig, PaperExchangeSimulator


def make_buy_intent(*, qty: float = 1.0) -> OrderIntent:
    return OrderIntent(
        symbol='BTCUSDT',
        side='buy',
        position_side='long',
        qty=qty,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
    )


def make_market_event(*, price: float, ts: str) -> MarketEvent:
    return MarketEvent(
        symbol='BTCUSDT',
        exchange='paper',
        channel='mark_price',
        ts=ts,
        payload={'price': price},
    )


def test_paper_exchange_emits_ack_partial_fill_and_cancel_events():
    exchange = PaperExchangeSimulator(
        initial_cash=1000.0,
        config=PaperExchangeConfig(queue_delay_ticks=1, cancel_delay_ticks=1, partial_fill_ratio=0.5),
    )

    events = exchange.submit_intents([make_buy_intent()], exchange_name='paper', ts='2026-03-12T00:00:00+00:00')
    events += exchange.on_market_event(make_market_event(price=100.0, ts='2026-03-12T00:00:01+00:00'))
    events += exchange.cancel_order(client_order_id='paper-1', ts='2026-03-12T00:00:02+00:00')
    events += exchange.on_market_event(make_market_event(price=100.0, ts='2026-03-12T00:00:03+00:00'))

    statuses = [event.status for event in events if hasattr(event, 'status')]
    snapshot = exchange.snapshot()

    assert 'acked' in statuses
    assert 'partially_filled' in statuses
    assert 'canceled' in statuses
    assert snapshot['orders'][0]['status'] == 'canceled'


def test_paper_exchange_snapshot_tracks_runtime_ledger_and_positions():
    exchange = PaperExchangeSimulator(initial_cash=1000.0, config=PaperExchangeConfig())

    exchange.submit_intents([make_buy_intent()], exchange_name='paper', ts='2026-03-12T00:00:00+00:00')
    exchange.on_market_event(make_market_event(price=100.0, ts='2026-03-12T00:00:01+00:00'))
    snapshot = exchange.snapshot()

    assert snapshot['mode'] == 'paper'
    assert snapshot['orders'][0]['status'] == 'filled'
    assert snapshot['positions']['BTCUSDT']['long']['qty'] == 1.0



def test_paper_exchange_snapshot_exposes_continuity_signals_for_soak_review():
    exchange = PaperExchangeSimulator(initial_cash=1000.0, config=PaperExchangeConfig())

    exchange.submit_intents([make_buy_intent()], exchange_name='paper', ts='2026-03-12T00:00:00+00:00')
    exchange.on_market_event(make_market_event(price=100.0, ts='2026-03-12T00:00:01+00:00'))
    snapshot = exchange.snapshot()

    assert snapshot['health']['degraded'] is False
    assert snapshot['position_invariants']['open_position_count'] == 1
    assert snapshot['ledger_invariants']['used_margin_non_negative'] is True
