"""Binance REST client (spot) with signed endpoints and symbol rule sync."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .base import ExchangeOrder, ExchangePosition, SymbolSpec


class BinanceClient:
    """Minimal Binance client for order/account/rule operations."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.binance.com",
        recv_window: int = 5000,
        max_retries: int = 2,
        retry_backoff_ms: int = 100,
    ):
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms

    def place_order(self, order: ExchangeOrder) -> dict[str, Any]:
        payload = {
            "symbol": order.symbol.upper(),
            "side": order.side,
            "type": order.order_type.upper(),
            "quantity": self._fmt(order.qty),
            "newClientOrderId": order.client_order_id,
        }
        if order.price is not None:
            payload["price"] = self._fmt(order.price)
            payload["timeInForce"] = "GTC"
        return self._signed_request("POST", "/api/v3/order", payload)

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        payload = {"symbol": symbol.upper(), "origClientOrderId": client_order_id}
        return self._signed_request("DELETE", "/api/v3/order", payload)

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        payload = {"symbol": symbol.upper()} if symbol else {}
        data = self._signed_request("GET", "/api/v3/openOrders", payload)
        return data if isinstance(data, list) else []

    def get_account_positions(self) -> list[ExchangePosition]:
        payload = self._signed_request("GET", "/api/v3/account", {})
        if not isinstance(payload, dict):
            return []
        balances = payload.get("balances", [])
        out: list[ExchangePosition] = []
        for b in balances:
            free = float(b.get("free", 0.0))
            locked = float(b.get("locked", 0.0))
            qty = free + locked
            if abs(qty) > 1e-12:
                out.append(ExchangePosition(symbol=str(b.get("asset", "")), qty=qty))
        return out

    def get_symbol_specs(self, symbols: list[str] | None = None) -> dict[str, SymbolSpec]:
        data = self._public_request("GET", "/api/v3/exchangeInfo", {})
        wants = {s.upper() for s in symbols} if symbols else None

        specs: dict[str, SymbolSpec] = {}
        for item in data.get("symbols", []):
            symbol = str(item.get("symbol", "")).upper()
            if wants and symbol not in wants:
                continue

            tick_size = 0.0
            lot_size = 0.0
            min_qty = 0.0
            min_notional = 0.0
            for f in item.get("filters", []):
                ftype = f.get("filterType")
                if ftype == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0.0))
                elif ftype == "LOT_SIZE":
                    lot_size = float(f.get("stepSize", 0.0))
                    min_qty = float(f.get("minQty", 0.0))
                elif ftype in {"MIN_NOTIONAL", "NOTIONAL"}:
                    min_notional = float(f.get("minNotional", 0.0) or f.get("notional", 0.0))

            specs[symbol] = SymbolSpec(
                symbol=symbol,
                tick_size=tick_size,
                lot_size=lot_size,
                min_qty=min_qty,
                min_notional=min_notional,
            )
        return specs

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        payload = dict(params)
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = self.recv_window

        qs = urllib.parse.urlencode(payload)
        sig = hmac.new(self.api_secret, qs.encode("utf-8"), hashlib.sha256).hexdigest()
        payload["signature"] = sig
        return self._request(method, path, payload, signed=True)

    def _public_request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        data = self._request(method, path, params, signed=False)
        return data if isinstance(data, dict) else {"data": data}

    def _request(self, method: str, path: str, params: dict[str, Any], signed: bool) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}"
        if method in {"GET", "DELETE"} and query:
            url = f"{url}?{query}"

        body = None
        headers = {"User-Agent": "quantx/0.1"}
        if signed:
            headers["X-MBX-APIKEY"] = self.api_key
        if method in {"POST", "PUT"}:
            body = query.encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        return self._open_json_with_retry(req)

    def _open_json_with_retry(self, req: urllib.request.Request) -> Any:
        attempts = max(1, self.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self.retry_backoff_ms / 1000)
        if last_error is None:
            last_error = RuntimeError("binance_request_failed_without_exception")
        raise RuntimeError(f"binance_request_failed:{last_error}")

    @staticmethod
    def _fmt(v: float) -> str:
        return ("%.12f" % v).rstrip("0").rstrip(".")

    def as_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "recv_window": self.recv_window,
            "max_retries": self.max_retries,
            "retry_backoff_ms": self.retry_backoff_ms,
            "api_key_set": bool(self.api_key),
            "api_secret_set": bool(self.api_secret),
        }

    def __repr__(self) -> str:
        return f"BinanceClient({self.as_dict()})"
