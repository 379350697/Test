from dataclasses import is_dataclass

from quantx.runtime.events import (
    AccountEvent,
    EventKind,
    FillEvent,
    MarketEvent,
    OrderEvent,
)
from quantx.runtime.models import AccountLedger, OrderIntent, PositionLeg, TrackedOrder


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


def test_model_order_intent_captures_derivatives_order_shape():
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=2.0,
        price=100500.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
    )

    assert is_dataclass(intent)
    assert intent.position_side == 'long'
    assert intent.reduce_only is False
    assert intent.order_type == 'limit'


def test_model_order_intent_carries_strategy_trace_metadata():
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100000.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
        intent_id='intent-1',
        strategy_id='scalp-v1',
        signal_id='sig-1',
        reason='breakout_retest',
        created_ts='2026-03-12T00:00:00+00:00',
        tags=('scalp', 'event'),
    )
    tracked = TrackedOrder(
        client_order_id='cid-1',
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        order_type='limit',
        time_in_force='gtc',
        strategy_id='scalp-v1',
        intent_id='intent-1',
    )

    assert intent.strategy_id == 'scalp-v1'
    assert intent.intent_id == 'intent-1'
    assert intent.tags == ('scalp', 'event')
    assert tracked.strategy_id == 'scalp-v1'
    assert tracked.intent_id == 'intent-1'


def test_model_position_leg_uses_symbol_and_position_side_key():
    leg = PositionLeg(symbol='BTC-USDT-SWAP', position_side='short')

    assert is_dataclass(leg)
    assert leg.key == ('BTC-USDT-SWAP', 'short')
    assert leg.qty == 0.0
    assert leg.avg_entry_price == 0.0


def test_model_account_ledger_tracks_cross_margin_fields():
    ledger = AccountLedger(
        wallet_balance=1000.0,
        equity=1025.0,
        available_margin=700.0,
        used_margin=250.0,
        maintenance_margin=40.0,
        risk_ratio=0.039,
    )
    tracked = TrackedOrder(
        client_order_id='cid-1',
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=2.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
    )

    assert is_dataclass(ledger)
    assert is_dataclass(tracked)
    assert ledger.available_margin == 700.0
    assert ledger.maintenance_margin == 40.0
    assert tracked.position_side == 'long'
    assert tracked.status == 'intent_created'
