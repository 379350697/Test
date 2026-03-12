from __future__ import annotations

from quantx.runtime import build_reconcile_report


def test_reconcile_report_flags_position_and_margin_mismatch_without_rewriting_runtime_truth():
    runtime_snapshot = {
        'positions': {
            'BTC-USDT-SWAP': {
                'long': {
                    'qty': 1.0,
                    'avg_entry_price': 100.0,
                    'funding_total': -0.2,
                }
            }
        },
        'ledger': {
            'equity': 999.7,
            'available_margin': 899.7,
            'used_margin': 100.0,
            'maintenance_margin': 50.0,
        },
        'observed_exchange': {
            'positions': {
                'BTC-USDT-SWAP': {
                    'long': {
                        'qty': 2.0,
                        'avg_entry_price': 101.0,
                    }
                }
            },
            'account': {
                'equity': 980.0,
                'available_margin': 870.0,
                'used_margin': 110.0,
                'maintenance_margin': 55.0,
            },
        },
    }

    report = build_reconcile_report(runtime_snapshot)

    assert report['ok'] is False
    assert report['position_mismatches']['BTC-USDT-SWAP']['runtime_qty'] == 1.0
    assert report['position_mismatches']['BTC-USDT-SWAP']['exchange_qty'] == 2.0
    assert runtime_snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 1.0
