# OKX Unattended Live MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current OKX live-preflight stack into a real unattended `5m` closed-bar live runtime with `supervisor + worker`, `reduce_only` degradation, `3` healthy-cycle auto-recovery, and operator-visible start/status surfaces.

**Architecture:** Reuse the existing runtime-truth path instead of building a second live engine. Implement a real market-driver input, a richer supervisor/execution-mode contract, a long-lived runtime loop with persisted status, and CLI surfaces that launch and inspect that runtime rather than returning static payloads.

**Tech Stack:** Python 3.10+, pytest, argparse CLI, existing `quantx` live/runtime modules, JSON status files, OKX REST polling for closed candles, OKX private WebSocket transport.

---

## File Map

**Create**
- `quantx/live_runtime.py` - long-lived unattended runtime coordinator that owns bootstrap, worker loops, and supervisor-driven order permissions.
- `quantx/live_runtime_store.py` - JSON-backed runtime status/config snapshot store used by `autotrade-start`, `autotrade-status`, and restart recovery.
- `tests/test_live_market_driver.py` - closed-bar polling, dedupe, and timestamp coverage for the `OKX` market driver.
- `tests/test_live_runtime.py` - integration coverage for degrade/recover loops, persisted status, and restart behavior.

**Modify**
- `quantx/exchanges/okx_perp_client.py` - add a minimal candle-fetch surface the market driver can call without inventing a second OKX HTTP client.
- `quantx/live_market_driver.py` - turn the stub into a real `OKX` closed-`5m` market driver with dedupe and polling helpers.
- `quantx/live_supervisor.py` - expand the minimal state holder into a real unattended state machine with recovery counters and execution permissions.
- `quantx/live_service.py` - accept a supervisor execution mode override and enforce `reduce_only` / `read_only` / `blocked` behavior at order-submit time.
- `quantx/exchanges/okx_private_stream.py` - make the transport easier to drive from a long-lived health worker without inventing parallel socket logic.
- `quantx/cli.py` - add an internal runtime-launch path, make `autotrade-start` spawn a real runtime, and make `autotrade-status` read persisted runtime truth.
- `tests/test_okx_perp_client.py` - add candle fetch coverage for the perp client.
- `tests/test_live_supervisor.py` - add recovery-window and hard-block coverage.
- `tests/test_live_readiness.py` - cover `reduce_only` enforcement and keep readiness aligned with the real runtime states.
- `tests/runtime/test_private_stream.py` - extend private-stream health coverage used by the health worker.
- `tests/test_quantx.py` - turn CLI acceptance tests from payload-only checks into real runtime launch/status checks.
- `docs/personal_live_go_no_go_checklist.md` - align operator steps with the real launcher/status workflow.
- `docs/restart_takeover_runbook.md` - document degrade/recover/blocked behavior for the new unattended runtime.

## Chunk 1: Market Input And Execution Control

### Task 1: Add a real OKX closed-`5m` market driver on top of the perp client

**Files:**
- Modify: `quantx/exchanges/okx_perp_client.py`
- Modify: `quantx/live_market_driver.py`
- Create: `tests/test_live_market_driver.py`
- Modify: `tests/test_okx_perp_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_okx_kline_market_driver_emits_only_new_closed_5m_bars_per_symbol():
    client = _ClosedBarStub()
    driver = OKXKlineMarketDriver(
        client=client,
        watchlist=('BTC-USDT-SWAP', 'ETH-USDT-SWAP'),
        timeframe='5m',
    )

    first = driver.poll_once()
    second = driver.poll_once()

    assert set(first) == {'BTC-USDT-SWAP', 'ETH-USDT-SWAP'}
    assert first['BTC-USDT-SWAP'][-1].close == 101.5
    assert second == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_market_driver.py tests/test_okx_perp_client.py -k "closed_5m_bars or candle_fetch"`
Expected: FAIL with missing candle fetch support or missing `poll_once()` / dedupe behavior.

- [ ] **Step 3: Write minimal implementation**

```python
class OKXPerpClient(OKXClient):
    def get_candles(self, symbol: str, *, bar: str = '5m', limit: int = 200) -> list[dict[str, Any]]: ...

@dataclass(slots=True)
class OKXKlineMarketDriver:
    client: Any
    watchlist: tuple[str, ...]
    timeframe: str = '5m'
    _last_closed_bar_ts: dict[str, str] = field(default_factory=dict)

    def poll_once(self) -> dict[str, list[Candle]]: ...
```

Implementation rules:
- Use one `OKX` client surface; do not invent a second market-only client.
- Emit only confirmed/closed bars.
- Deduplicate by last emitted closed-bar timestamp per symbol.
- Keep the driver deterministic and testable with a stub client.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_market_driver.py tests/test_okx_perp_client.py -k "closed_5m_bars or candle_fetch"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/exchanges/okx_perp_client.py quantx/live_market_driver.py tests/test_live_market_driver.py tests/test_okx_perp_client.py
git commit -m "feat: add okx closed-bar market driver"
```

### Task 2: Expand the supervisor into a real reduce-only / recovery execution controller

**Files:**
- Modify: `quantx/live_supervisor.py`
- Modify: `quantx/live_service.py`
- Modify: `tests/test_live_supervisor.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

```python
def test_live_execution_service_rejects_opening_orders_when_execution_mode_is_reduce_only():
    service = LiveExecutionService(_BudgetExchange(), config=LiveExecutionConfig(dry_run=True))
    service.set_execution_mode('reduce_only')

    result = service.execute_orders([
        {'symbol': 'BTC-USDT-SWAP', 'side': 'BUY', 'qty': 1.0, 'price': 100.0, 'reduce_only': False},
    ])

    assert result['ok'] is False
    assert result['rejected'][0]['reason'] == 'runtime_truth_reduce_only'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_supervisor.py tests/test_live_readiness.py -k "healthy_cycles_to_recover or execution_mode_is_reduce_only"`
Expected: FAIL because the supervisor does not track recovery windows and the execution service only blocks `blocked` mode today.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class LiveSupervisor:
    state: str = 'bootstrap_pending'
    required_healthy_cycles: int = 3
    consecutive_healthy_cycles: int = 0
    last_degrade_reason: str | None = None

    def execution_mode(self) -> str: ...
    def record_health_cycle(self, *, healthy: bool, cycle_boundary: bool) -> None: ...
    def allow_order(self, *, reduce_only: bool) -> bool: ...
```

```python
class LiveExecutionService:
    def set_execution_mode(self, mode: str) -> None: ...
```

Implementation rules:
- `reduce_only` must reject new risk but allow reducing orders.
- `read_only` and `blocked` must reject all orders.
- Healthy recovery counting should advance only on closed-bar boundaries.
- Hard failures should still escalate to `blocked`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_supervisor.py tests/test_live_readiness.py -k "healthy_cycles_to_recover or execution_mode_is_reduce_only"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_supervisor.py quantx/live_service.py tests/test_live_supervisor.py tests/test_live_readiness.py
git commit -m "feat: enforce supervisor recovery and reduce-only gates"
```

## Chunk 2: Long-Lived Runtime And Persistence

### Task 3: Build the unattended live runtime coordinator and worker loop

**Files:**
- Create: `quantx/live_runtime.py`
- Modify: `quantx/live_service.py`
- Modify: `quantx/exchanges/okx_private_stream.py`
- Create: `tests/test_live_runtime.py`
- Modify: `tests/runtime/test_private_stream.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_runtime_degrades_to_reduce_only_and_recovers_after_three_healthy_5m_cycles():
    runtime = LiveRuntime(
        config=LiveRuntimeConfig(watchlist=('BTC-USDT-SWAP',), strategy_name='cta_strategy', total_margin=1000.0),
        market_driver=_MarketDriverStub(),
        private_stream_transport=_PrivateStreamStub(),
        service=_LiveServiceStub(),
    )

    runtime.bootstrap_once()
    runtime.run_health_iteration(force_gap=True)
    assert runtime.supervisor.state == 'reduce_only'

    for _ in range(3):
        runtime.run_health_iteration(force_healthy=True, cycle_boundary=True)

    assert runtime.supervisor.state == 'live_active'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py tests/runtime/test_private_stream.py -k "reduce_only_and_recovers_after_three_healthy_5m_cycles or reconnect"`
Expected: FAIL because there is no long-lived runtime coordinator yet.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class LiveRuntimeConfig:
    watchlist: tuple[str, ...]
    strategy_name: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    total_margin: float = 0.0
    max_symbol_weight: float = 0.5
    healthy_recovery_cycles: int = 3

class LiveRuntime:
    def bootstrap_once(self) -> dict[str, Any]: ...
    def run_market_iteration(self) -> dict[str, Any]: ...
    def run_health_iteration(self, *, cycle_boundary: bool = False) -> dict[str, Any]: ...
    def run_forever(self, *, stop_event: Any | None = None) -> None: ...
```

Implementation rules:
- Keep the runtime testable via explicit `run_market_iteration()` and `run_health_iteration()` helpers.
- Reuse `LiveStrategyRunner`, `MarginAllocator`, `LiveExecutionService`, and the existing private-stream supervisor.
- Do not move business logic into the CLI.
- Use the supervisor as the single source of permission for order placement.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py tests/runtime/test_private_stream.py -k "reduce_only_and_recovers_after_three_healthy_5m_cycles or reconnect"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_runtime.py quantx/live_service.py quantx/exchanges/okx_private_stream.py tests/test_live_runtime.py tests/runtime/test_private_stream.py
git commit -m "feat: add unattended live runtime loop"
```

### Task 4: Persist runtime truth and restart-relevant state for operator status and recovery

**Files:**
- Create: `quantx/live_runtime_store.py`
- Modify: `quantx/live_runtime.py`
- Modify: `tests/test_live_runtime.py`
- Modify: `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py tests/test_bootstrap.py -k "round_trips_status_and_recovery_state or restart"`
Expected: FAIL because there is no runtime store yet.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class LiveRuntimeStore:
    status_path: Path

    def write_status(self, payload: dict[str, Any]) -> None: ...
    def read_status(self) -> dict[str, Any]: ...
```

Implementation rules:
- Persist only what the runtime needs to expose or recover: supervisor state, recovery counters, watchlist, process metadata, last closed-bar timestamps, runtime truth summary.
- Use atomic file writes.
- Keep the format human-readable JSON.
- Do not bypass bootstrap or readiness with persisted state.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_live_runtime.py tests/test_bootstrap.py -k "round_trips_status_and_recovery_state or restart"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_runtime_store.py quantx/live_runtime.py tests/test_live_runtime.py tests/test_bootstrap.py
git commit -m "feat: persist unattended runtime status"
```

## Chunk 3: CLI Launch And Operator Surfaces

### Task 5: Make `autotrade-start` launch a real runtime and `autotrade-status` read persisted runtime truth

**Files:**
- Modify: `quantx/cli.py`
- Modify: `tests/test_quantx.py`
- Modify: `docs/personal_live_go_no_go_checklist.md`
- Modify: `docs/restart_takeover_runbook.md`

- [ ] **Step 1: Write the failing test**

```python
def test_autotrade_start_spawns_runtime_process_and_status_reads_runtime_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, '_spawn_autotrade_runtime', lambda *args, **kwargs: _FakeProcess(pid=4242))

    start = cli.main([... 'autotrade-start', '--json'])
    status = cli.main([... 'autotrade-status', '--json'])

    assert start['process']['pid'] == 4242
    assert status['supervisor']['state'] in {'warming', 'live_active', 'reduce_only'}
    assert status['runtime']['execution_path'] == 'runtime_core'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= tests/test_quantx.py -k "spawns_runtime_process or reads_runtime_store"`
Expected: FAIL because `autotrade-start` still returns an in-process payload and `autotrade-status` does not yet read persisted runtime state.

- [ ] **Step 3: Write minimal implementation**

```python
def _spawn_autotrade_runtime(args) -> Any: ...

def _build_autotrade_status_payload(args) -> dict[str, object]: ...
```

Implementation rules:
- Keep `deploy --mode live` as the go/no-go evidence path.
- Add an internal `autotrade-run` command or equivalent runtime entrypoint for the child process.
- `autotrade-start` should validate artifacts, write config/state paths, spawn the runtime, and return process metadata.
- `autotrade-status` should read the runtime store rather than rebuilding a fake live snapshot.
- Keep the operator docs aligned with the real command behavior.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= tests/test_quantx.py -k "spawns_runtime_process or reads_runtime_store"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/cli.py tests/test_quantx.py docs/personal_live_go_no_go_checklist.md docs/restart_takeover_runbook.md
git commit -m "feat: launch unattended runtime from cli"
```

## Final Verification

- [ ] **Step 1: Run the focused unattended-runtime suite**

Run: `python -m pytest -q -o addopts= tests/test_okx_perp_client.py tests/test_live_market_driver.py tests/test_live_supervisor.py tests/test_live_runtime.py tests/test_live_readiness.py tests/test_bootstrap.py tests/test_quantx.py tests/runtime/test_private_stream.py`
Expected: PASS.

- [ ] **Step 2: Run the broader runtime regression suite**

Run: `python -m pytest -q -o addopts= tests/runtime/test_health.py tests/runtime/test_reconcile.py tests/runtime/test_live_coordinator.py tests/runtime/test_runtime_session.py tests/runtime/test_runtime_parity.py tests/test_exchange_clients.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS.

- [ ] **Step 3: Commit final runtime closure changes**

```bash
git add quantx tests docs
git commit -m "feat: close okx unattended live runtime mvp"
```
