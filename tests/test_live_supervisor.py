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
