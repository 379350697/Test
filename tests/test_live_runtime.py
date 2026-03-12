from __future__ import annotations

from quantx.live_runtime import LiveRuntime, LiveRuntimeConfig
from quantx.live_runtime_store import LiveRuntimeStore


class _MarketDriverStub:
    def __init__(self):
        self._last_closed_bar_ts = {'BTC-USDT-SWAP': '2026-03-12T00:05:00+00:00'}

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


def test_live_runtime_degrades_to_reduce_only_and_recovers_after_three_healthy_5m_cycles(tmp_path):
    runtime = LiveRuntime(
        config=LiveRuntimeConfig(watchlist=('BTC-USDT-SWAP',), strategy_name='cta_strategy', total_margin=1000.0),
        market_driver=_MarketDriverStub(),
        private_stream_transport=_PrivateStreamStub(),
        service=_LiveServiceStub(),
        store=LiveRuntimeStore(tmp_path / 'autotrade' / 'status.json'),
    )

    runtime.bootstrap_once()
    runtime.run_health_iteration(force_gap=True)
    assert runtime.supervisor.state == 'reduce_only'

    for _ in range(3):
        runtime.run_health_iteration(force_healthy=True, cycle_boundary=True)

    assert runtime.supervisor.state == 'live_active'
    assert runtime.store.read_status()['supervisor']['state'] == 'live_active'


def test_live_runtime_store_round_trips_status_and_recovery_state(tmp_path):
    store = LiveRuntimeStore(tmp_path / 'autotrade' / 'status.json')

    store.write_status({
        'supervisor': {'state': 'reduce_only'},
        'healthy_cycle_count': 2,
        'last_closed_bar_ts': {'BTC-USDT-SWAP': '2026-03-12T00:05:00+00:00'},
    })

    payload = store.read_status()
    assert payload['supervisor']['state'] == 'reduce_only'
    assert payload['healthy_cycle_count'] == 2


def test_live_runtime_persists_heartbeat_fields_and_degrade_reason(tmp_path):
    store = LiveRuntimeStore(tmp_path / 'status.json')
    runtime = LiveRuntime(
        config=LiveRuntimeConfig(watchlist=('BTC-USDT-SWAP',), strategy_name='cta_strategy', total_margin=1000.0),
        market_driver=_MarketDriverStub(),
        private_stream_transport=_PrivateStreamStub(),
        service=_LiveServiceStub(),
        store=store,
    )

    runtime.bootstrap_once()
    runtime.run_market_iteration()
    runtime.run_health_iteration(force_gap=True)

    payload = store.read_status()
    assert payload['process']['started_at']
    assert payload['runtime']['updated_at']
    assert payload['runtime']['last_market_iteration_at']
    assert payload['runtime']['last_health_iteration_at']
    assert payload['supervisor']['last_degrade_reason'] == 'stream_gap'
