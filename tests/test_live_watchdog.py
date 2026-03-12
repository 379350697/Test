from quantx.live_watchdog import evaluate_live_watchdog


def test_live_watchdog_classifies_dead_process_and_stale_status_as_blocked():
    result = evaluate_live_watchdog(
        status_payload={
            'process': {'pid': 4242, 'started_at': '2026-03-12T00:00:00+00:00'},
            'runtime': {'updated_at': '2026-03-12T00:00:00+00:00', 'execution_mode': 'live'},
            'supervisor': {'state': 'live_active'},
        },
        process_alive=False,
        now='2026-03-12T00:03:00+00:00',
        stale_after_s=60,
    )

    assert result.ok is False
    assert result.status == 'blocked'
    assert result.reason == 'process_dead'
