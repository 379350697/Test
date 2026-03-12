# Backtest Paper Live Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen QuantX's release path so backtest credibility, paper-trading stability, and small-size live rollout use one consistent promotion standard.

**Architecture:** Build on the existing runtime-core and OKX live-truth work instead of adding a parallel workflow. Add a unified promotion-gate evaluator, tighten backtest/runtime parity evidence, then turn paper trading into a pre-production soak surface with replayable health summaries and explicit live-promotion rules.

**Tech Stack:** Python 3.10+, pytest, existing `quantx` runtime/backtest modules, JSONL replay logs, CLI/readiness surfaces, Markdown runbooks.

---

## File Map

**Create**
- `quantx/release_gates.py` - shared promotion gate evaluator for `backtest -> paper -> live`.
- `quantx/paper_harness.py` - deterministic paper-run harness for soak-style health summaries.
- `tests/test_release_gates.py` - unit coverage for promotion gate logic.
- `tests/test_paper_harness.py` - paper-soak and health-summary coverage.

**Modify**
- `quantx/backtest.py` - surface stronger fidelity/cost metadata needed for gate decisions.
- `quantx/micro_backtest.py` - align tick/orderbook fidelity metadata with the same gate model.
- `quantx/replay.py` - summarize replay health, continuity, and incident counts for paper/live review.
- `quantx/reporting.py` - emit compact gate-oriented summary fields for downstream consumers.
- `quantx/monitoring.py` - expose incident and degradation summaries suitable for paper soak review.
- `quantx/readiness.py` - consume promotion gate outputs instead of only ad hoc booleans.
- `quantx/bootstrap.py` - expose startup policy details needed by live-promotion checks.
- `quantx/cli.py` - add a promotion-summary output path and paper-harness entrypoint.
- `quantx/runtime/paper_exchange.py` - expose counters and lifecycle metadata needed for soak summaries.
- `tests/runtime/test_runtime_parity.py` - expand parity assertions between backtest, paper, and replayed runtime truth.
- `tests/runtime/test_paper_exchange.py` - cover paper-exchange metrics and continuity signals.
- `tests/test_replay.py` - cover replay health summaries.
- `tests/test_live_readiness.py` - cover readiness and promotion rules tied to runtime health.
- `tests/test_quantx.py` - acceptance coverage for CLI promotion outputs and paper-run summaries.
- `docs/personal_live_go_no_go_checklist.md` - align checklist wording with actual gate outputs.
- `docs/restart_takeover_runbook.md` - align restart/live takeover guidance with new promotion surfaces.

## Chunk 1: Week 1 - Promotion Rules And Backtest Credibility

### Task 1: Add one shared promotion-gate evaluator for backtest, paper, and live

**Files:**
- Create: `quantx/release_gates.py`
- Create: `tests/test_release_gates.py`
- Modify: `quantx/readiness.py`
- Modify: `quantx/cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_release_gates_block_live_when_backtest_or_paper_requirements_fail():
    report = evaluate_release_gates(
        backtest={"ok": False, "max_drawdown_pct": 22.0},
        paper={"ok": True, "continuous_hours": 30, "alerts_ok": True},
        live={"runtime_truth_ok": True, "resume_mode": "live"},
    )

    assert report["eligible_stage"] == "backtest_only"
    assert "backtest_quality" in report["failed_gates"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_release_gates.py -k backtest_or_paper_requirements_fail`
Expected: FAIL with missing `evaluate_release_gates`.

- [ ] **Step 3: Write minimal implementation**

```python
def evaluate_release_gates(*, backtest: dict[str, object], paper: dict[str, object], live: dict[str, object]) -> dict[str, object]:
    ...
```

Start with a compact report shape:
- `eligible_stage`
- `failed_gates`
- `checks`
- `recommended_next_step`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_release_gates.py -k backtest_or_paper_requirements_fail`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_release_gates.py quantx/release_gates.py quantx/readiness.py quantx/cli.py
git commit -m "feat: add shared promotion gate evaluator"
```

### Task 2: Strengthen backtest metadata so promotion gates can judge credibility

**Files:**
- Modify: `quantx/backtest.py`
- Modify: `quantx/micro_backtest.py`
- Modify: `quantx/reporting.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_backtest_payload_exposes_gate_relevant_cost_and_fidelity_metadata():
    payload = main(["backtest", "--file", "data/demo.csv", "--strategy", "dca", "--params", "{}", "--json"])

    assert "promotion_summary" in payload
    assert "fidelity" in payload["promotion_summary"]
    assert "fee_ratio" in payload["promotion_summary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_quantx.py -k gate_relevant_cost_and_fidelity_metadata`
Expected: FAIL because current backtest output does not expose a gate-oriented summary.

- [ ] **Step 3: Write minimal implementation**

Add a backtest summary helper that surfaces:
- `fidelity`
- `trade_count`
- `fee_ratio`
- `max_drawdown_pct`
- `stability_score`
- `runtime_mode`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_quantx.py -k gate_relevant_cost_and_fidelity_metadata`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_quantx.py quantx/backtest.py quantx/micro_backtest.py quantx/reporting.py
git commit -m "feat: surface gate-oriented backtest credibility metadata"
```

### Task 3: Expand parity evidence so backtest, paper, and replay prove they behave like one system

**Files:**
- Modify: `tests/runtime/test_runtime_parity.py`
- Modify: `tests/test_replay.py`
- Modify: `quantx/replay.py`
- Modify: `quantx/runtime/paper_exchange.py`

- [ ] **Step 1: Write the failing test**

```python
def test_backtest_paper_and_replay_share_runtime_health_and_order_sequence_invariants():
    ...
    assert backtest_runtime["order_state_sequences"] == paper_runtime["order_state_sequences"]
    assert replay_summary["runtime_summary"]["health"]["degraded"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_parity.py tests/test_replay.py -k "runtime_health_and_order_sequence_invariants"`
Expected: FAIL because replay/paper summaries do not yet share the same invariant-focused health shape.

- [ ] **Step 3: Write minimal implementation**

Have paper and replay surfaces expose a minimal common summary:
- `health.degraded`
- `health.last_error`
- `order_state_sequences`
- `position_invariants`
- `ledger_invariants`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_parity.py tests/test_replay.py -k "runtime_health_and_order_sequence_invariants"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_runtime_parity.py tests/test_replay.py quantx/replay.py quantx/runtime/paper_exchange.py
git commit -m "test: tighten runtime parity evidence across backtest paper and replay"
```

## Chunk 2: Week 2 - Paper Soak And Live Promotion Closure

### Task 4: Add a deterministic paper harness that behaves like a pre-production soak surface

**Files:**
- Create: `quantx/paper_harness.py`
- Create: `tests/test_paper_harness.py`
- Modify: `quantx/cli.py`
- Modify: `tests/runtime/test_paper_exchange.py`

- [ ] **Step 1: Write the failing test**

```python
def test_paper_harness_reports_continuity_health_and_alert_status(tmp_path):
    report = run_paper_harness(
        event_log_path=str(tmp_path / "runtime" / "events.jsonl"),
        duration_minutes=60,
    )

    assert "continuous_minutes" in report
    assert "incident_counts" in report
    assert "promotion_ready" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_paper_harness.py -k continuity_health_and_alert_status`
Expected: FAIL with missing `run_paper_harness`.

- [ ] **Step 3: Write minimal implementation**

```python
def run_paper_harness(*, event_log_path: str, duration_minutes: int = 60) -> dict[str, object]:
    ...
```

First version goal: summarize continuity, incidents, and a simple promotion recommendation from deterministic inputs. Do not build background daemons yet.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_paper_harness.py -k continuity_health_and_alert_status`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_paper_harness.py tests/runtime/test_paper_exchange.py quantx/paper_harness.py quantx/cli.py
git commit -m "feat: add paper soak harness summary"
```

### Task 5: Extend replay and monitoring outputs so paper incidents are actionable instead of forensic work

**Files:**
- Modify: `quantx/replay.py`
- Modify: `quantx/monitoring.py`
- Modify: `quantx/reporting.py`
- Modify: `tests/test_replay.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_replay_daily_surfaces_incident_summary_and_gate_recommendation(tmp_path):
    payload = main(["replay-daily", "--events", str(tmp_path / "events.jsonl"), "--json"])

    assert "incident_summary" in payload
    assert "gate_recommendation" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_replay.py tests/test_quantx.py -k incident_summary_and_gate_recommendation`
Expected: FAIL because replay output is missing a promotion-oriented summary.

- [ ] **Step 3: Write minimal implementation**

Add compact summary fields:
- `incident_summary`
- `degrade_windows`
- `gate_recommendation`
- `operator_actions`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_replay.py tests/test_quantx.py -k incident_summary_and_gate_recommendation`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_replay.py tests/test_quantx.py quantx/replay.py quantx/monitoring.py quantx/reporting.py
git commit -m "feat: add replay incident summaries for promotion review"
```

### Task 6: Turn go/no-go guidance into a real live-promotion contract

**Files:**
- Modify: `quantx/readiness.py`
- Modify: `quantx/bootstrap.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `docs/personal_live_go_no_go_checklist.md`
- Modify: `docs/restart_takeover_runbook.md`

- [ ] **Step 1: Write the failing test**

```python
def test_readiness_requires_backtest_paper_and_runtime_promotion_gates_before_live():
    report = evaluate_readiness(
        mode="live",
        runtime_status={"execution_mode": "live", "degraded": False},
        promotion_gates={
            "eligible_stage": "paper_only",
            "failed_gates": ["paper_soak_duration"],
        },
    )

    assert report["checks_by_name"]["promotion_stage_gate"]["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k promotion_gates_before_live`
Expected: FAIL because readiness does not yet require a unified promotion-stage gate.

- [ ] **Step 3: Write minimal implementation**

Teach readiness to require:
- acceptable `eligible_stage`
- healthy runtime truth
- acceptable bootstrap `resume_mode`
- explicit paper-soak completion

Update the two runbooks so the documented checklist matches the new machine-readable gate names.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k promotion_gates_before_live`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py quantx/readiness.py quantx/bootstrap.py docs/personal_live_go_no_go_checklist.md docs/restart_takeover_runbook.md
git commit -m "feat: require promotion gates before live rollout"
```

## Final Verification

- [ ] **Step 1: Run the focused verification suite**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_release_gates.py tests/test_paper_harness.py tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py tests/runtime/test_runtime_parity.py tests/runtime/test_paper_exchange.py`
Expected: PASS.

- [ ] **Step 2: Run the broader regression suite covering current live-truth work**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_health.py tests/runtime/test_private_stream.py tests/runtime/test_reconcile.py tests/test_bootstrap.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS.

- [ ] **Step 3: Commit final acceptance adjustments**

```bash
git add quantx tests docs
git commit -m "test: add backtest paper live promotion acceptance coverage"
```

## Recommended Order

### Week 1
1. Task 1 - promotion gates
2. Task 2 - backtest credibility metadata
3. Task 3 - runtime parity evidence

### Week 2
1. Task 4 - paper soak harness
2. Task 5 - replay incident summary
3. Task 6 - live promotion contract

## Notes

- Use @superpowers:test-driven-development for every task.
- Use @superpowers:systematic-debugging before fixing any failure that is not directly explained by the current task.
- Use @superpowers:verification-before-completion before claiming any chunk is complete.
- Keep one shared promotion model; do not create separate ad hoc pass/fail rules in backtest, paper, and live.
- Prefer deterministic summaries and replayable fixtures over real-time soak orchestration in the first pass.
- Do not widen live rollout automatically; this plan is about making promotion decisions explicit and testable.
