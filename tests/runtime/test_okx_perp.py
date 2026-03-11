from __future__ import annotations

from quantx.exchanges.base import ExchangeOrder
from quantx.exchanges.okx_perp import OKXPerpAdapter
from quantx.runtime.events import AccountEvent, FillEvent, OrderEvent



def test_okx_perp_adapter_normalizes_order_fill_position_and_account_events():
    adapter = OKXPerpAdapter()

    order = adapter.normalize_order_event(
        {
            'instId': 'BTC-USDT-SWAP',
            'clOrdId': 'cid-1',
            'ordId': 'oid-1',
            'state': 'live',
            'side': 'buy',
            'posSide': 'long',
            'tdMode': 'cross',
            'uTime': '1710201600000',
        }
    )
    fill = adapter.normalize_fill_event(
        {
            'instId': 'BTC-USDT-SWAP',
            'clOrdId': 'cid-1',
            'ordId': 'oid-1',
            'tradeId': 'tid-1',
            'fillSz': '1',
            'fillPx': '100',
            'fillFee': '-0.2',
            'side': 'buy',
            'posSide': 'long',
            'tdMode': 'cross',
            'fillTime': '1710201601000',
        }
    )
    position = adapter.normalize_position_event(
        {
            'instId': 'BTC-USDT-SWAP',
            'posSide': 'long',
            'pos': '2',
            'avgPx': '101',
            'mgnMode': 'cross',
            'uTime': '1710201602000',
        }
    )
    account = adapter.normalize_account_event(
        {
            'ccy': 'USDT',
            'eq': '1000',
            'availEq': '800',
            'imr': '120',
            'mmr': '50',
            'upl': '25',
            'uTime': '1710201603000',
        }
    )

    assert isinstance(order, OrderEvent)
    assert order.status == 'working'
    assert order.payload['position_side'] == 'long'
    assert order.payload['margin_mode'] == 'cross'

    assert isinstance(fill, FillEvent)
    assert fill.position_side == 'long'
    assert fill.qty == 1.0
    assert fill.fee == -0.2

    assert isinstance(position, AccountEvent)
    assert position.event_type == 'position'
    assert position.payload['symbol'] == 'BTC-USDT-SWAP'
    assert position.payload['position_side'] == 'long'
    assert position.payload['margin_mode'] == 'cross'

    assert isinstance(account, AccountEvent)
    assert account.event_type == 'account'
    assert account.payload['equity'] == 1000.0
    assert account.payload['available_margin'] == 800.0



def test_okx_perp_adapter_maps_rest_place_response_to_runtime_ack():
    adapter = OKXPerpAdapter()
    order = ExchangeOrder(
        client_order_id='cid-1',
        symbol='BTC-USDT-SWAP',
        side='BUY',
        qty=1.0,
        order_type='MARKET',
        price=None,
        position_side='long',
        margin_mode='cross',
        reduce_only=False,
    )

    event = adapter.normalize_place_order_response(
        order,
        {'data': [{'clOrdId': 'cid-1', 'ordId': 'oid-1', 'sCode': '0', 'sMsg': ''}]},
        ts='2026-03-12T00:00:00+00:00',
    )

    assert event.status == 'acked'
    assert event.client_order_id == 'cid-1'
    assert event.payload['position_side'] == 'long'
