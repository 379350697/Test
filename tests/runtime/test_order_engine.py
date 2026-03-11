from __future__ import annotations

import pytest

from quantx.runtime.events import FillEvent, OrderEvent
from quantx.runtime.models import OrderIntent
from quantx.runtime.order_engine import OrderEngine, OrderStateError


@pytest.fixture
def order_intent() -> OrderIntent:
    return OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100000.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
    )


def make_order_event(status: str, exchange_order_id: str | None = 'oid-1') -> OrderEvent:
    return OrderEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
        client_order_id='cid-1',
        exchange_order_id=exchange_order_id,
        status=status,
        payload={},
    )


def make_fill_event(qty: float, trade_id: str) -> FillEvent:
    return FillEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
        client_order_id='cid-1',
        exchange_order_id='oid-1',
        trade_id=trade_id,
        side='buy',
        position_side='long',
        qty=qty,
        price=100000.0,
        fee=1.0,
        payload={},
    )


def test_order_engine_advances_through_runtime_lifecycle(order_intent: OrderIntent):
    engine = OrderEngine()

    created = engine.create_intent(client_order_id='cid-1', intent=order_intent)
    assert created.status == 'intent_created'

    engine.apply_order_event(make_order_event('risk_accepted', exchange_order_id=None))
    engine.apply_order_event(make_order_event('submitted', exchange_order_id=None))
    engine.apply_order_event(make_order_event('acked'))
    engine.apply_order_event(make_order_event('working'))
    partial = engine.apply_fill_event(make_fill_event(qty=0.4, trade_id='tid-1'))

    assert partial.status == 'partially_filled'
    assert partial.filled_qty == pytest.approx(0.4)

    filled = engine.apply_fill_event(make_fill_event(qty=0.6, trade_id='tid-2'))

    assert filled.status == 'filled'
    assert filled.exchange_order_id == 'oid-1'
    assert filled.filled_qty == pytest.approx(1.0)


@pytest.mark.parametrize(
    ('status', 'from_status'),
    [
        ('rejected', 'submitted'),
        ('canceled', 'working'),
        ('expired', 'working'),
    ],
)
def test_order_engine_supports_terminal_runtime_branches(
    order_intent: OrderIntent,
    status: str,
    from_status: str,
):
    engine = OrderEngine()
    engine.create_intent(client_order_id='cid-1', intent=order_intent)
    engine.apply_order_event(make_order_event('risk_accepted', exchange_order_id=None))
    engine.apply_order_event(make_order_event('submitted', exchange_order_id=None))

    if from_status == 'working':
        engine.apply_order_event(make_order_event('acked'))
        engine.apply_order_event(make_order_event('working'))

    order = engine.apply_order_event(make_order_event(status))

    assert order.status == status


def test_order_engine_rejects_invalid_transition(order_intent: OrderIntent):
    engine = OrderEngine()
    engine.create_intent(client_order_id='cid-1', intent=order_intent)

    with pytest.raises(OrderStateError):
        engine.apply_order_event(make_order_event('working'))


def test_order_engine_is_idempotent_for_duplicate_exchange_events(order_intent: OrderIntent):
    engine = OrderEngine()
    engine.create_intent(client_order_id='cid-1', intent=order_intent)
    engine.apply_order_event(make_order_event('risk_accepted', exchange_order_id=None))
    engine.apply_order_event(make_order_event('submitted', exchange_order_id=None))
    first = engine.apply_order_event(make_order_event('acked'))
    second = engine.apply_order_event(make_order_event('acked'))

    assert first.status == 'acked'
    assert second.status == 'acked'
    assert second.exchange_order_id == 'oid-1'
