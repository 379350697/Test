from __future__ import annotations

from quantx.runtime.private_stream import PrivateStreamSupervisor


def test_private_stream_supervisor_marks_stream_stale_after_heartbeat_timeout():
    supervisor = PrivateStreamSupervisor(stale_after_s=30, reconnect_backoff_s=1)
    supervisor.mark_connected('2026-03-12T00:00:00+00:00')
    supervisor.mark_message('2026-03-12T00:00:05+00:00')

    snapshot = supervisor.snapshot(now_ts='2026-03-12T00:01:00+00:00')

    assert snapshot['state'] == 'stale'
    assert snapshot['gap_detected'] is True


def test_private_stream_supervisor_requires_reconcile_after_disconnect_and_reconnect():
    supervisor = PrivateStreamSupervisor(stale_after_s=30, reconnect_backoff_s=1)
    supervisor.mark_connected('2026-03-12T00:00:00+00:00')
    supervisor.mark_disconnect('2026-03-12T00:00:10+00:00', reason='socket_closed')
    supervisor.mark_connected('2026-03-12T00:00:20+00:00')

    snapshot = supervisor.snapshot(now_ts='2026-03-12T00:00:21+00:00')

    assert snapshot['state'] == 'reconnected'
    assert snapshot['reconcile_required'] is True

from quantx.exchanges.okx_private_stream import OKXPrivateStreamTransport


class _SocketStub:
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def recv(self) -> str:
        return ''

    def close(self) -> None:
        self.closed = True


def test_okx_private_stream_transport_reconnects_after_close():
    sockets: list[_SocketStub] = []

    def factory(_url: str) -> _SocketStub:
        socket = _SocketStub()
        sockets.append(socket)
        return socket

    transport = OKXPrivateStreamTransport(websocket_factory=factory)

    transport.connect()
    assert transport.is_connected is True

    transport.close()
    assert transport.is_connected is False

    transport.connect()
    assert transport.is_connected is True
    assert len(sockets) == 2
