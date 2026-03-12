# OKX Unattended Live Closure Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed runtime health, continuous private-stream supervision, reconcile-driven execution restrictions, and operator-facing health surfaces so `OKX` live truth can advance from supervised tooling toward unattended live operation.

**Architecture:** Build on the existing live-truth runtime instead of redefining semantics. Introduce a runtime-owned health model that consumes replay, runtime-event, reconcile, stream-freshness, and bootstrap signals; have `LiveExecutionService`, `bootstrap`, `readiness`, and CLI consume that one health view. Add a small transport-agnostic private-stream supervisor first, then wire an `OKX` transport adapter into it so unattended behavior is testable without live network access.

**Tech Stack:** Python 3.10+, dataclasses, pytest, existing QuantX runtime modules, `OKX` REST/runtime adapters, JSONL replay logs, `websocket-client` for private-stream transport.

---

## File Map

**Create**
- `quantx/runtime/health.py` - runtime-owned health state, degrade reasons, stream freshness, reconcile status, and execution-mode derivation.
- `quantx/runtime/private_stream.py` - transport-agnostic private-stream supervisor with heartbeat, reconnect, stale-stream, and gap-detected state transitions.
- `quantx/exchanges/okx_private_stream.py` - `OKX` private-stream transport/auth/subscription wrapper around an injectable websocket client.
- `tests/runtime/test_health.py` - unit coverage for runtime health derivation and degrade semantics.
- `tests/runtime/test_private_stream.py` - supervisor coverage for heartbeat, reconnect, and gap-detection flows.

**Modify**
- `pyproject.toml` - add the minimal websocket dependency needed for the `OKX` private-stream transport.
- `quantx/runtime/live_coordinator.py` - own a mutable runtime health state, record runtime-event apply faults, reconcile results, stream health, and expose a full `status()` snapshot.
- `quantx/live_service.py` - surface runtime status, fail closed on degraded/block conditions, integrate the private-stream supervisor, and stop swallowing runtime truth faults.
- `quantx/runtime/reconcile.py` - promote mismatch reporting from informational output to severity-bearing operational signal.
- `quantx/bootstrap.py` - turn warm/cold recovery into explicit resume policy (`live`, `reduce_only`, `read_only`, `blocked`) plus runtime-status output.
- `quantx/readiness.py` - consume real runtime status fields instead of only static injected booleans.
- `quantx/cli.py` - expose runtime health, execution mode, stream freshness, and recovery policy in deploy/runtime payloads.
- `quantx/runtime/__init__.py` - export runtime health and private-stream helpers.
- `tests/runtime/test_reconcile.py` - add severity escalation coverage.
- `tests/test_bootstrap.py` - add startup policy coverage.
- `tests/test_live_readiness.py` - add fail-closed, private-stream, and runtime-health integration coverage.
- `tests/test_quantx.py` - add CLI/runtime acceptance coverage for unattended health metadata.

## Chunk 1: Runtime Health Ownership

### Task 1: Add a runtime-owned health model that derives execution safety from real signals

**Files:**
- Create: `quantx/runtime/health.py`
- Modify: `quantx/runtime/__init__.py`
- Create: `tests/runtime/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
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
    assert snapshot['execution_mode'] == 'blocked'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_health.py -k stale_stream_and_blocking_reconcile`
Expected: FAIL with missing `RuntimeHealthState` import or missing snapshot fields.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class RuntimeHealthState:
    replay_persistence: bool = False
    degraded: bool = False
    recovery_mode: str = 'cold'
    reconcile_report: dict[str, Any] | None = None
    execution_mode: str = 'blocked'
    last_stream_event_ts: str | None = None
    last_degrade_reason: str | None = None

    def snapshot(self, *, now_ts: str | None = None, stale_after_s: int = 30) -> dict[str, Any]:
        ...
```

Keep the first version small: model replay persistence, reconcile health, stream freshness, last degrade reason, and derived execution mode (`live`, `reduce_only`, `read_only`, `blocked`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_health.py -k stale_stream_and_blocking_reconcile`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_health.py quantx/runtime/health.py quantx/runtime/__init__.py
git commit -m "feat: add runtime health snapshot model"
```

### Task 2: Surface runtime-event apply faults through live coordinator and service health

**Files:**
- Modify: `quantx/runtime/live_coordinator.py`
- Modify: `quantx/live_service.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_service_marks_runtime_degraded_when_runtime_event_application_fails(tmp_path):
    svc = LiveExecutionService(
        DummyExchange(),
        config=LiveExecutionConfig(dry_run=True, exchange='okx', runtime_mode='derivatives'),
        runtime_event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
    )

    svc.ingest_runtime_event(
        AccountEvent(
            exchange='okx',
            ts='2026-03-12T00:00:00+00:00',
            event_type='funding',
            payload={},
        )
    )

    status = svc.runtime_status()

    assert status['degraded'] is True
    assert status['last_error']['stage'] == 'apply_event'
    assert status['execution_mode'] == 'blocked'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k runtime_event_application_fails`
Expected: FAIL because runtime event errors are currently swallowed and no `runtime_status()` surface exists.

- [ ] **Step 3: Write minimal implementation**

```python
class LiveRuntimeCoordinator:
    health: RuntimeHealthState = field(default_factory=RuntimeHealthState)

    def apply_event(self, event: object) -> object:
        try:
            ...
        except Exception as exc:
            self.health.mark_apply_error(exc, stage='apply_event')
            raise

class LiveExecutionService:
    def runtime_status(self) -> dict[str, Any]:
        return self.runtime_coordinator.status()
```

Stop swallowing runtime truth faults. The service may still log them, but the runtime health model must record them and drive the system into degraded/blocked state.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k runtime_event_application_fails`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py quantx/runtime/live_coordinator.py quantx/live_service.py
git commit -m "feat: surface runtime truth faults through health status"
```

## Chunk 2: Reconcile And Recovery Policy

### Task 3: Turn reconcile output into a severity-bearing execution gate

**Files:**
- Modify: `quantx/runtime/reconcile.py`
- Modify: `quantx/live_service.py`
- Modify: `tests/runtime/test_reconcile.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_reconcile_report_escalates_to_block_for_position_mismatch():
    report = build_reconcile_report(
        {
            'positions': {'BTC-USDT-SWAP': {'long': {'qty': 1.0, 'avg_entry_price': 100.0}}},
            'ledger': {'equity': 1000.0},
            'observed_exchange': {
                'positions': {'BTC-USDT-SWAP': {'long': {'qty': 2.0, 'avg_entry_price': 100.0}}},
                'account': {'equity': 1000.0},
            },
        }
    )

    assert report['severity'] == 'block'


def test_live_service_blocks_new_orders_when_reconcile_health_is_blocked():
    svc = LiveExecutionService(DummyExchange(), config=LiveExecutionConfig(dry_run=True, exchange='okx'))
    svc.runtime_coordinator.health.mark_reconcile({'ok': False, 'severity': 'block'})

    result = svc.execute_orders([
        {'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'price': 50000.0, 'position_side': 'long'}
    ])

    assert result['ok'] is False
    assert result['rejected'][0]['reason'] == 'runtime_truth_blocked'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_reconcile.py tests/test_live_readiness.py -k "escalates_to_block or reconcile_health_is_blocked"`
Expected: FAIL because reconcile severity is only `ok/warn` today and `execute_orders()` does not fail closed on runtime health.

- [ ] **Step 3: Write minimal implementation**

```python
def build_reconcile_report(...):
    severity = 'block' if position_mismatches else 'warn' if account_mismatches else 'ok'
    ...

class LiveExecutionService:
    def execute_orders(self, orders):
        status = self.runtime_status()
        if status['execution_mode'] == 'blocked':
            return {'accepted': [], 'rejected': [{'reason': 'runtime_truth_blocked'}], 'ok': False, ...}
```

First version rule: any position mismatch blocks new live risk. Account-only drift may remain `warn` unless it crosses future stricter thresholds.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_reconcile.py tests/test_live_readiness.py -k "escalates_to_block or reconcile_health_is_blocked"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_reconcile.py tests/test_live_readiness.py quantx/runtime/reconcile.py quantx/live_service.py
git commit -m "feat: gate live execution on reconcile health"
```

### Task 4: Convert bootstrap recovery reports into explicit resume policy

**Files:**
- Modify: `quantx/bootstrap.py`
- Modify: `quantx/readiness.py`
- Modify: `tests/test_bootstrap.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bootstrap_recover_and_reconcile_returns_blocked_resume_mode_for_cold_recovery(tmp_path):
    report = bootstrap_recover_and_reconcile(
        service=_StubService({'open_orders': [], 'positions': [], 'symbol_rules': {}}),
        oms_store=JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl')),
        runtime_event_log_path=str(tmp_path / 'runtime' / 'missing.jsonl'),
        initial_cash=1000.0,
        symbol='BTC-USDT-SWAP',
    )

    assert report['recovery_mode'] == 'cold'
    assert report['resume_mode'] == 'blocked'
    assert report['runtime_status']['degraded'] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_bootstrap.py -k blocked_resume_mode_for_cold_recovery`
Expected: FAIL because bootstrap currently reports recovery facts but not a runtime policy surface.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class BootstrapTakeoverReport:
    ...
    resume_mode: str
    runtime_status: dict[str, Any]

if warm_snapshot is None:
    resume_mode = 'blocked'
elif position_diffs:
    resume_mode = 'read_only'
else:
    resume_mode = 'live'
```

Return policy with the report so callers can wire startup decisions without re-deriving the logic ad hoc.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_bootstrap.py -k blocked_resume_mode_for_cold_recovery`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_bootstrap.py tests/test_live_readiness.py quantx/bootstrap.py quantx/readiness.py
git commit -m "feat: derive startup resume policy from recovery"
```

## Chunk 3: Continuous Private-Stream Maintenance

### Task 5: Add a transport-agnostic private-stream supervisor with freshness and reconnect state

**Files:**
- Create: `quantx/runtime/private_stream.py`
- Create: `tests/runtime/test_private_stream.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_private_stream.py`
Expected: FAIL with missing `PrivateStreamSupervisor`.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class PrivateStreamSupervisor:
    stale_after_s: int = 30
    reconnect_backoff_s: int = 1
    ...

    def mark_connected(self, ts: str) -> None: ...
    def mark_message(self, ts: str) -> None: ...
    def mark_disconnect(self, ts: str, *, reason: str) -> None: ...
    def snapshot(self, *, now_ts: str) -> dict[str, Any]: ...
```

Keep it transport-agnostic: no websocket code yet, only state transitions and derived freshness/reconcile-required status.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_private_stream.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_private_stream.py quantx/runtime/private_stream.py
git commit -m "feat: add private stream supervisor"
```

### Task 6: Add an `OKX` private-stream transport and wire it into `LiveExecutionService`

**Files:**
- Modify: `pyproject.toml`
- Create: `quantx/exchanges/okx_private_stream.py`
- Modify: `quantx/live_service.py`
- Modify: `quantx/runtime/live_coordinator.py`
- Modify: `quantx/runtime/__init__.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_service_private_stream_updates_runtime_health_and_ingests_messages(monkeypatch, tmp_path):
    transport = _FakeOKXPrivateStream(
        messages=[
            {'type': 'fill', 'payload': {...}},
            {'type': 'funding', 'payload': {...}},
        ]
    )
    svc = LiveExecutionService(
        _DummyOKXPerpExchange(),
        config=LiveExecutionConfig(dry_run=False, exchange='okx', runtime_mode='derivatives'),
        runtime_adapter=OKXPerpAdapter(),
        runtime_event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
        private_stream_transport=transport,
    )

    svc.run_private_stream_once()

    status = svc.runtime_status()
    snapshot = svc.runtime_snapshot()

    assert status['stream']['state'] == 'connected'
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k private_stream_updates_runtime_health`
Expected: FAIL because `LiveExecutionService` has no private-stream loop and no injectable transport.

- [ ] **Step 3: Write minimal implementation**

```python
class OKXPrivateStreamTransport:
    def connect(self): ...
    def iter_messages(self): ...
    def close(self): ...

class LiveExecutionService:
    def run_private_stream_once(self) -> int:
        ...
```

Wire the smallest viable transport seam:
- injectable websocket factory
- login/subscription payload builders
- one service loop that maps incoming raw messages through `OKXPerpAdapter`
- supervisor + runtime health updates on connect/message/disconnect

Do not over-design background orchestration yet; get one deterministic, testable run loop in place first.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k private_stream_updates_runtime_health`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_live_readiness.py quantx/exchanges/okx_private_stream.py quantx/live_service.py quantx/runtime/live_coordinator.py quantx/runtime/__init__.py
git commit -m "feat: integrate okx private stream supervision"
```

## Chunk 4: Operator Surfaces And Acceptance

### Task 7: Wire runtime health into readiness and CLI surfaces

**Files:**
- Modify: `quantx/readiness.py`
- Modify: `quantx/cli.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_readiness_blocks_live_when_stream_is_stale_even_if_replay_persists(tmp_path):
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(dry_run=False, exchange='okx', runtime_mode='derivatives', allowed_symbols=('BTC-USDT-SWAP',), max_orders_per_cycle=1, max_notional_per_cycle=1000.0),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=1000.0),
        alert_router=_router_with_webhook(),
        oms_store=JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl')),
        runtime_status={
            'replay_persistence': True,
            'degraded': False,
            'reconcile_ok': True,
            'stream': {'stale': True},
            'execution_mode': 'blocked',
        },
    )

    report = evaluate_readiness(ctx)
    checks = {check['name']: check for check in report.checks}

    assert checks['live_truth_stream_fresh']['ok'] is False
    assert checks['live_truth_execution_mode_allowed']['ok'] is False


def test_deploy_payload_surfaces_runtime_health_summary_for_unattended_live():
    payload = main(['deploy', '--json', '--symbol', 'BTC-USDT-SWAP'])

    assert 'runtime_truth' in payload['runtime']
    assert 'execution_mode' in payload['runtime']['runtime_truth']
    assert 'stream' in payload['runtime']['runtime_truth']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py tests/test_quantx.py -k "stream_is_stale or runtime_health_summary_for_unattended_live"`
Expected: FAIL because readiness and CLI only expose a subset of runtime health today.

- [ ] **Step 3: Write minimal implementation**

```python
_append_check(checks, 'live_truth_stream_fresh', not bool(runtime_status.get('stream', {}).get('stale')), ...)
_append_check(checks, 'live_truth_execution_mode_allowed', runtime_status.get('execution_mode') in {'live', 'reduce_only'}, ...)
```

Also have CLI export the real runtime-health shape instead of only a static `replay_persistence/degraded/reconcile_ok` triple.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py tests/test_quantx.py -k "stream_is_stale or runtime_health_summary_for_unattended_live"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py tests/test_quantx.py quantx/readiness.py quantx/cli.py
git commit -m "feat: expose unattended runtime health surfaces"
```

### Task 8: Add unattended-live acceptance coverage across recovery, private stream, and CLI

**Files:**
- Modify: `tests/test_bootstrap.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_bootstrap_and_runtime_health_fail_closed_after_cold_recovery(tmp_path):
    report = bootstrap_recover_and_reconcile(
        service=_StubService({'open_orders': [], 'positions': [], 'symbol_rules': {}}),
        oms_store=JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl')),
        runtime_event_log_path=str(tmp_path / 'runtime' / 'missing.jsonl'),
        initial_cash=1000.0,
        symbol='BTC-USDT-SWAP',
    )

    assert report['resume_mode'] == 'blocked'
    assert report['runtime_status']['execution_mode'] == 'blocked'


def test_live_service_blocks_new_risk_until_reconcile_clears_after_stream_gap(tmp_path):
    svc = ...
    svc.runtime_coordinator.health.mark_stream_gap('2026-03-12T00:00:10+00:00', reason='disconnect')

    blocked = svc.execute_orders([{'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'price': 50000.0}])
    assert blocked['ok'] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_bootstrap.py tests/test_live_readiness.py tests/test_quantx.py -k "fail_closed or stream_gap"`
Expected: FAIL until runtime health, startup policy, and private-stream gaps are all wired through the outward-facing surfaces.

- [ ] **Step 3: Write minimal implementation**

```python
# Keep this final task as the acceptance-tightening pass.
# Only patch the pieces exposed by the new tests:
# - startup fail-closed policy surfaces
# - stream-gap degrade semantics
# - CLI/readiness/runtime acceptance metadata
```

Do not add new abstractions here unless the acceptance failures force them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_health.py tests/runtime/test_private_stream.py tests/runtime/test_reconcile.py tests/test_bootstrap.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_health.py tests/runtime/test_private_stream.py tests/runtime/test_reconcile.py tests/test_bootstrap.py tests/test_live_readiness.py tests/test_quantx.py pyproject.toml quantx/runtime/health.py quantx/runtime/private_stream.py quantx/exchanges/okx_private_stream.py quantx/runtime/live_coordinator.py quantx/runtime/reconcile.py quantx/live_service.py quantx/bootstrap.py quantx/readiness.py quantx/cli.py quantx/runtime/__init__.py
git commit -m "test: add unattended live closure acceptance coverage"
```

## Notes For Execution

- Use @superpowers:using-git-worktrees before implementation, because unattended-live closure should be developed in an isolated worktree.
- Use @superpowers:test-driven-development for every task.
- Use @superpowers:systematic-debugging before fixing any failing behavior that is not explained by the current task.
- Use @superpowers:verification-before-completion before claiming any chunk is complete or committing.
- Keep exchange snapshots reconciliation-only; do not introduce snapshot-driven ledger rewrites while implementing unattended health logic.
- Prefer a transport-agnostic supervisor before embedding `OKX` websocket details deep in `LiveExecutionService`.
- If the harness does not provide the `plan-document-reviewer` subagent, do a manual chunk review before moving to the next chunk and record any assumptions in execution notes or commit messages.

Plan complete and saved to `docs/superpowers/plans/2026-03-12-okx-unattended-live-closure.md`. Ready to execute?