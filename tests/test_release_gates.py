from quantx.release_gates import evaluate_release_gates


def test_release_gates_block_live_when_backtest_or_paper_requirements_fail():
    report = evaluate_release_gates(
        backtest={"ok": False, "max_drawdown_pct": 22.0},
        paper={"ok": True, "continuous_hours": 30, "alerts_ok": True},
        live={"runtime_truth_ok": True, "resume_mode": "live"},
    )

    assert report["eligible_stage"] == "backtest_only"
    assert "backtest_quality" in report["failed_gates"]
