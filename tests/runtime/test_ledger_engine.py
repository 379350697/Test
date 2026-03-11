from __future__ import annotations

import pytest

from quantx.runtime.events import AccountEvent, FillEvent, MarketEvent
from quantx.runtime.ledger_engine import LedgerEngine


def make_fill_event(*, side: str, position_side: str, qty: float, price: float, fee: float = 1.0) -> FillEvent:
    return FillEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        ts='2026-03-12T00:00:00+00:00',
        client_order_id=f'{position_side}-{side}-{qty}',
        exchange_order_id='oid-1',
        trade_id=f'{position_side}-{side}-{qty}-{price}',
        side=side,
        position_side=position_side,
        qty=qty,
        price=price,
        fee=fee,
        payload={},
    )


def make_mark_event(price: float) -> MarketEvent:
    return MarketEvent(
        symbol='BTC-USDT-SWAP',
        exchange='okx',
        channel='mark_price',
        ts='2026-03-12T00:01:00+00:00',
        payload={'price': price},
    )


def make_funding_event(amount: float, position_side: str = 'long') -> AccountEvent:
    return AccountEvent(
        exchange='okx',
        ts='2026-03-12T00:08:00+00:00',
        event_type='funding',
        payload={
            'symbol': 'BTC-USDT-SWAP',
            'position_side': position_side,
            'amount': amount,
        },
    )


def test_ledger_engine_tracks_separate_long_and_short_legs_for_same_symbol():
    engine = LedgerEngine(wallet_balance=1000.0)

    ledger = engine.apply_fill(make_fill_event(side='buy', position_side='long', qty=1.0, price=100.0))
    ledger = engine.apply_fill(make_fill_event(side='sell', position_side='short', qty=0.5, price=105.0, fee=0.5))

    long_leg = ledger.positions[('BTC-USDT-SWAP', 'long')]
    short_leg = ledger.positions[('BTC-USDT-SWAP', 'short')]

    assert long_leg.qty == pytest.approx(1.0)
    assert long_leg.avg_entry_price == pytest.approx(100.0)
    assert short_leg.qty == pytest.approx(0.5)
    assert short_leg.avg_entry_price == pytest.approx(105.0)
    assert ledger.wallet_balance == pytest.approx(998.5)



def test_ledger_engine_realizes_pnl_when_a_leg_is_closed():
    engine = LedgerEngine(wallet_balance=1000.0)

    engine.apply_fill(make_fill_event(side='buy', position_side='long', qty=2.0, price=100.0))
    ledger = engine.apply_fill(make_fill_event(side='sell', position_side='long', qty=1.0, price=110.0))
    leg = ledger.positions[('BTC-USDT-SWAP', 'long')]

    assert leg.qty == pytest.approx(1.0)
    assert leg.realized_pnl == pytest.approx(10.0)
    assert ledger.wallet_balance == pytest.approx(1008.0)



def test_ledger_engine_marks_unrealized_pnl_and_cross_margin_account_fields():
    engine = LedgerEngine(wallet_balance=1000.0, initial_margin_ratio=0.1, maintenance_margin_ratio=0.05)

    engine.apply_fill(make_fill_event(side='buy', position_side='long', qty=1.0, price=100.0, fee=0.0))
    engine.apply_fill(make_fill_event(side='sell', position_side='short', qty=1.0, price=110.0, fee=0.0))
    ledger = engine.apply_market_event(make_mark_event(price=105.0))

    long_leg = ledger.positions[('BTC-USDT-SWAP', 'long')]
    short_leg = ledger.positions[('BTC-USDT-SWAP', 'short')]

    assert long_leg.unrealized_pnl == pytest.approx(5.0)
    assert short_leg.unrealized_pnl == pytest.approx(5.0)
    assert ledger.equity == pytest.approx(1010.0)
    assert ledger.used_margin == pytest.approx(21.0)
    assert ledger.available_margin == pytest.approx(989.0)
    assert ledger.maintenance_margin == pytest.approx(10.5)
    assert ledger.risk_ratio == pytest.approx(10.5 / 1010.0)



def test_ledger_engine_applies_funding_without_bypassing_position_legs():
    engine = LedgerEngine(wallet_balance=1000.0)

    engine.apply_fill(make_fill_event(side='buy', position_side='long', qty=1.0, price=100.0, fee=0.0))
    ledger = engine.apply_account_event(make_funding_event(amount=-2.5))
    leg = ledger.positions[('BTC-USDT-SWAP', 'long')]

    assert leg.funding_total == pytest.approx(-2.5)
    assert ledger.wallet_balance == pytest.approx(997.5)


def test_runtime_risk_rejects_invalid_position_side_and_bad_reduce_only():
    from quantx.runtime.models import AccountLedger, OrderIntent, PositionLeg
    from quantx.runtime.runtime_risk import RuntimeRiskLimits, RuntimeRiskValidator

    ledger = AccountLedger(
        wallet_balance=1000.0,
        equity=1000.0,
        available_margin=800.0,
        used_margin=200.0,
        maintenance_margin=50.0,
        risk_ratio=0.05,
        positions={
            ('BTC-USDT-SWAP', 'long'): PositionLeg(
                symbol='BTC-USDT-SWAP',
                position_side='long',
                qty=1.0,
                avg_entry_price=100.0,
            )
        },
    )
    validator = RuntimeRiskValidator(RuntimeRiskLimits())

    bad_side = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='net',
        qty=0.2,
        price=101.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
    )
    ok, reason = validator.validate_intent(bad_side, ledger)
    assert not ok and reason == 'invalid_position_side'

    bad_reduce = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=0.2,
        price=101.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=True,
    )
    ok, reason = validator.validate_intent(bad_reduce, ledger)
    assert not ok and reason == 'reduce_only_would_increase_position'
