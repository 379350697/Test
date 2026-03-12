from __future__ import annotations

from quantx.runtime import RuntimeHealthState


def test_runtime_health_snapshot_degrades_on_stale_stream_and_blocking_reconcile():
    health = RuntimeHealthState()
    health.mark_replay_persistence(True)
    health.mark_stream_started('2026-03-12T00:00:00+00:00')
    health.mark_stream_event('2026-03-12T00:00:05+00:00')
    health.mark_reconcile({'ok': False, 'severity': 'block'})

    snapshot = health.snapshot(
        now_ts='2026-03-12T00:01:00+00:00',
        stale_after_s=30,
    )

    assert snapshot['replay_persistence'] is True
    assert snapshot['stream']['stale'] is True
    assert snapshot['reconcile_ok'] is False
    assert snapshot['degraded'] is True
