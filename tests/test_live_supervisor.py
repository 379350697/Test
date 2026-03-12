from __future__ import annotations

from quantx.live_supervisor import LiveSupervisor


def test_live_supervisor_transitions_from_warming_to_reduce_only_and_blocked():
    supervisor = LiveSupervisor()

    supervisor.mark_bootstrap_ready()
    assert supervisor.state == 'warming'

    supervisor.on_stream_gap_detected()
    assert supervisor.state == 'reduce_only'

    supervisor.on_position_mismatch_detected()
    assert supervisor.state == 'blocked'


def test_live_supervisor_requires_three_healthy_cycles_to_recover_from_reduce_only():
    supervisor = LiveSupervisor(required_healthy_cycles=3)

    supervisor.mark_bootstrap_ready()
    supervisor.mark_live_active()
    supervisor.on_stream_gap_detected(reason='stream_gap')

    for _ in range(2):
        supervisor.record_health_cycle(healthy=True, cycle_boundary=True)
        assert supervisor.state == 'reduce_only'

    supervisor.record_health_cycle(healthy=True, cycle_boundary=True)
    assert supervisor.state == 'live_active'
