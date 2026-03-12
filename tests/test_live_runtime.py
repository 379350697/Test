from __future__ import annotations

from quantx.live_runtime import LiveRuntime, LiveRuntimeConfig


class _MarketDriverStub:
    def poll_once(self):
        return {}


class _PrivateStreamStub:
    def connect(self) -> None:
        return None

    def iter_messages(self):
        return []

    def close(self) -> None:
        return None


class _LiveServiceStub:
    def __init__(self):
        self.execution_modes: list[str] = []

    def set_execution_mode(self, mode: str) -> None:
        self.execution_modes.append(mode)

    def run_private_stream_once(self) -> int:
        return 0


def test_live_runtime_degrades_to_reduce_only_and_recovers_after_three_healthy_5m_cycles():
    runtime = LiveRuntime(
        config=LiveRuntimeConfig(watchlist=('BTC-USDT-SWAP',), strategy_name='cta_strategy', total_margin=1000.0),
        market_driver=_MarketDriverStub(),
        private_stream_transport=_PrivateStreamStub(),
        service=_LiveServiceStub(),
    )

    runtime.bootstrap_once()
    runtime.run_health_iteration(force_gap=True)
    assert runtime.supervisor.state == 'reduce_only'

    for _ in range(3):
        runtime.run_health_iteration(force_healthy=True, cycle_boundary=True)

    assert runtime.supervisor.state == 'live_active'
