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
