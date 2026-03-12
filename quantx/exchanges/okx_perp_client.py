from __future__ import annotations

from typing import Any

from .base import ExchangeOrder, ExchangePosition
from .okx import OKXClient


class OKXPerpClient(OKXClient):
    """Perpetual contract OKX client for SWAP + cross + net mode flows."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = "https://www.okx.com",
        inst_type: str = "SWAP",
        max_retries: int = 2,
        retry_backoff_ms: int = 100,
    ):
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            base_url=base_url,
            inst_type=inst_type,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff_ms,
        )

    def place_order(self, order: ExchangeOrder) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instId": order.symbol.upper(),
            "tdMode": str(order.margin_mode or "cross").lower(),
            "side": order.side.lower(),
            "ordType": "limit" if order.price is not None else "market",
            "clOrdId": order.client_order_id,
            "sz": self._fmt(order.qty),
            "posSide": str(order.position_side or "net").lower(),
        }
        if order.price is not None:
            payload["px"] = self._fmt(order.price)
        if order.reduce_only:
            payload["reduceOnly"] = True
        return self._signed_request("POST", "/api/v5/trade/order", payload)

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.get_raw_open_orders(symbol)

    def get_raw_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"instType": self.inst_type}
        if symbol:
            params["instId"] = symbol.upper()
        data = self._signed_request("GET", "/api/v5/trade/orders-pending", params)
        return data.get("data", []) if isinstance(data, dict) else []

    def get_account_positions(self) -> list[ExchangePosition]:
        out: list[ExchangePosition] = []
        for row in self.get_raw_account_positions():
            qty = float(row.get("pos", 0.0) or 0.0)
            if abs(qty) <= 1e-12:
                continue
            out.append(
                ExchangePosition(
                    symbol=str(row.get("instId", "")).upper(),
                    qty=qty,
                    position_side=str(row.get("posSide", "net")).lower(),
                    margin_mode=str(row.get("mgnMode", "cross")).lower(),
                )
            )
        return out

    def get_raw_account_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"instType": self.inst_type}
        if symbol:
            params["instId"] = symbol.upper()
        data = self._signed_request("GET", "/api/v5/account/positions", params)
        return data.get("data", []) if isinstance(data, dict) else []

    def get_raw_account_snapshot(self) -> dict[str, Any]:
        data = self._signed_request("GET", "/api/v5/account/balance", {})
        rows = data.get("data", []) if isinstance(data, dict) else []
        return rows[0] if rows else {}

    def validate_account_mode(self) -> dict[str, str]:
        data = self._signed_request("GET", "/api/v5/account/config", {})
        rows = data.get("data", []) if isinstance(data, dict) else []
        row = rows[0] if rows else {}
        return {
            "product_type": self.inst_type.lower(),
            "margin_mode": str(row.get("acctLv", "cross")).lower(),
            "position_mode": str(row.get("posMode", "net_mode")).lower(),
        }
