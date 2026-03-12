from __future__ import annotations

from quantx.runtime import (
    AccountEvent,
    FillEvent,
    LiveRuntimeCoordinator,
    OrderEvent,
    OrderIntent,
    RuntimeReplayStore,
    RuntimeSession,
)


def test_live_runtime_coordinator_persists_submit_fill_and_funding_events(tmp_path):
    store = RuntimeReplayStore(str(tmp_path / 'runtime' / 'events.jsonl'))
    coordinator = LiveRuntimeCoordinator(
        session=RuntimeSession(mode='live', wallet_balance=1000.0),
        replay_store=store,
    )
    intent = OrderIntent(
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

    coordinator.submit_intents([intent], exchange='okx', ts='2026-03-12T00:00:00+00:00')
    coordinator.apply_event(
        OrderEvent(
            symbol='BTC-USDT-SWAP',
            exchange='okx',
            ts='2026-03-12T00:00:01+00:00',
            client_order_id='cid-1',
            exchange_order_id='oid-1',
            status='acked',
            payload={},
        )
    )
    coordinator.apply_event(
        FillEvent(
            symbol='BTC-USDT-SWAP',
            exchange='okx',
            ts='2026-03-12T00:00:02+00:00',
            client_order_id='cid-1',
            exchange_order_id='oid-1',
            trade_id='tid-1',
            side='buy',
            position_side='long',
            qty=1.0,
            price=100.0,
            fee=0.1,
            payload={},
        )
    )
    coordinator.apply_event(
        AccountEvent(
            exchange='okx',
            ts='2026-03-12T08:00:00+00:00',
            event_type='funding',
            payload={'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'amount': -0.2},
        )
    )

    rows, invalid = store.load()
    snapshot = coordinator.snapshot()

    assert invalid == 0
    assert [row['kind'] for row in rows] == [
        'order_event',
        'order_event',
        'order_event',
        'order_event',
        'fill_event',
        'account_event',
    ]
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2

