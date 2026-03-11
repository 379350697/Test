from __future__ import annotations

import pytest

from quantx.runtime.events import FillEvent, MarketEvent, OrderEvent
from quantx.runtime.fill_engine import FillEngine, FillEngineConfig
from quantx.runtime.models import TrackedOrder


@pytest.fixture
def tracked_order() -> TrackedOrder:
    return TrackedOrder(
        client_order_id='cid-1',
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
        status='working',
    )


def make_market_event(price: float, ts: str) -> MarketEvent:
    return MarketEvent(
        symbol='BTC-USDT-SWAP',
        exchange='paper',
        channel='mark_price',
        ts=ts,
        payload={'price': price},
    )


def test_fill_engine_waits_for_queue_delay_before_emitting_fill(tracked_order: TrackedOrder):
    engine = FillEngine(FillEngineConfig(queue_delay_ticks=2, partial_fill_ratio=1.0, slippage_bps=0.0))
    engine.submit_order(tracked_order, exchange='paper', ts='2026-03-12T00:00:00+00:00')

    first = engine.on_market_event(make_market_event(price=99.0, ts='2026-03-12T00:00:01+00:00'))
    second = engine.on_market_event(make_market_event(price=99.0, ts='2026-03-12T00:00:02+00:00'))

    assert [event for event in first if isinstance(event, FillEvent)] == []
    assert [event for event in second if isinstance(event, FillEvent)][0].qty == pytest.approx(1.0)



def test_fill_engine_emits_partial_fills_before_completing_order(tracked_order: TrackedOrder):
    engine = FillEngine(FillEngineConfig(queue_delay_ticks=1, partial_fill_ratio=0.4, slippage_bps=0.0))
    engine.submit_order(tracked_order, exchange='paper', ts='2026-03-12T00:00:00+00:00')

    first = engine.on_market_event(make_market_event(price=99.0, ts='2026-03-12T00:00:01+00:00'))
    second = engine.on_market_event(make_market_event(price=99.0, ts='2026-03-12T00:00:02+00:00'))

    first_fill = [event for event in first if isinstance(event, FillEvent)][0]
    second_fill = [event for event in second if isinstance(event, FillEvent)][0]
    second_order = [event for event in second if isinstance(event, OrderEvent)][-1]

    assert first_fill.qty == pytest.approx(0.4)
    assert second_fill.qty == pytest.approx(0.6)
    assert second_order.status == 'filled'



def test_fill_engine_honors_cancel_delay_before_cancel_ack(tracked_order: TrackedOrder):
    engine = FillEngine(FillEngineConfig(queue_delay_ticks=10, cancel_delay_ticks=2, partial_fill_ratio=1.0, slippage_bps=0.0))
    engine.submit_order(tracked_order, exchange='paper', ts='2026-03-12T00:00:00+00:00')
    engine.request_cancel(
        client_order_id='cid-1',
        symbol='BTC-USDT-SWAP',
        exchange='paper',
        ts='2026-03-12T00:00:01+00:00',
    )

    first = engine.on_market_event(make_market_event(price=99.0, ts='2026-03-12T00:00:02+00:00'))
    second = engine.on_market_event(make_market_event(price=99.0, ts='2026-03-12T00:00:03+00:00'))

    assert [event for event in first if isinstance(event, OrderEvent)] == []
    assert [event for event in second if isinstance(event, OrderEvent)][0].status == 'canceled'



def test_fill_engine_applies_directional_slippage_to_synthetic_fills():
    buy_order = TrackedOrder(
        client_order_id='cid-buy',
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=None,
        order_type='market',
        time_in_force='ioc',
        reduce_only=False,
        status='working',
    )
    sell_order = TrackedOrder(
        client_order_id='cid-sell',
        symbol='BTC-USDT-SWAP',
        side='sell',
        position_side='short',
        qty=1.0,
        price=None,
        order_type='market',
        time_in_force='ioc',
        reduce_only=False,
        status='working',
    )
    engine = FillEngine(FillEngineConfig(queue_delay_ticks=1, partial_fill_ratio=1.0, slippage_bps=10.0))
    engine.submit_order(buy_order, exchange='paper', ts='2026-03-12T00:00:00+00:00')
    engine.submit_order(sell_order, exchange='paper', ts='2026-03-12T00:00:00+00:00')

    events = engine.on_market_event(make_market_event(price=100.0, ts='2026-03-12T00:00:01+00:00'))
    fills = [event for event in events if isinstance(event, FillEvent)]
    prices = {event.client_order_id: event.price for event in fills}

    assert prices['cid-buy'] == pytest.approx(100.1)
    assert prices['cid-sell'] == pytest.approx(99.9)
