from dataclasses import is_dataclass

from quantx.runtime.events import (
    AccountEvent,
    EventKind,
    FillEvent,
    MarketEvent,
    OrderEvent,
)


def test_event_kind_declares_all_runtime_event_families():
    assert EventKind.MARKET.value == 'market_event'
    assert EventKind.ORDER.value == 'order_event'
    assert EventKind.FILL.value == 'fill_event'
    assert EventKind.ACCOUNT.value == 'account_event'


def test_event_market_dataclass_carries_symbol_and_origin():
    ev = MarketEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        channel='trades',
        ts='2026-03-12T00:00:00+00:00',
        payload={'price': 100000.0},
    )

    assert is_dataclass(ev)
    assert ev.kind is EventKind.MARKET
    assert ev.symbol == 'BTC-USDT-SWAP'
    assert ev.exchange == 'okx'
    assert ev.channel == 'trades'


def test_event_order_fill_and_account_dataclasses_expose_runtime_fields():
    order = OrderEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
        client_order_id='cid-1',
        exchange_order_id='oid-1',
        status='acked',
        payload={'note': 'accepted'},
    )
    fill = FillEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
        client_order_id='cid-1',
        exchange_order_id='oid-1',
        trade_id='tid-1',
        side='buy',
        position_side='long',
        qty=1.5,
        price=100000.0,
        fee=12.5,
        payload={'liquidity': 'maker'},
    )
    account = AccountEvent(
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
        event_type='funding',
        payload={'funding_fee': -3.5},
    )

    assert is_dataclass(order)
    assert is_dataclass(fill)
    assert is_dataclass(account)
    assert order.kind is EventKind.ORDER
    assert fill.kind is EventKind.FILL
    assert account.kind is EventKind.ACCOUNT
    assert fill.position_side == 'long'
    assert fill.fee == 12.5
    assert account.event_type == 'funding'
