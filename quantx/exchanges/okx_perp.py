from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import ExchangeOrder
from ..runtime.events import AccountEvent, FillEvent, OrderEvent


class OKXPerpAdapter:
    exchange = 'okx'
    margin_mode = 'cross'

    def normalize_place_order_response(self, order: ExchangeOrder, response: dict[str, Any], *, ts: str) -> OrderEvent:
        rows = response.get('data', []) if isinstance(response, dict) else []
        row = rows[0] if rows else {}
        ok = str(row.get('sCode', '0')) == '0'
        return OrderEvent(
            symbol=str(row.get('instId') or order.symbol).upper(),
            exchange=self.exchange,
            ts=ts,
            client_order_id=str(row.get('clOrdId') or order.client_order_id),
            exchange_order_id=str(row.get('ordId') or order.client_order_id),
            status='acked' if ok else 'rejected',
            payload={
                'position_side': (order.position_side or 'net').lower(),
                'margin_mode': (order.margin_mode or self.margin_mode).lower(),
                'reason': str(row.get('sMsg', '')),
            },
        )

    def normalize_order_event(self, payload: dict[str, Any]) -> OrderEvent:
        state = self._map_order_state(str(payload.get('state', 'unknown')))
        return OrderEvent(
            symbol=str(payload.get('instId', '')).upper(),
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('uTime') or payload.get('cTime') or payload.get('ts')),
            client_order_id=str(payload.get('clOrdId', '')),
            exchange_order_id=str(payload.get('ordId', '')),
            status=state,
            payload={
                'position_side': str(payload.get('posSide', 'net')).lower(),
                'margin_mode': str(payload.get('tdMode', self.margin_mode)).lower(),
                'reason': str(payload.get('sMsg', '')) or str(payload.get('cancelSource', '')),
            },
        )

    def normalize_fill_event(self, payload: dict[str, Any]) -> FillEvent:
        return FillEvent(
            symbol=str(payload.get('instId', '')).upper(),
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('fillTime') or payload.get('uTime') or payload.get('ts')),
            client_order_id=str(payload.get('clOrdId', '')),
            exchange_order_id=str(payload.get('ordId', '')),
            trade_id=str(payload.get('tradeId', '')),
            side=str(payload.get('side', '')).lower(),
            position_side=str(payload.get('posSide', 'net')).lower(),
            qty=float(payload.get('fillSz', 0.0) or 0.0),
            price=float(payload.get('fillPx', 0.0) or 0.0),
            fee=float(payload.get('fillFee', 0.0) or 0.0),
            payload={
                'margin_mode': str(payload.get('tdMode', self.margin_mode)).lower(),
            },
        )

    def normalize_position_event(self, payload: dict[str, Any]) -> AccountEvent:
        return AccountEvent(
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('uTime') or payload.get('ts')),
            event_type='position_snapshot',
            payload={
                'symbol': str(payload.get('instId', '')).upper(),
                'position_side': str(payload.get('posSide', 'net')).lower(),
                'qty': float(payload.get('pos', 0.0) or 0.0),
                'avg_entry_price': float(payload.get('avgPx', 0.0) or 0.0),
                'margin_mode': str(payload.get('mgnMode', self.margin_mode)).lower(),
            },
        )

    def normalize_account_event(self, payload: dict[str, Any]) -> AccountEvent:
        return AccountEvent(
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('uTime') or payload.get('ts')),
            event_type='account_snapshot',
            payload={
                'currency': str(payload.get('ccy', 'USDT')).upper(),
                'equity': float(payload.get('eq', 0.0) or 0.0),
                'available_margin': float(payload.get('availEq', 0.0) or 0.0),
                'used_margin': float(payload.get('imr', 0.0) or 0.0),
                'maintenance_margin': float(payload.get('mmr', 0.0) or 0.0),
                'unrealized_pnl': float(payload.get('upl', 0.0) or 0.0),
            },
        )

    def normalize_funding_event(self, payload: dict[str, Any]) -> AccountEvent:
        return AccountEvent(
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('ts')),
            event_type='funding',
            payload={
                'symbol': str(payload.get('instId', '')).upper(),
                'position_side': str(payload.get('posSide', 'long')).lower(),
                'amount': float(payload.get('funding', 0.0) or 0.0),
            },
        )

    def normalize_open_orders(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    'symbol': str(row.get('instId', '')).upper(),
                    'clientOrderId': str(row.get('clOrdId', '')),
                    'exchangeOrderId': str(row.get('ordId', '')),
                    'status': self._map_order_state(str(row.get('state', 'unknown'))),
                    'position_side': str(row.get('posSide', 'net')).lower(),
                    'margin_mode': str(row.get('tdMode', self.margin_mode)).lower(),
                }
            )
        return out

    def normalize_positions(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            qty = float(row.get('pos', 0.0) or 0.0)
            if abs(qty) <= 1e-12:
                continue
            out.append(
                {
                    'symbol': str(row.get('instId', '')).upper(),
                    'position_side': str(row.get('posSide', 'net')).lower(),
                    'qty': qty,
                    'avg_entry_price': float(row.get('avgPx', 0.0) or 0.0),
                    'margin_mode': str(row.get('mgnMode', self.margin_mode)).lower(),
                }
            )
        return out

    def _map_order_state(self, state: str) -> str:
        mapping = {
            'live': 'working',
            'partially_filled': 'partially_filled',
            'filled': 'filled',
            'canceled': 'canceled',
            'mmp_canceled': 'canceled',
            'order_failed': 'rejected',
        }
        return mapping.get(state.lower(), 'rejected' if state else 'unknown')

    def _normalize_ts(self, value: Any) -> str:
        if value is None or value == '':
            return datetime.now(timezone.utc).isoformat()
        text = str(value)
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).isoformat()
        return text.replace('Z', '+00:00')

