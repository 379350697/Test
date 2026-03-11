from __future__ import annotations

from quantx.bootstrap import bootstrap_recover_and_reconcile
from quantx.oms import JsonlOMSStore, OMSOrder, OrderManager


class _StubService:
    def __init__(self, snapshot: dict):
        self._snapshot = snapshot

    def reconcile(self, symbol: str | None = None) -> dict:
        return self._snapshot


def test_bootstrap_recover_and_reconcile_ok(tmp_path):
    store = JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl"))
    om = OrderManager(initial_cash=1000.0, store=store)
    om.submit(OMSOrder(order_id="o-filled", symbol="BTCUSDT", side="BUY", qty=0.1))
    om.fill("o-filled", fill_qty=0.1, fill_price=100.0)
    om.submit(OMSOrder(order_id="o-open", symbol="BTCUSDT", side="BUY", qty=0.2))

    svc = _StubService(
        {
            "open_orders": [{"clientOrderId": "o-open", "symbol": "BTCUSDT"}],
            "positions": [{"symbol": "BTCUSDT", "qty": 0.1}],
            "symbol_rules": {},
        }
    )

    rep = bootstrap_recover_and_reconcile(service=svc, oms_store=store, initial_cash=1000.0)
    assert rep["ok"] is True
    assert rep["recovered_orders"] == 2
    assert rep["recovered_working_orders"] == 1
    assert rep["position_diffs"] == {}
    assert rep["missing_on_exchange"] == []
    assert rep["unmanaged_on_exchange"] == []


def test_bootstrap_recover_and_reconcile_detects_mismatch(tmp_path):
    store = JsonlOMSStore(str(tmp_path / "oms" / "events.jsonl"))
    om = OrderManager(initial_cash=1000.0, store=store)
    om.submit(OMSOrder(order_id="o-open", symbol="BTCUSDT", side="BUY", qty=0.2))

    svc = _StubService(
        {
            "open_orders": [{"clientOrderId": "remote-only", "symbol": "BTCUSDT"}],
            "positions": [{"symbol": "BTCUSDT", "qty": 0.5}],
            "symbol_rules": {},
        }
    )

    rep = bootstrap_recover_and_reconcile(service=svc, oms_store=store, initial_cash=1000.0)
    assert rep["ok"] is False
    assert "BTCUSDT" in rep["position_diffs"]
    assert rep["missing_on_exchange"] == ["o-open"]
    assert rep["unmanaged_on_exchange"] == ["remote-only"]
    assert "position_mismatch_detected" in rep["notes"]


def test_bootstrap_recover_and_reconcile_prefers_runtime_positions_over_raw_assets(monkeypatch):
    class _RecoveredOrderManager:
        def __init__(self):
            self.ledger = type('Ledger', (), {'positions': {'BTC-USDT-SWAP': 0.1}})()

        def list_working_order_ids(self):
            return []

        def list_orders(self):
            return ['o-filled']

    monkeypatch.setattr(OrderManager, 'recover', staticmethod(lambda store, initial_cash=0.0: _RecoveredOrderManager()))

    svc = _StubService(
        {
            'open_orders': [],
            'positions': [{'symbol': 'USDT', 'qty': 1000.0}],
            'runtime_positions': [{'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'qty': 0.1}],
            'symbol_rules': {},
        }
    )

    rep = bootstrap_recover_and_reconcile(service=svc, oms_store=object(), initial_cash=1000.0)
    assert rep['ok'] is True
    assert rep['exchange_positions']['BTC-USDT-SWAP'] == 0.1
    assert rep['position_diffs'] == {}
