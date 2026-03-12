from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any, Callable, Iterable


class OKXPrivateStreamTransport:
    def __init__(
        self,
        *,
        api_key: str = '',
        api_secret: str = '',
        passphrase: str = '',
        url: str = 'wss://ws.okx.com:8443/ws/v5/private',
        channels: tuple[str, ...] = ('orders', 'positions', 'account'),
        websocket_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.url = url
        self.channels = channels
        self.websocket_factory = websocket_factory
        self._socket: Any | None = None

    @property
    def is_connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self._socket is not None:
            return
        factory = self.websocket_factory or self._default_websocket_factory
        self._socket = factory(self.url)
        if self.api_key and self.api_secret and self.passphrase:
            self._socket.send(json.dumps(self.login_payload()))
        self._socket.send(json.dumps(self.subscribe_payload()))

    def iter_messages(self) -> Iterable[dict[str, Any]]:
        if self._socket is None:
            return []

        messages: list[dict[str, Any]] = []
        while True:
            raw = self._socket.recv()
            if not raw:
                break
            packet = json.loads(raw)
            if not isinstance(packet, dict):
                continue
            messages.extend(self._normalize_packet(packet))
        return messages

    def close(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.close()
        finally:
            self._socket = None

    def login_payload(self) -> dict[str, Any]:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        signature = self._sign(timestamp)
        return {
            'op': 'login',
            'args': [{
                'apiKey': self.api_key,
                'passphrase': self.passphrase,
                'timestamp': timestamp,
                'sign': signature,
            }],
        }

    def subscribe_payload(self) -> dict[str, Any]:
        return {
            'op': 'subscribe',
            'args': [{'channel': channel} for channel in self.channels],
        }

    def _normalize_packet(self, packet: dict[str, Any]) -> list[dict[str, Any]]:
        if 'type' in packet and 'payload' in packet:
            return [packet]
        if packet.get('event') in {'login', 'subscribe'}:
            return []
        arg = packet.get('arg', {}) if isinstance(packet.get('arg'), dict) else {}
        channel = str(arg.get('channel', '')).lower()
        data = packet.get('data', []) if isinstance(packet.get('data'), list) else []
        message_type = {
            'orders': 'order',
            'positions': 'position',
            'account': 'account',
        }.get(channel)
        if message_type is None:
            return []
        return [{'type': message_type, 'payload': row} for row in data if isinstance(row, dict)]

    def _sign(self, timestamp: str) -> str:
        message = f'{timestamp}GET/users/self/verify'.encode('utf-8')
        secret = self.api_secret.encode('utf-8')
        digest = hmac.new(secret, message, hashlib.sha256).digest()
        return base64.b64encode(digest).decode('utf-8')

    @staticmethod
    def _default_websocket_factory(url: str):
        from websocket import create_connection

        return create_connection(url)
