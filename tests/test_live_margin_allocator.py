from __future__ import annotations

from quantx.exchanges.base import ExchangePosition, SymbolSpec
from quantx.live_margin_allocator import MarginAllocator, SymbolBudget
from quantx.live_service import LiveExecutionConfig, LiveExecutionService


class _BudgetExchange:
    def place_order(self, order):
        return {'ok': True, 'clientOrderId': order.client_order_id}

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, object]:
        return {'ok': True}

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, object]]:
        return []

    def get_account_positions(self) -> list[ExchangePosition]:
        return []

    def get_symbol_specs(self, symbols: list[str] | None = None) -> dict[str, SymbolSpec]:
        return {
            'BTC-USDT-SWAP': SymbolSpec(
                symbol='BTC-USDT-SWAP',
                tick_size=0.1,
                lot_size=0.001,
                min_qty=0.001,
                min_notional=5.0,
            )
        }


def test_margin_allocator_slices_total_margin_and_enforces_symbol_caps():
    allocator = MarginAllocator(total_margin=1000.0, max_symbol_weight=0.5)

    budgets = allocator.allocate(
        watchlist=('BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'),
        target_scores={'BTC-USDT-SWAP': 1.0, 'ETH-USDT-SWAP': 0.5, 'SOL-USDT-SWAP': 0.5},
    )

    assert round(sum(item.max_margin for item in budgets.values()), 8) <= 1000.0
    assert budgets['BTC-USDT-SWAP'].max_margin <= 500.0


def test_total_margin_budget_gate_rejects_orders_beyond_symbol_caps():
    service = LiveExecutionService(
        _BudgetExchange(),
        config=LiveExecutionConfig(dry_run=True, allowed_symbols=('BTC-USDT-SWAP',)),
    )
    service.sync_symbol_rules(['BTC-USDT-SWAP'])
    service.set_symbol_budgets(
        {
            'BTC-USDT-SWAP': SymbolBudget(
                symbol='BTC-USDT-SWAP',
                max_margin=100.0,
                max_notional=300.0,
                max_leverage=3.0,
            )
        }
    )

    result = service.execute_orders(
        [
            {
                'symbol': 'BTC-USDT-SWAP',
                'side': 'BUY',
                'qty': 1.0,
                'price': 400.0,
                'position_side': 'net',
                'metadata': {'required_margin': 150.0, 'max_leverage': 4.0},
            }
        ]
    )

    assert result['ok'] is False
    assert result['rejected'][0]['reason'] in {
        'symbol_margin_budget_exceeded',
        'symbol_notional_budget_exceeded',
        'symbol_leverage_budget_exceeded',
    }
