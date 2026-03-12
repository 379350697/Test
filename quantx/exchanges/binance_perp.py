from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import ExchangeOrder
from ..runtime.events import AccountEvent, FillEvent, MarketEvent, OrderEvent


class BinancePerpAdapter:
    exchange = 'binance'
    margin_mode = 'cross'

    def normalize_place_order_response(self, order: ExchangeOrder, response: dict[str, Any], *, ts: str) -> OrderEvent:
        status = self._map_order_state(str(response.get('status', 'NEW')))
        return OrderEvent(
            symbol=str(response.get('symbol') or order.symbol).upper(),
            exchange=self.exchange,
            ts=ts,
            client_order_id=str(response.get('clientOrderId') or order.client_order_id),
            exchange_order_id=self._string_or_none(response.get('orderId')) or order.client_order_id,
            status=status,
            payload={
                'position_side': (order.position_side or 'net').lower(),
                'margin_mode': (order.margin_mode or self.margin_mode).lower(),
                'reason': str(response.get('msg', '') or response.get('code', '')),
            },
        )

    def normalize_order_event(self, payload: dict[str, Any]) -> OrderEvent:
        order = payload.get('o', payload)
        return OrderEvent(
            symbol=str(order.get('s', '')).upper(),
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('E') or order.get('E') or order.get('T') or order.get('updateTime')),
            client_order_id=str(order.get('c') or order.get('clientOrderId') or ''),
            exchange_order_id=self._string_or_none(order.get('i') or order.get('orderId')),
            status=self._map_order_state(str(order.get('X') or order.get('status') or 'NEW')),
            payload={
                'position_side': str(order.get('ps', 'net')).lower(),
                'margin_mode': str(order.get('mt') or order.get('marginType') or self.margin_mode).lower(),
                'order_type': str(order.get('ot') or order.get('o') or order.get('type') or '').lower(),
                'reason': str(order.get('r') or order.get('rejectReason') or ''),
            },
        )

    def normalize_fill_event(self, payload: dict[str, Any]) -> FillEvent:
        order = payload.get('o', payload)
        return FillEvent(
            symbol=str(order.get('s', '')).upper(),
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('E') or order.get('E') or order.get('T') or order.get('updateTime')),
            client_order_id=str(order.get('c') or order.get('clientOrderId') or ''),
            exchange_order_id=self._string_or_none(order.get('i') or order.get('orderId')),
            trade_id=self._string_or_none(order.get('t') or order.get('tradeId')) or '',
            side=str(order.get('S') or order.get('side') or '').lower(),
            position_side=str(order.get('ps', 'net')).lower(),
            qty=float(order.get('l') or order.get('lastFilledQty') or 0.0),
            price=float(order.get('L') or order.get('lastFilledPrice') or 0.0),
            fee=float(order.get('n') or order.get('commission') or 0.0),
            payload={
                'fee_asset': str(order.get('N') or order.get('commissionAsset') or '').upper(),
                'margin_mode': str(order.get('mt') or order.get('marginType') or self.margin_mode).lower(),
            },
        )

    def normalize_position_event(self, payload: dict[str, Any], *, ts: str | None = None) -> AccountEvent:
        return AccountEvent(
            exchange=self.exchange,
            ts=ts or self._normalize_ts(payload.get('E') or payload.get('updateTime') or payload.get('T')),
            event_type='position',
            payload={
                'symbol': str(payload.get('s', '')).upper(),
                'position_side': str(payload.get('ps', 'net')).lower(),
                'qty': float(payload.get('pa', 0.0) or 0.0),
                'avg_entry_price': float(payload.get('ep', 0.0) or 0.0),
                'margin_mode': str(payload.get('mt') or payload.get('marginType') or self.margin_mode).lower(),
                'unrealized_pnl': float(payload.get('up', 0.0) or 0.0),
            },
        )

    def normalize_account_event(self, payload: dict[str, Any]) -> AccountEvent:
        account = payload.get('a', payload)
        balances = account.get('B', []) if isinstance(account, dict) else []
        balance = self._pick_balance_row(balances)
        equity = float(balance.get('wb', 0.0) or 0.0)
        available = float(balance.get('cw', 0.0) or 0.0)
        return AccountEvent(
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('E') or account.get('E') or account.get('T')),
            event_type='account',
            payload={
                'currency': str(balance.get('a', 'USDT')).upper(),
                'equity': equity,
                'available_margin': available,
                'used_margin': max(equity - available, 0.0),
                'maintenance_margin': float(account.get('mm', 0.0) or 0.0),
                'unrealized_pnl': sum(float(row.get('up', 0.0) or 0.0) for row in account.get('P', [])),
                'reason': str(account.get('m', '')),
            },
        )

    def normalize_depth_event(self, payload: dict[str, Any]) -> MarketEvent:
        data = payload.get('data', payload)
        return MarketEvent(
            symbol=str(data.get('s', '')).upper(),
            exchange=self.exchange,
            channel='depth',
            ts=self._normalize_ts(data.get('E') or data.get('T') or payload.get('E')),
            payload={
                'bids': [self._normalize_level(level) for level in data.get('b', [])],
                'asks': [self._normalize_level(level) for level in data.get('a', [])],
                'stream': str(payload.get('stream', '')),
            },
        )

    def normalize_open_orders(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    'symbol': str(row.get('symbol') or row.get('s') or '').upper(),
                    'clientOrderId': str(row.get('clientOrderId') or row.get('c') or ''),
                    'exchangeOrderId': self._string_or_none(row.get('orderId') or row.get('i')),
                    'status': self._map_order_state(str(row.get('status') or row.get('X') or 'NEW')),
                    'position_side': str(row.get('positionSide') or row.get('ps') or 'net').lower(),
                    'margin_mode': str(row.get('marginType') or row.get('mt') or self.margin_mode).lower(),
                }
            )
        return out

    def normalize_positions(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            qty = float(row.get('positionAmt') or row.get('pa') or 0.0)
            if abs(qty) <= 1e-12:
                continue
            out.append(
                {
                    'symbol': str(row.get('symbol') or row.get('s') or '').upper(),
                    'position_side': str(row.get('positionSide') or row.get('ps') or 'net').lower(),
                    'qty': qty,
                    'avg_entry_price': float(row.get('entryPrice') or row.get('ep') or 0.0),
                    'margin_mode': str(row.get('marginType') or row.get('mt') or self.margin_mode).lower(),
                }
            )
        return out

    def _map_order_state(self, status: str) -> str:
        mapping = {
            'NEW': 'acked',
            'PARTIALLY_FILLED': 'partially_filled',
            'FILLED': 'filled',
            'CANCELED': 'canceled',
            'EXPIRED': 'expired',
            'REJECTED': 'rejected',
            'EXPIRED_IN_MATCH': 'expired',
        }
        return mapping.get(status.upper(), 'rejected' if status else 'unknown')

    def _pick_balance_row(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        for row in rows:
            if str(row.get('a', '')).upper() == 'USDT':
                return row
        return rows[0] if rows else {}

    def _normalize_level(self, level: list[Any] | tuple[Any, ...]) -> list[float]:
        price = float(level[0]) if len(level) > 0 else 0.0
        qty = float(level[1]) if len(level) > 1 else 0.0
        return [price, qty]

    def _normalize_ts(self, value: Any) -> str:
        if value is None or value == '':
            return datetime.now(timezone.utc).isoformat()
        text = str(value)
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).isoformat()
        return text.replace('Z', '+00:00')

    def _string_or_none(self, value: Any) -> str | None:
        if value is None or value == '':
            return None
        return str(value)
