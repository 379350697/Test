from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.error
import urllib.request

from quantx.exchanges.base import ExchangeClient, ExchangeOrder
from quantx.exchanges.binance import BinanceClient
from quantx.exchanges.okx import OKXClient


class _BinanceStub(BinanceClient):
    def __init__(self):
        super().__init__("k", "s")
        self.calls: list[tuple[str, str, dict, bool]] = []

    def _request(self, method: str, path: str, params: dict, signed: bool):  # type: ignore[override]
        self.calls.append((method, path, params, signed))
        if path == "/api/v3/account":
            return {"balances": [{"asset": "BTC", "free": "0.1", "locked": "0.2"}]}
        if path == "/api/v3/exchangeInfo":
            return {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                        ],
                    }
                ]
            }
        if path == "/api/v3/openOrders":
            return [{"symbol": "BTCUSDT"}]
        return {"ok": True}


class _OKXStub(OKXClient):
    def __init__(self):
        super().__init__("k", "s", "p")
        self.calls: list[tuple[str, str, dict]] = []

    def _signed_request(self, method: str, path: str, params: dict):  # type: ignore[override]
        self.calls.append((method, path, params))
        if path == "/api/v5/account/balance":
            return {"data": [{"details": [{"ccy": "USDT", "availBal": "10", "frozenBal": "1"}]}]}
        if path == "/api/v5/trade/orders-pending":
            return {"data": [{"instId": "BTC-USDT"}]}
        return {"data": []}

    def _public_request(self, method: str, path: str, params: dict):  # type: ignore[override]
        return {
            "data": [
                {"instId": "BTC-USDT", "lotSz": "0.0001", "tickSz": "0.1", "minSz": "0.0002", "minNotional": "5"}
            ]
        }


def test_binance_client_endpoints_and_specs():
    client = _BinanceStub()
    order = ExchangeOrder(
        client_order_id="c1",
        symbol="btcusdt",
        side="BUY",
        qty=0.25,
        order_type="LIMIT",
        price=100.0,
    )

    client.place_order(order)
    client.cancel_order("btcusdt", "c1")
    opens = client.get_open_orders("btcusdt")
    pos = client.get_account_positions()
    specs = client.get_symbol_specs(["BTCUSDT"])

    assert isinstance(client, ExchangeClient)
    assert len(opens) == 1
    assert pos[0].symbol == "BTC" and abs(pos[0].qty - 0.3) < 1e-12
    assert specs["BTCUSDT"].tick_size == 0.1
    assert any(path == "/api/v3/order" for _, path, _, _ in client.calls)


def test_okx_client_endpoints_and_signing():
    client = _OKXStub()
    order = ExchangeOrder(
        client_order_id="c2",
        symbol="btc-usdt",
        side="SELL",
        qty=0.15,
        order_type="MARKET",
    )

    client.place_order(order)
    client.cancel_order("btc-usdt", "c2")
    opens = client.get_open_orders("btc-usdt")
    pos = client.get_account_positions()
    specs = client.get_symbol_specs(["BTC-USDT"])

    assert isinstance(client, ExchangeClient)
    assert len(opens) == 1
    assert pos[0].symbol == "USDT" and abs(pos[0].qty - 11.0) < 1e-12
    assert specs["BTC-USDT"].min_qty == 0.0002

    prehash = "1700000000.000GET/api/v5/account/balance"
    expected = base64.b64encode(hmac.new(b"s", prehash.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
    assert client._sign(prehash) == expected


class _DummyResp:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_binance_open_json_with_retry(monkeypatch):
    client = BinanceClient("k", "s", max_retries=2, retry_backoff_ms=1)
    req = urllib.request.Request("https://example.com", method="GET")

    calls = {"n": 0}

    def fake_urlopen(_req, timeout=20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("temporary")
        return _DummyResp('{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    payload = client._open_json_with_retry(req)
    assert payload["ok"] is True
    assert calls["n"] == 2


def test_okx_open_json_with_retry_exhausted(monkeypatch):
    client = OKXClient("k", "s", "p", max_retries=1, retry_backoff_ms=1)
    req = urllib.request.Request("https://example.com", method="GET")

    def fake_urlopen(_req, timeout=20):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        client._open_json_with_retry(req)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "okx_request_failed" in str(exc)
