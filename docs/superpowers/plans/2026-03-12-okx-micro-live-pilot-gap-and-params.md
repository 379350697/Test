# OKX Micro Live Pilot Gap And Params Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last-mile gaps between the unattended `OKX` MVP and a first small-capital `BTC/ETH/XRP` micro-live pilot by adding external health visibility, hard pilot risk gates, and operator-ready documentation.

**Architecture:** Keep the existing unattended runtime as the trading core. Add richer persisted heartbeat truth, a pure watchdog/healthcheck layer with CLI entrypoints and alerts, and machine-enforced pilot circuit-breaker stops that surface back through runtime status and operator docs.

**Tech Stack:** Python 3.10+, pytest, argparse CLI, existing `quantx` live runtime modules, JSON status files, `AlertRouter`, `RiskCircuitBreaker`, operator markdown docs.

---

## File Map

**Create**
- `quantx/live_watchdog.py` - pure watchdog classification logic for persisted runtime status, process liveness, stale detection, and alert-worthy outcomes.
- `tests/test_live_watchdog.py` - unit coverage for status classification, stale windows, process-dead handling, and alert decision boundaries.

**Modify**
- `quantx/live_runtime.py` - persist heartbeat timestamps, degrade reasons, and pilot-visible circuit state into the runtime store.
- `quantx/live_service.py` - integrate `RiskCircuitBreaker` into live order acceptance and expose circuit snapshots to runtime/status surfaces.
- `quantx/risk_engine.py` - add circuit-breaker snapshot helpers needed for persistence and status reporting.
- `quantx/cli.py` - add `autotrade-healthcheck`, wire watchdog alerts, and surface richer runtime metadata through `autotrade-status`.
- `tests/test_live_runtime.py` - cover heartbeat persistence and blocked-state reporting from runtime-owned status.
- `tests/test_live_readiness.py` - cover circuit-breaker enforcement in the live execution path.
- `tests/test_quantx.py` - add CLI acceptance coverage for `autotrade-healthcheck`, stale/dead-process classification, and runtime status payload updates.
- `docs/personal_live_go_no_go_checklist.md` - add the first-week `BTC/ETH/XRP` pilot envelope and healthcheck operator sequence.
- `docs/restart_takeover_runbook.md` - add watchdog interpretation and next-morning recovery guidance for `process_dead` and `status_stale`.

## Chunk 1: Heartbeat Truth And External Watchdog

### Task 1: Persist operator-usable runtime heartbeat fields

**Files:**
- Modify: `quantx/live_runtime.py`
- Modify: `tests/test_live_runtime.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py -k "heartbeat_fields_and_degrade_reason"`
Expected: FAIL because the runtime status payload does not yet persist the heartbeat timestamps and degrade reason fields.

- [ ] **Step 3: Write minimal implementation**

```python
class LiveRuntime:
    def bootstrap_once(self) -> dict[str, Any]:
        self._started_at = self._utc_now()
        ...

    def run_market_iteration(self) -> dict[str, Any]:
        self._last_market_iteration_at = self._utc_now()
        ...

    def run_health_iteration(...) -> dict[str, Any]:
        self._last_health_iteration_at = self._utc_now()
        ...

    def status(self) -> dict[str, Any]:
        return {
            'process': {'started_at': ...},
            'runtime': {
                'updated_at': ...,
                'last_market_iteration_at': ...,
                'last_health_iteration_at': ...,
                ...
            },
            'supervisor': {'last_degrade_reason': ...},
        }
```

Implementation rules:
- Use UTC ISO timestamps for all persisted heartbeat fields.
- Do not overwrite existing `process.pid` values seeded by `autotrade-start`.
- Keep the new fields inside the existing status JSON contract instead of creating a second status file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py -k "heartbeat_fields_and_degrade_reason"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_runtime.py tests/test_live_runtime.py tests/test_quantx.py
git commit -m "feat: persist pilot runtime heartbeat truth"
```

### Task 2: Add a pure watchdog classifier for persisted runtime status

**Files:**
- Create: `quantx/live_watchdog.py`
- Create: `tests/test_live_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_watchdog.py`
Expected: FAIL because the watchdog module does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class LiveWatchdogResult:
    ok: bool
    status: str
    reason: str
    should_alert: bool
    detail: dict[str, Any]

def evaluate_live_watchdog(
    *,
    status_payload: Mapping[str, Any],
    process_alive: bool,
    now: str | datetime,
    stale_after_s: int,
) -> LiveWatchdogResult:
    ...
```

Implementation rules:
- `process_dead` and `status_stale` must classify as `blocked`.
- `reduce_only` must stay distinct from `blocked`.
- Keep the classifier pure: no filesystem or network access in the core function.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_watchdog.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_watchdog.py tests/test_live_watchdog.py
git commit -m "feat: add live runtime watchdog classifier"
```

### Task 3: Add `autotrade-healthcheck` with optional webhook alerts

**Files:**
- Modify: `quantx/cli.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_autotrade_healthcheck_reports_blocked_for_stale_status_and_emits_alert(tmp_path, monkeypatch):
    status_path = tmp_path / 'autotrade' / 'status.json'
    LiveRuntimeStore(status_path).write_status({
        'process': {'pid': 4242, 'started_at': '2026-03-12T00:00:00+00:00'},
        'runtime': {'updated_at': '2026-03-12T00:00:00+00:00', 'execution_mode': 'live'},
        'supervisor': {'state': 'live_active'},
    })

    monkeypatch.setattr(cli, '_pid_is_alive', lambda pid: False)
    payload = cli.main([
        'autotrade-healthcheck',
        '--config', str(tmp_path / 'autotrade' / 'runtime_config.json'),
        '--status-path', str(status_path),
        '--stale-after-seconds', '60',
        '--alert-webhook', 'https://example.com/hook',
        '--json',
    ])

    assert payload['ok'] is False
    assert payload['status'] == 'blocked'
    assert payload['reason'] == 'process_dead'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_quantx.py -k "autotrade_healthcheck_reports_blocked"`
Expected: FAIL because the command does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def _pid_is_alive(pid: int) -> bool: ...

def _build_autotrade_healthcheck_payload(args) -> dict[str, Any]:
    ...

sub.add_parser('autotrade-healthcheck')
```

Implementation rules:
- Reuse `LiveRuntimeStore`, `AlertRouter`, and the pure watchdog classifier.
- Return structured JSON with `ok`, `status`, `reason`, and `detail`.
- Exit non-zero only through the CLI command path, not through the pure helper functions.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_quantx.py -k "autotrade_healthcheck_reports_blocked"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/cli.py tests/test_quantx.py
git commit -m "feat: add autotrade healthcheck alerts"
```

## Chunk 2: Hard Pilot Risk Gates

### Task 4: Integrate `RiskCircuitBreaker` into live order execution

**Files:**
- Modify: `quantx/live_service.py`
- Modify: `quantx/risk_engine.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_execution_service_blocks_new_orders_after_daily_loss_circuit_trip():
    breaker = RiskCircuitBreaker(CircuitBreakerLimits(max_daily_loss=50.0, max_orders_per_day=10))
    breaker.register_fill(-60.0)
    service = LiveExecutionService(
        DummyExchange(),
        config=LiveExecutionConfig(dry_run=True),
        circuit_breaker=breaker,
    )

    result = service.execute_orders([
        {'symbol': 'BTC-USDT-SWAP', 'side': 'BUY', 'qty': 1.0, 'price': 100.0, 'reduce_only': False},
    ])

    assert result['ok'] is False
    assert result['rejected'][0]['reason'] == 'pilot_circuit_daily_loss_exceeded'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_readiness.py -k "daily_loss_circuit_trip"`
Expected: FAIL because the live execution service does not yet consult the circuit breaker before submitting orders.

- [ ] **Step 3: Write minimal implementation**

```python
class RiskCircuitBreaker:
    def snapshot(self) -> dict[str, Any]:
        ...

class LiveExecutionService:
    def __init__(..., circuit_breaker: RiskCircuitBreaker | None = None, ...):
        ...

    def execute_orders(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        ok, reason = self.circuit_breaker.check()
        if not ok:
            return {'ok': False, 'rejected': [{'reason': f'pilot_circuit_{reason}'}], ...}
        ...
```

Implementation rules:
- Check the breaker before placing new orders.
- Register accepted orders with the breaker.
- Keep the breaker optional so existing non-pilot call sites remain compatible.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_readiness.py -k "daily_loss_circuit_trip"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_service.py quantx/risk_engine.py tests/test_live_readiness.py
git commit -m "feat: enforce pilot live circuit breaker"
```

### Task 5: Surface pilot circuit state through runtime truth and status

**Files:**
- Modify: `quantx/live_runtime.py`
- Modify: `tests/test_live_runtime.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_autotrade_status_includes_pilot_circuit_snapshot(tmp_path):
    status_path = tmp_path / 'autotrade' / 'status.json'
    LiveRuntimeStore(status_path).write_status({
        'process': {'pid': 4242},
        'runtime': {'execution_mode': 'blocked'},
        'supervisor': {'state': 'blocked'},
        'pilot_risk': {'reason': 'daily_loss_exceeded', 'ok': False},
    })

    payload = cli.main([
        'autotrade-status',
        '--exchange', 'okx',
        '--strategy', 'cta_strategy',
        '--watchlist', '["BTC-USDT-SWAP","ETH-USDT-SWAP","XRP-USDT-SWAP"]',
        '--total-margin', '1000',
        '--status-path', str(status_path),
        '--json',
    ])

    assert payload['pilot_risk']['ok'] is False
    assert payload['pilot_risk']['reason'] == 'daily_loss_exceeded'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py tests/test_quantx.py -k "pilot_circuit_snapshot"`
Expected: FAIL because runtime/status payloads do not yet preserve the circuit snapshot.

- [ ] **Step 3: Write minimal implementation**

```python
class LiveRuntime:
    def status(self) -> dict[str, Any]:
        return {
            ...,
            'pilot_risk': self.service.circuit_breaker_snapshot(),
        }
```

Implementation rules:
- Persist the snapshot through the existing runtime store.
- Keep the payload absent or explicitly healthy when no circuit breaker is configured.
- Do not introduce a second pilot-risk status path.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py tests/test_quantx.py -k "pilot_circuit_snapshot"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_runtime.py tests/test_live_runtime.py tests/test_quantx.py
git commit -m "feat: expose pilot circuit status"
```

## Chunk 3: Operator Docs And First-Week Envelope

### Task 6: Update operator docs for `BTC/ETH/XRP` micro-live rollout

**Files:**
- Modify: `docs/personal_live_go_no_go_checklist.md`
- Modify: `docs/restart_takeover_runbook.md`

- [ ] **Step 1: Write the failing doc acceptance check**

```text
Checklist and runbook must mention:
- BTC/ETH/XRP first-week watchlist
- total_margin=1000
- max_symbol_weight=0.30
- max_notional_per_cycle=400
- autotrade-healthcheck
- process_dead / status_stale next-morning handling
```

- [ ] **Step 2: Verify the current docs are missing it**

Run: `python - <<'PY'\nfrom pathlib import Path\ntext=(Path('docs/personal_live_go_no_go_checklist.md').read_text(encoding='utf-8') + '\\n' + Path('docs/restart_takeover_runbook.md').read_text(encoding='utf-8'))\nfor needle in ['BTC-USDT-SWAP','ETH-USDT-SWAP','XRP-USDT-SWAP','max_notional_per_cycle=400','autotrade-healthcheck','process_dead','status_stale']:\n    assert needle in text, needle\nPY`
Expected: FAIL with one or more missing strings.

- [ ] **Step 3: Write minimal documentation updates**

```markdown
- Add the recommended first-week pilot envelope for `BTC/ETH/XRP`
- Add `autotrade-healthcheck` to the operator command sequence
- Add morning response guidance for `process_dead` and `status_stale`
```

Implementation rules:
- Keep the docs aligned with the actual CLI argument names.
- Document the values as the recommended starting envelope, not as hard-coded application defaults.

- [ ] **Step 4: Run the doc acceptance check**

Run: `python - <<'PY'\nfrom pathlib import Path\ntext=(Path('docs/personal_live_go_no_go_checklist.md').read_text(encoding='utf-8') + '\\n' + Path('docs/restart_takeover_runbook.md').read_text(encoding='utf-8'))\nfor needle in ['BTC-USDT-SWAP','ETH-USDT-SWAP','XRP-USDT-SWAP','max_notional_per_cycle=400','autotrade-healthcheck','process_dead','status_stale']:\n    assert needle in text, needle\nprint('ok')\nPY`
Expected: PASS and print `ok`.

- [ ] **Step 5: Commit**

```bash
git add docs/personal_live_go_no_go_checklist.md docs/restart_takeover_runbook.md
git commit -m "docs: add micro-live pilot operator envelope"
```

## Final Verification

- [ ] **Step 1: Run the focused pilot runtime suite**

Run: `python -m pytest -q -o addopts= tests/test_live_watchdog.py tests/test_live_runtime.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS with no failures.

- [ ] **Step 2: Run `diff --check`**

Run: `git diff --check`
Expected: no output.

- [ ] **Step 3: Re-read the spec and confirm each design area is implemented**

Check against: `docs/superpowers/specs/2026-03-12-okx-micro-live-pilot-gap-and-params-design.md`
Expected: heartbeat truth, watchdog, pilot circuit gates, and docs envelope are all covered.
