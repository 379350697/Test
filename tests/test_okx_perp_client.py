from __future__ import annotations

from quantx.exchanges.base import ExchangeOrder
from quantx.exchanges.okx_perp_client import OKXPerpClient


class _OKXPerpStub(OKXPerpClient):
    def __init__(self):
        super().__init__("k", "s", "p")
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def _signed_request(self, method: str, path: str, params: dict[str, object]):  # type: ignore[override]
        self.calls.append((method, path, params))
        if path == "/api/v5/trade/orders-pending":
            return {"data": [{"instId": "BTC-USDT-SWAP", "clOrdId": "cid-1"}]}
        if path == "/api/v5/account/positions":
            return {"data": [{"instId": "BTC-USDT-SWAP", "posSide": "net", "mgnMode": "cross"}]}
        if path == "/api/v5/account/balance":
            return {"data": [{"details": [{"ccy": "USDT", "availEq": "800"}]}]}
        return {"data": [{"instId": "BTC-USDT-SWAP", "clOrdId": "cid-1", "ordId": "oid-1", "sCode": "0"}]}


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
