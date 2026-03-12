# OKX Live Truth Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an `OKX` live runtime truth path where normalized private-stream events drive order state, funding booking, replay persistence, reconciliation, and warm recovery from one auditable runtime core.

**Architecture:** Add a focused live coordinator and reconciliation layer on top of the existing runtime session. Extend the `OKX` adapter and replay store so live `order/fill/funding/snapshot` events share one normalized event model, then route live service, bootstrap, replay, readiness, and CLI through those components without introducing snapshot-based auto-heal.

**Tech Stack:** Python 3.10+, dataclasses, pytest, existing QuantX runtime modules, OKX perpetual adapter, JSONL replay logs.

---

## File Map

**Create**
- `quantx/runtime/live_coordinator.py` - owns live intent submission, normalized event application, replay persistence, and degraded-mode flags.
- `quantx/runtime/reconcile.py` - compares runtime truth against observed exchange snapshots and produces mismatch reports.
- `tests/runtime/test_live_coordinator.py` - coordinator tests for persistence, funding booking, duplicate/idempotent handling, and snapshot output.
- `tests/runtime/test_reconcile.py` - reconciliation severity and mismatch-report coverage.
- `tests/fixtures/okx_live_truth_events.jsonl` - normalized live event fixture with `acked/fill/funding/position_snapshot/account_snapshot` records.

**Modify**
- `quantx/runtime/events.py` - keep live event typing explicit enough for funding and reconciliation-only snapshots.
- `quantx/runtime/session.py` - store observed exchange state separately from truth-bearing ledger state and surface it in snapshots.
- `quantx/runtime/replay_store.py` - add helpers for live-truth loading, day filtering, and warm-recovery replays.
- `quantx/runtime/__init__.py` - export live coordinator and reconcile helpers.
- `quantx/exchanges/okx_perp.py` - normalize funding plus reconciliation-only `position/account` snapshots.
- `quantx/live_service.py` - route live execution through the new coordinator and expose ingestion hooks for private-stream events.
- `quantx/bootstrap.py` - prefer warm recovery from runtime replay and fall back to cold degraded recovery when replay is incomplete.
- `quantx/replay.py` - include funding and live-truth replay semantics in daily replay summaries.
- `quantx/readiness.py` - gate `micro_live/normal_live` on replay persistence, degraded state, and reconcile status.
- `quantx/cli.py` - expose live-truth health, recovery mode, and stage-gate metadata.
- `tests/runtime/test_okx_perp.py` - adapter coverage for funding and reconciliation-only snapshots.
- `tests/runtime/test_runtime_session.py` - session coverage for funding booking vs observed exchange snapshots.
- `tests/test_bootstrap.py` - warm/cold recovery coverage.
- `tests/test_live_readiness.py` - live service, reconcile, and stage-gate coverage.
- `tests/test_replay.py` - replay summary coverage for funding/live truth.
- `tests/test_quantx.py` - CLI/runtime acceptance coverage for deploy/live truth visibility.

## Chunk 1: Runtime Live Event Semantics

### Task 1: Separate truth-bearing ledger updates from observed exchange snapshots

**Files:**
- Modify: `quantx/runtime/session.py`
- Modify: `quantx/runtime/events.py`
- Modify: `tests/runtime/test_runtime_session.py`

- [ ] **Step 1: Write the failing test**

```python
def test_runtime_session_books_funding_without_rewriting_truth_from_position_snapshot():
    session = RuntimeSession(mode='live', wallet_balance=1000.0)
    session.apply_events([
        FillEvent(
            symbol='BTC-USDT-SWAP',
            exchange='okx',
            ts='2026-03-12T00:00:01+00:00',
            client_order_id='cid-1',
            exchange_order_id='oid-1',
            trade_id='tid-1',
            side='buy',
            position_side='long',
            qty=1.0,
            price=100.0,
            fee=0.1,
            payload={},
        ),
        AccountEvent(
            exchange='okx',
            ts='2026-03-12T08:00:00+00:00',
            event_type='funding',
            payload={'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'amount': -0.2},
        ),
        AccountEvent(
            exchange='okx',
            ts='2026-03-12T08:00:01+00:00',
            event_type='position_snapshot',
            payload={'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'qty': 2.0, 'avg_entry_price': 101.0},
        ),
    ])

    snapshot = session.snapshot()

    assert snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 1.0
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
    assert snapshot['observed_exchange']['positions']['BTC-USDT-SWAP']['long']['qty'] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_session.py -k funding_without_rewriting_truth`
Expected: FAIL because `RuntimeSession.snapshot()` does not expose `observed_exchange` and position snapshots are not yet separated from ledger truth.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class RuntimeSession:
    ...
    _observed_exchange_positions: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    _observed_exchange_account: dict[str, float] = field(default_factory=dict)

    def apply_events(self, events):
        ...
        elif isinstance(event, AccountEvent) and event.event_type == 'funding':
            self.ledger_engine.apply_account_event(event)
        elif isinstance(event, AccountEvent) and event.event_type == 'position_snapshot':
            self._store_position_snapshot(event)
        elif isinstance(event, AccountEvent) and event.event_type == 'account_snapshot':
            self._store_account_snapshot(event)
```

Keep the implementation small: booking events change the ledger, reconciliation-only snapshots only populate an observed-exchange view, and `snapshot()` surfaces both.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_session.py -k funding_without_rewriting_truth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_runtime_session.py quantx/runtime/session.py quantx/runtime/events.py
git commit -m "feat: preserve observed exchange state beside runtime truth"
```

### Task 2: Normalize OKX funding and reconciliation-only snapshot events

**Files:**
- Modify: `quantx/exchanges/okx_perp.py`
- Modify: `tests/runtime/test_okx_perp.py`

- [ ] **Step 1: Write the failing test**

```python
def test_okx_perp_adapter_normalizes_funding_and_reconciliation_only_snapshots():
    adapter = OKXPerpAdapter()

    funding = adapter.normalize_funding_event(
        {
            'instId': 'BTC-USDT-SWAP',
            'posSide': 'long',
            'funding': '-0.2',
            'ts': '1710230400000',
        }
    )
    position = adapter.normalize_position_event(
        {
            'instId': 'BTC-USDT-SWAP',
            'posSide': 'long',
            'pos': '2',
            'avgPx': '101',
            'mgnMode': 'cross',
            'uTime': '1710201602000',
        }
    )
    account = adapter.normalize_account_event(
        {
            'ccy': 'USDT',
            'eq': '1000',
            'availEq': '800',
            'imr': '120',
            'mmr': '50',
            'upl': '25',
            'uTime': '1710201603000',
        }
    )

    assert funding.event_type == 'funding'
    assert funding.payload['amount'] == -0.2
    assert position.event_type == 'position_snapshot'
    assert account.event_type == 'account_snapshot'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_okx_perp.py -k funding_and_reconciliation_only_snapshots`
Expected: FAIL with missing `normalize_funding_event` and/or wrong `event_type` values.

- [ ] **Step 3: Write minimal implementation**

```python
class OKXPerpAdapter:
    ...
    def normalize_position_event(self, payload):
        return AccountEvent(..., event_type='position_snapshot', ...)

    def normalize_account_event(self, payload):
        return AccountEvent(..., event_type='account_snapshot', ...)

    def normalize_funding_event(self, payload):
        return AccountEvent(
            exchange=self.exchange,
            ts=self._normalize_ts(payload.get('ts')),
            event_type='funding',
            payload={
                'symbol': str(payload.get('instId', '')).upper(),
                'position_side': str(payload.get('posSide', 'long')).lower(),
                'amount': float(payload.get('funding', 0.0) or 0.0),
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_okx_perp.py -k funding_and_reconciliation_only_snapshots`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_okx_perp.py quantx/exchanges/okx_perp.py
git commit -m "feat: normalize okx live funding and snapshot events"
```

## Chunk 2: Live Coordinator And Replay Persistence

### Task 3: Add a live runtime coordinator that persists normalized events

**Files:**
- Create: `quantx/runtime/live_coordinator.py`
- Modify: `quantx/runtime/replay_store.py`
- Modify: `quantx/runtime/__init__.py`
- Create: `tests/runtime/test_live_coordinator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_runtime_coordinator_persists_submit_fill_and_funding_events(tmp_path):
    store = RuntimeReplayStore(str(tmp_path / 'runtime' / 'events.jsonl'))
    coordinator = LiveRuntimeCoordinator(
        session=RuntimeSession(mode='live', wallet_balance=1000.0),
        replay_store=store,
    )
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='market',
        time_in_force='ioc',
        reduce_only=False,
        intent_id='cid-1',
    )

    coordinator.submit_intents([intent], exchange='okx', ts='2026-03-12T00:00:00+00:00')
    coordinator.apply_event(OrderEvent(symbol='BTC-USDT-SWAP', exchange='okx', ts='2026-03-12T00:00:01+00:00', client_order_id='cid-1', exchange_order_id='oid-1', status='acked', payload={}))
    coordinator.apply_event(FillEvent(symbol='BTC-USDT-SWAP', exchange='okx', ts='2026-03-12T00:00:02+00:00', client_order_id='cid-1', exchange_order_id='oid-1', trade_id='tid-1', side='buy', position_side='long', qty=1.0, price=100.0, fee=0.1, payload={}))
    coordinator.apply_event(AccountEvent(exchange='okx', ts='2026-03-12T08:00:00+00:00', event_type='funding', payload={'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'amount': -0.2}))

    rows, invalid = store.load()
    snapshot = coordinator.snapshot()

    assert invalid == 0
    assert [row['kind'] for row in rows] == ['order_event', 'order_event', 'order_event', 'order_event', 'fill_event', 'account_event']
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_live_coordinator.py`
Expected: FAIL with missing `LiveRuntimeCoordinator` import or missing replay persistence.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class LiveRuntimeCoordinator:
    session: RuntimeSession
    replay_store: RuntimeReplayStore
    degraded: bool = False

    def submit_intents(self, intents, *, exchange: str, ts: str):
        emitted = self.session.submit_intents(intents, exchange=exchange, ts=ts)
        for event in emitted:
            self.replay_store.append(event)
        return emitted

    def apply_event(self, event):
        self.replay_store.append(event)
        self.session.apply_events([event])
        return event
```

Also add a tiny replay-store helper if the tests need one, but do not over-design the API yet.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_live_coordinator.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_live_coordinator.py quantx/runtime/live_coordinator.py quantx/runtime/replay_store.py quantx/runtime/__init__.py
git commit -m "feat: add live runtime coordinator"
```

### Task 4: Route live execution through the coordinator and replay store

**Files:**
- Modify: `quantx/live_service.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_execution_service_ingests_private_stream_events_into_runtime_truth(tmp_path):
    adapter = OKXPerpAdapter()
    svc = LiveExecutionService(
        _DummyOKXPerpExchange(),
        config=LiveExecutionConfig(dry_run=False, max_retries=0, runtime_mode='derivatives', exchange='okx'),
        runtime_adapter=adapter,
        runtime_event_log_path=str(tmp_path / 'runtime' / 'events.jsonl'),
    )
    svc.sync_symbol_rules(['BTCUSDT'])
    svc.execute_orders([
        {'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'price': 100000.0, 'position_side': 'long'}
    ])

    svc.ingest_runtime_event(
        adapter.normalize_fill_event(
            {'instId': 'BTC-USDT-SWAP', 'clOrdId': 'cid-1', 'ordId': 'oid-1', 'tradeId': 'tid-1', 'fillSz': '0.01', 'fillPx': '100000', 'fillFee': '-0.1', 'side': 'buy', 'posSide': 'long', 'tdMode': 'cross', 'fillTime': '1710201601000'}
        )
    )
    svc.ingest_runtime_event(
        adapter.normalize_funding_event(
            {'instId': 'BTC-USDT-SWAP', 'posSide': 'long', 'funding': '-0.2', 'ts': '1710230400000'}
        )
    )

    snapshot = svc.runtime_snapshot()

    assert snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 0.01
    assert snapshot['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
    assert snapshot['observed_exchange']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k ingests_private_stream_events_into_runtime_truth`
Expected: FAIL because `LiveExecutionService` does not yet expose a coordinator-backed ingestion path or replay-store-backed runtime state.

- [ ] **Step 3: Write minimal implementation**

```python
class LiveExecutionService:
    def __init__(..., runtime_event_log_path: str | None = None, ...):
        self.runtime_coordinator = LiveRuntimeCoordinator(...)

    def execute_orders(self, orders):
        ...
        self.runtime_coordinator.submit_intents([...], exchange=self.config.exchange, ts=ts)
        ...

    def ingest_runtime_event(self, event):
        self.runtime_coordinator.apply_event(event)

    def runtime_snapshot(self):
        return self.runtime_coordinator.snapshot()
```

Keep dry-run behavior intact; just move live truth ownership out of ad hoc `RuntimeSession` calls and into the coordinator.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k ingests_private_stream_events_into_runtime_truth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py tests/test_quantx.py quantx/live_service.py
git commit -m "refactor: route okx live execution through runtime coordinator"
```

## Chunk 3: Reconciliation And Recovery

### Task 5: Add reconciliation reports without auto-heal

**Files:**
- Create: `quantx/runtime/reconcile.py`
- Create: `tests/runtime/test_reconcile.py`
- Modify: `quantx/runtime/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_report_flags_position_and_margin_mismatch_without_rewriting_runtime_truth():
    runtime_snapshot = {
        'positions': {'BTC-USDT-SWAP': {'long': {'qty': 1.0, 'avg_entry_price': 100.0, 'funding_total': -0.2}}},
        'ledger': {'equity': 999.7, 'available_margin': 899.7, 'used_margin': 100.0, 'maintenance_margin': 50.0},
        'observed_exchange': {
            'positions': {'BTC-USDT-SWAP': {'long': {'qty': 2.0, 'avg_entry_price': 101.0}}},
            'account': {'equity': 980.0, 'available_margin': 870.0, 'used_margin': 110.0, 'maintenance_margin': 55.0},
        },
    }

    report = build_reconcile_report(runtime_snapshot)

    assert report['ok'] is False
    assert report['position_mismatches']['BTC-USDT-SWAP']['runtime_qty'] == 1.0
    assert report['position_mismatches']['BTC-USDT-SWAP']['exchange_qty'] == 2.0
    assert runtime_snapshot['positions']['BTC-USDT-SWAP']['long']['qty'] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_reconcile.py`
Expected: FAIL with missing `build_reconcile_report`.

- [ ] **Step 3: Write minimal implementation**

```python
def build_reconcile_report(runtime_snapshot: dict[str, Any], *, qty_tolerance: float = 1e-9) -> dict[str, Any]:
    ...
    return {
        'ok': not position_mismatches and not account_mismatches,
        'position_mismatches': position_mismatches,
        'account_mismatches': account_mismatches,
        'severity': 'warn' if ... else 'ok',
    }
```

Focus on deterministic mismatch reporting only. Do not add mutation or repair code.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_reconcile.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_reconcile.py quantx/runtime/reconcile.py quantx/runtime/__init__.py
git commit -m "feat: add runtime reconciliation reports"
```

### Task 6: Prefer warm recovery from runtime replay and fall back to cold degraded recovery

**Files:**
- Modify: `quantx/bootstrap.py`
- Modify: `quantx/runtime/replay_store.py`
- Modify: `tests/test_bootstrap.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bootstrap_recover_and_reconcile_uses_runtime_replay_for_warm_recovery(tmp_path):
    replay = RuntimeReplayStore(str(tmp_path / 'runtime' / 'events.jsonl'))
    replay.append(OrderEvent(symbol='BTC-USDT-SWAP', exchange='okx', ts='2026-03-12T00:00:00+00:00', client_order_id='cid-1', exchange_order_id='oid-1', status='acked', payload={}))
    replay.append(FillEvent(symbol='BTC-USDT-SWAP', exchange='okx', ts='2026-03-12T00:00:01+00:00', client_order_id='cid-1', exchange_order_id='oid-1', trade_id='tid-1', side='buy', position_side='long', qty=0.25, price=100000.0, fee=0.0, payload={}))
    replay.append(AccountEvent(exchange='okx', ts='2026-03-12T08:00:00+00:00', event_type='funding', payload={'symbol': 'BTC-USDT-SWAP', 'position_side': 'long', 'amount': -0.2}))

    report = bootstrap_recover_and_reconcile(
        service=_StubLiveTruthService(...),
        oms_store=JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl')),
        runtime_event_log_path=str(replay.path),
        initial_cash=1000.0,
        symbol='BTC-USDT-SWAP',
    )

    assert report['recovery_mode'] == 'warm'
    assert report['runtime_positions']['BTC-USDT-SWAP']['long']['qty'] == 0.25
    assert report['runtime_positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_bootstrap.py -k runtime_replay_for_warm_recovery`
Expected: FAIL because bootstrap still centers on `OMS` recovery instead of runtime replay recovery.

- [ ] **Step 3: Write minimal implementation**

```python
def bootstrap_recover_and_reconcile(..., runtime_event_log_path: str | None = None, ...):
    if runtime_event_log_path and RuntimeReplayStore(runtime_event_log_path).path.exists():
        recovery_mode = 'warm'
        runtime_snapshot = rebuild_runtime_snapshot_from_replay(...)
    else:
        recovery_mode = 'cold'
        runtime_snapshot = {'positions': {}, 'observed_exchange': {}, 'ledger': {}}
```

Keep the first version simple: prefer runtime replay when available, mark cold recovery explicitly, and feed both modes through the same reconciliation report.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_bootstrap.py -k runtime_replay_for_warm_recovery`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_bootstrap.py tests/test_live_readiness.py quantx/bootstrap.py quantx/runtime/replay_store.py
git commit -m "refactor: recover live truth from runtime replay"
```

## Chunk 4: Rollout Gates And Acceptance

### Task 7: Gate live rollout on replay persistence, degraded state, and reconcile health

**Files:**
- Modify: `quantx/readiness.py`
- Modify: `quantx/cli.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing test**

```python
def test_readiness_blocks_normal_live_when_runtime_truth_is_degraded_or_unrecoverable(tmp_path):
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=False,
            allowed_symbols=('BTC-USDT-SWAP',),
            max_orders_per_cycle=5,
            max_notional_per_cycle=50000.0,
            runtime_mode='derivatives',
            exchange='okx',
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=10000.0),
        alert_router=_router_with_webhook(),
        oms_store=JsonlOMSStore(str(tmp_path / 'oms' / 'events.jsonl')),
        runtime_status={'replay_persistence': False, 'degraded': True, 'reconcile_ok': False},
    )

    report = evaluate_readiness(ctx)
    checks = {check['name']: check for check in report.checks}

    assert checks['live_truth_replay_persistence']['ok'] is False
    assert checks['live_truth_not_degraded']['ok'] is False
    assert checks['live_truth_reconcile_ok']['ok'] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k runtime_truth_is_degraded_or_unrecoverable`
Expected: FAIL because readiness does not yet inspect runtime live-truth health.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class ReadinessContext:
    ...
    runtime_status: dict[str, Any] | None = None

_append_check(checks, 'live_truth_replay_persistence', bool(runtime_status.get('replay_persistence')), ...)
_append_check(checks, 'live_truth_not_degraded', not bool(runtime_status.get('degraded')), ...)
_append_check(checks, 'live_truth_reconcile_ok', bool(runtime_status.get('reconcile_ok')), ...)
```

Also have the CLI surface this runtime status in deploy-style payloads.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k runtime_truth_is_degraded_or_unrecoverable`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py tests/test_quantx.py quantx/readiness.py quantx/cli.py
git commit -m "feat: add live truth rollout gates"
```

### Task 8: Add OKX live truth acceptance coverage across replay, recovery, and CLI

**Files:**
- Modify: `quantx/replay.py`
- Modify: `tests/test_replay.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`
- Add: `tests/fixtures/okx_live_truth_events.jsonl`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_daily_replay_report_reconstructs_live_truth_with_funding_and_order_lineage():
    rep = build_daily_replay_report(event_log_path='tests/fixtures/okx_live_truth_events.jsonl', day='2026-03-12')

    assert rep['runtime_summary']['order_state_sequences']['cid-1'] == ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working', 'filled']
    assert rep['runtime_summary']['positions']['BTC-USDT-SWAP']['long']['funding_total'] == -0.2
    assert rep['drift_metrics']['paper_vs_live']['funding_booking_drift'] >= 0.0


def test_deploy_payload_surfaces_live_truth_health_and_recovery_mode():
    payload = main(['deploy', '--json', '--symbol', 'BTC-USDT-SWAP'])

    assert payload['runtime']['execution_path'] == 'runtime_core'
    assert 'runtime_truth' in payload['runtime']
    assert 'recovery_mode' in payload['runtime']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py -k "live_truth or funding or recovery_mode"`
Expected: FAIL until replay summaries, recovery metadata, and CLI payloads expose the new live-truth details.

- [ ] **Step 3: Write minimal implementation**

```python
# Keep this task as the integration-tightening pass.
# Only patch the pieces the new acceptance tests expose:
# - funding in replay summary
# - recovery mode/report visibility
# - deploy/runtime payload live-truth metadata
```

Do not add new abstractions here unless the acceptance failures force them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_session.py tests/runtime/test_okx_perp.py tests/runtime/test_live_coordinator.py tests/runtime/test_reconcile.py tests/test_bootstrap.py tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_runtime_session.py tests/runtime/test_okx_perp.py tests/runtime/test_live_coordinator.py tests/runtime/test_reconcile.py tests/test_bootstrap.py tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py tests/fixtures/okx_live_truth_events.jsonl quantx/replay.py quantx/live_service.py quantx/bootstrap.py quantx/readiness.py quantx/cli.py quantx/runtime/live_coordinator.py quantx/runtime/reconcile.py quantx/runtime/session.py quantx/runtime/replay_store.py quantx/exchanges/okx_perp.py quantx/runtime/__init__.py
git commit -m "test: add okx live truth acceptance coverage"
```

## Notes For Execution

- Use @superpowers:using-git-worktrees before implementation, because this plan should execute in an isolated worktree.
- Use @superpowers:test-driven-development for every task.
- Use @superpowers:verification-before-completion before claiming any chunk is complete.
- Keep `fill_event` and `funding` as the only ledger-mutating live events in the first version.
- Keep `position_snapshot` and `account_snapshot` reconciliation-only in the first version.
- If the harness does not provide the `plan-document-reviewer` subagent, do a manual chunk review before moving to the next chunk and record any assumptions in execution notes or commit messages.

Plan complete and saved to `docs/superpowers/plans/2026-03-12-okx-live-truth.md`. Ready to execute?
