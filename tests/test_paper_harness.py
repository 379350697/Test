from quantx.paper_harness import run_paper_harness


def test_paper_harness_reports_continuity_health_and_alert_status(tmp_path):
    report = run_paper_harness(
        event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
        duration_minutes=60,
    )

    assert 'continuous_minutes' in report
    assert 'incident_counts' in report
    assert 'promotion_ready' in report
