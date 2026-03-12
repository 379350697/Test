from __future__ import annotations

from quantx.exchanges.base import ExchangeOrder
from quantx.exchanges.binance_perp import BinancePerpAdapter
from quantx.runtime.events import AccountEvent, FillEvent, MarketEvent, OrderEvent


def test_binance_perp_adapter_normalizes_order_fill_position_account_and_depth_events():
    adapter = BinancePerpAdapter()

    order = adapter.normalize_order_event(
        {
            'o': {
                's': 'BTCUSDT',
                'c': 'cid-1',
                'i': 123,
                'X': 'NEW',
                'S': 'BUY',
                'ps': 'LONG',
                'ot': 'MARKET',
            },
            'E': 1710201600000,
        }
    )
    fill = adapter.normalize_fill_event(
        {
            'o': {
                's': 'BTCUSDT',
                'c': 'cid-1',
                'i': 123,
                't': 456,
                'S': 'BUY',
                'ps': 'LONG',
                'l': '1.25',
                'L': '100.5',
                'n': '0.2',
                'N': 'USDT',
            },
            'E': 1710201601000,
        }
    )
    position = adapter.normalize_position_event(
        {
            's': 'BTCUSDT',
            'pa': '0.5',
            'ep': '100.0',
            'ps': 'LONG',
            'mt': 'cross',
            'up': '5.0',
        },
        ts='2026-03-12T00:00:02+00:00',
    )
    account = adapter.normalize_account_event(
        {
            'a': {
                'm': 'ORDER',
                'B': [{'a': 'USDT', 'wb': '1000', 'cw': '850'}],
                'P': [],
            },
            'E': 1710201603000,
        }
    )
    depth = adapter.normalize_depth_event(
        {
            'stream': 'btcusdt@depth20@100ms',
            'data': {
                's': 'BTCUSDT',
                'b': [['100.0', '1.2']],
                'a': [['100.1', '0.8']],
                'E': 1710201604000,
            },
        }
    )

    assert isinstance(order, OrderEvent)
    assert order.status == 'acked'
    assert order.payload['position_side'] == 'long'

    assert isinstance(fill, FillEvent)
    assert fill.position_side == 'long'
    assert fill.qty == 1.25
    assert fill.price == 100.5
    assert fill.fee == 0.2

    assert isinstance(position, AccountEvent)
    assert position.event_type == 'position'
    assert position.payload['symbol'] == 'BTCUSDT'
    assert position.payload['position_side'] == 'long'
    assert position.payload['margin_mode'] == 'cross'

    assert isinstance(account, AccountEvent)
    assert account.event_type == 'account'
    assert account.payload['equity'] == 1000.0
    assert account.payload['available_margin'] == 850.0

    assert isinstance(depth, MarketEvent)
    assert depth.channel == 'depth'
    assert depth.payload['bids'][0] == [100.0, 1.2]
    assert depth.payload['asks'][0] == [100.1, 0.8]


def test_binance_perp_adapter_maps_rest_place_response_to_runtime_ack():
    adapter = BinancePerpAdapter()
    order = ExchangeOrder(
        client_order_id='cid-1',
        symbol='BTCUSDT',
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
        {'clientOrderId': 'cid-1', 'orderId': 123, 'status': 'NEW'},
        ts='2026-03-12T00:00:00+00:00',
    )

    assert event.status == 'acked'
    assert event.client_order_id == 'cid-1'
    assert event.payload['position_side'] == 'long'
