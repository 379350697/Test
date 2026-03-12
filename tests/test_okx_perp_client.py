from __future__ import annotations

from quantx.exchanges.base import ExchangeOrder
from quantx.exchanges.okx_perp_client import OKXPerpClient


class _OKXPerpStub(OKXPerpClient):
    def __init__(self):
        super().__init__("k", "s", "p")
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.public_calls: list[tuple[str, str, dict[str, object]]] = []

    def _signed_request(self, method: str, path: str, params: dict[str, object]):  # type: ignore[override]
        self.calls.append((method, path, params))
        if path == "/api/v5/trade/orders-pending":
            return {"data": [{"instId": "BTC-USDT-SWAP", "clOrdId": "cid-1"}]}
        if path == "/api/v5/account/positions":
            return {"data": [{"instId": "BTC-USDT-SWAP", "posSide": "net", "mgnMode": "cross"}]}
        if path == "/api/v5/account/balance":
            return {"data": [{"details": [{"ccy": "USDT", "availEq": "800"}]}]}
        return {"data": [{"instId": "BTC-USDT-SWAP", "clOrdId": "cid-1", "ordId": "oid-1", "sCode": "0"}]}

    def _public_request(self, method: str, path: str, params: dict[str, str]):  # type: ignore[override]
        self.public_calls.append((method, path, params))
        return {
            "data": [
                ["1710000300000", "100.0", "102.0", "99.0", "101.5", "12.5", "0", "0", "1"],
                ["1710000000000", "95.0", "101.0", "94.0", "100.0", "8.0", "0", "0", "0"],
            ]
        }


def test_okx_perp_client_places_cross_net_swap_orders_and_exposes_raw_snapshots():
    client = _OKXPerpStub()
    order = ExchangeOrder(
        client_order_id="cid-1",
        symbol="BTC-USDT-SWAP",
        side="BUY",
        qty=1.0,
        order_type="MARKET",
        price=None,
        position_side="net",
        margin_mode="cross",
        reduce_only=False,
    )

    client.place_order(order)
    open_orders = client.get_raw_open_orders("BTC-USDT-SWAP")
    positions = client.get_raw_account_positions("BTC-USDT-SWAP")
    account = client.get_raw_account_snapshot()

    assert client.calls[0][1] == "/api/v5/trade/order"
    assert client.calls[0][2]["tdMode"] == "cross"
    assert client.calls[0][2]["instId"] == "BTC-USDT-SWAP"
    assert open_orders[0]["instId"] == "BTC-USDT-SWAP"
    assert positions[0]["instId"] == "BTC-USDT-SWAP"
    assert account["details"][0]["ccy"] == "USDT"


def test_okx_perp_client_candle_fetch_uses_market_endpoint_and_normalizes_rows():
    client = _OKXPerpStub()

    candles = client.get_candles("BTC-USDT-SWAP", bar="5m", limit=2)

    assert client.public_calls == [
        (
            "GET",
            "/api/v5/market/candles",
            {"instId": "BTC-USDT-SWAP", "bar": "5m", "limit": "2"},
        )
    ]
    assert candles == [
        {
            "ts": "1710000300000",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.5,
            "volume": 12.5,
            "confirmed": True,
        },
        {
            "ts": "1710000000000",
            "open": 95.0,
            "high": 101.0,
            "low": 94.0,
            "close": 100.0,
            "volume": 8.0,
            "confirmed": False,
        },
    ]
