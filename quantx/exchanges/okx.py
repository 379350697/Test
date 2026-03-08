"""OKX REST client (v5) with signed endpoints and instrument rule sync."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .base import ExchangeOrder, ExchangePosition, SymbolSpec


class OKXClient:
    """Minimal OKX client for order/account/rule operations."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = "https://www.okx.com",
        inst_type: str = "SPOT",
        max_retries: int = 2,
        retry_backoff_ms: int = 100,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self.inst_type = inst_type
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms

    def place_order(self, order: ExchangeOrder) -> dict:
        payload = {
            "instId": order.symbol.upper(),
            "tdMode": "cash",
            "side": order.side.lower(),
            "ordType": "limit" if order.price is not None else "market",
            "clOrdId": order.client_order_id,
            "sz": self._fmt(order.qty),
        }
        if order.price is not None:
            payload["px"] = self._fmt(order.price)
        return self._signed_request("POST", "/api/v5/trade/order", payload)

    def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        payload = {"instId": symbol.upper(), "clOrdId": client_order_id}
        return self._signed_request("POST", "/api/v5/trade/cancel-order", payload)

    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        params = {"instType": self.inst_type}
        if symbol:
            params["instId"] = symbol.upper()
        data = self._signed_request("GET", "/api/v5/trade/orders-pending", params)
        return data.get("data", []) if isinstance(data, dict) else []

    def get_account_positions(self) -> list[ExchangePosition]:
        data = self._signed_request("GET", "/api/v5/account/balance", {})
        rows = data.get("data", []) if isinstance(data, dict) else []

        out: list[ExchangePosition] = []
        for row in rows:
            for details in row.get("details", []):
                ccy = str(details.get("ccy", ""))
                avail = float(details.get("availBal", 0.0) or 0.0)
                frozen = float(details.get("frozenBal", 0.0) or 0.0)
                qty = avail + frozen
                if ccy and abs(qty) > 1e-12:
                    out.append(ExchangePosition(symbol=ccy, qty=qty))
        return out

    def get_symbol_specs(self, symbols: list[str] | None = None) -> dict[str, SymbolSpec]:
        data = self._public_request("GET", "/api/v5/public/instruments", {"instType": self.inst_type})
        wants = {s.upper() for s in symbols} if symbols else None

        specs: dict[str, SymbolSpec] = {}
        for inst in data.get("data", []):
            symbol = str(inst.get("instId", "")).upper()
            if wants and symbol not in wants:
                continue

            lot_size = float(inst.get("lotSz", 0.0) or 0.0)
            tick_size = float(inst.get("tickSz", 0.0) or 0.0)
            min_qty = float(inst.get("minSz", 0.0) or 0.0)
            min_notional = float(inst.get("minNotional", 0.0) or 0.0)
            specs[symbol] = SymbolSpec(
                symbol=symbol,
                tick_size=tick_size,
                lot_size=lot_size,
                min_qty=min_qty,
                min_notional=min_notional,
            )
        return specs

    def _public_request(self, method: str, path: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        req = urllib.request.Request(url, headers={"User-Agent": "quantx/0.1"}, method=method)
        payload = self._open_json_with_retry(req)
        return payload if isinstance(payload, dict) else {"data": payload}

    def _signed_request(self, method: str, path: str, params: dict[str, str] | dict) -> dict:
        ts = self._ts()
        query = ""
        body = ""

        if method == "GET":
            query = urllib.parse.urlencode(params)
            request_path = f"{path}?{query}" if query else path
        else:
            request_path = path
            body = json.dumps(params, separators=(",", ":"))

        prehash = f"{ts}{method}{request_path}{body}"
        sign = self._sign(prehash)

        headers = {
            "User-Agent": "quantx/0.1",
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }

        url = f"{self.base_url}{path}"
        data = None
        if method == "GET" and query:
            url = f"{url}?{query}"
        elif method != "GET":
            headers["Content-Type"] = "application/json"
            data = body.encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        payload = self._open_json_with_retry(req)
        return payload if isinstance(payload, dict) else {"data": payload}

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
            last_error = RuntimeError("okx_request_failed_without_exception")
        raise RuntimeError(f"okx_request_failed:{last_error}")

    def _sign(self, prehash: str) -> str:
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    @staticmethod
    def _fmt(v: float) -> str:
        return ("%.12f" % v).rstrip("0").rstrip(".")

    @staticmethod
    def _ts() -> str:
        return f"{time.time():.3f}"

    def as_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "inst_type": self.inst_type,
            "max_retries": self.max_retries,
            "retry_backoff_ms": self.retry_backoff_ms,
            "api_key_set": bool(self.api_key),
            "api_secret_set": bool(self.api_secret),
            "passphrase_set": bool(self.passphrase),
        }

    def __repr__(self) -> str:
        return f"OKXClient({self.as_dict()})"
