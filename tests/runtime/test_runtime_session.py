from __future__ import annotations

from quantx.runtime.events import AccountEvent, FillEvent, OrderEvent
from quantx.runtime.models import OrderIntent
from quantx.runtime.session import RuntimeSession


def test_runtime_session_submits_intents_and_records_state_sequence():
    session = RuntimeSession(mode='paper', wallet_balance=1000.0)
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
        strategy_id='dummy-event',
    )

    events = session.submit_intents([intent], exchange='paper', ts='2026-03-12T00:00:00+00:00')
    snapshot = session.snapshot()

    assert any(getattr(ev, 'status', None) == 'risk_accepted' for ev in events)
    assert list(snapshot['order_state_sequences'].values())[0][0] == 'intent_created'
    assert snapshot['orders'][0]['status'] == 'submitted'


def test_runtime_session_rejects_bad_reduce_only_before_submission():
    session = RuntimeSession(mode='paper', wallet_balance=1000.0)
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=True,
    )

    events = session.submit_intents([intent], exchange='paper', ts='2026-03-12T00:00:00+00:00')
    snapshot = session.snapshot()

    assert events[-1].status == 'rejected'
    assert snapshot['orders'][0]['status'] == 'rejected'


def test_runtime_session_snapshot_tracks_nested_positions_and_ledger_fields():
    session = RuntimeSession(mode='paper', wallet_balance=1000.0)
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
    )

    session.submit_intents([intent], exchange='paper', ts='2026-03-12T00:00:00+00:00')
    events = [
        OrderEvent(
            symbol='BTC-USDT-SWAP',
            exchange='paper',
            ts='2026-03-12T00:00:01+00:00',
            client_order_id='paper-1',
            exchange_order_id='paper-1',
            status='acked',
            payload={},
        ),
        OrderEvent(
            symbol='BTC-USDT-SWAP',
            exchange='paper',
            ts='2026-03-12T00:00:02+00:00',
            client_order_id='paper-1',
            exchange_order_id='paper-1',
            status='working',
            payload={},
        ),
        FillEvent(
            symbol='BTC-USDT-SWAP',
            exchange='paper',
            ts='2026-03-12T00:00:03+00:00',
            client_order_id='paper-1',
            exchange_order_id='paper-1',
            trade_id='trade-1',
            side='buy',
            position_side='long',
            qty=1.0,
            price=100.0,
            fee=0.0,
            payload={},
        ),
    ]
    session.apply_events(events)
    snapshot = session.snapshot()

    assert snapshot['ledger']['wallet_balance'] == 1000.0
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 1.0
    assert snapshot['orders'][0]['filled_qty'] == 1.0
    assert snapshot['orders'][0]['status'] == 'filled'


def test_runtime_session_books_funding_without_rewriting_truth_from_position_snapshot():
    session = RuntimeSession(mode='live', wallet_balance=1000.0)
    session.submit_intents(
        [
            OrderIntent(
                symbol='BTC-USDT-SWAP',
                side='buy',
                position_side='long',
                qty=1.0,
                price=100.0,
                order_type='market',
                time_in_force='ioc',
                reduce_only=False,
                intent_id='cid-1',
            )
        ],
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
    )
    session.apply_events(
        [
            OrderEvent(
                symbol='BTC-USDT-SWAP',
                exchange='okx',
                ts='2026-03-12T00:00:00+00:00',
                client_order_id='cid-1',
                exchange_order_id='oid-1',
                status='acked',
                payload={},
            ),
            FillEvent(
                symbol='BTC-USDT-SWAP',
                exchange='okx',
                ts='2026-03-12T00:00:01+00:00',
                client_order_id='cid-1',
                exchange_order_id='oid-1',
                trade_id='tid-1',
                side='buy',
                position_side='long',
                qty=1.0,
                price=100.0,
                fee=0.1,
                payload={},
            ),
            AccountEvent(
                exchange='okx',
                ts='2026-03-12T08:00:00+00:00',
                event_type='funding',
                payload={'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'amount': -0.2},
            ),
            AccountEvent(
                exchange='okx',
                ts='2026-03-12T08:00:01+00:00',
                event_type='position_snapshot',
                payload={
                    'symbol': 'BTC-USDT-SWAP',
                    'position_side': 'long',
                    'qty': 2.0,
                    'avg_entry_price': 101.0,
                },
            ),
        ]
    )

    snapshot = session.snapshot()

    assert snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 1.0
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
    assert snapshot['observed_exchange']['positions']['BTC-USDT-SWAP']['long']['qty'] == 2.0



