# Event-First Runtime Closure Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the event-first derivatives runtime so `event_strategy` is the primary production path and `backtest`, `paper`, and `live` all use one auditable execution loop.

**Architecture:** Add a focused runtime coordination layer on top of the existing order, ledger, risk, fill, and adapter primitives. Introduce `strategy_runtime`, `runtime_session`, and a paper exchange simulator, then route backtest, paper, replay, and live services through those shared components so drift is measured from separate live and paper executions on the same market tape.

**Tech Stack:** Python 3.10+, dataclasses, pytest, existing QuantX runtime modules, JSONL replay logs, OKX/Binance adapters.

---

## File Map

**Create**
- `quantx/runtime/strategy_runtime.py` - strategy contracts, legacy bar adapter, read-only strategy context, intent stamping.
- `quantx/runtime/session.py` - runtime coordinator that owns risk validation, order submission, event application, and snapshots.
- `quantx/runtime/paper_exchange.py` - exchange-like paper simulator built on the shared runtime session and fill engine.
- `tests/runtime/test_strategy_runtime.py` - event/bar strategy contract coverage and legacy strategy adapter tests.
- `tests/runtime/test_runtime_session.py` - runtime session tests for intent submission, rejection, event application, and snapshots.
- `tests/runtime/test_event_backtest.py` - event-backtest closure tests on market-event tapes.
- `tests/runtime/test_paper_exchange.py` - paper simulator tests for queue delay, partial fills, cancel delay, and rejects.
- `tests/fixtures/runtime_market_tape.jsonl` - normalized market tape fixture used by replay and drift tests.

**Modify**
- `quantx/runtime/models.py` - extend `OrderIntent` and `TrackedOrder` with trace metadata used by replay and attribution.
- `quantx/runtime/__init__.py` - export new runtime pieces.
- `quantx/strategies.py` - add event/bar base classes and compatibility hooks for legacy `signal(...) -> int` strategies.
- `quantx/backtest.py` - add `run_event_backtest`, convert bar backtest to shared runtime execution, and label backtest fidelity.
- `quantx/execution.py` - replace direct paper fill orchestration with the paper exchange simulator.
- `quantx/replay.py` - split live summary from paper replay summary and compute real paper-vs-live drift.
- `quantx/runtime/replay_store.py` - add helpers needed to load and filter market tape vs order/fill/account events cleanly.
- `quantx/live_service.py` - keep runtime-owned order and ledger state instead of returning normalized events only.
- `quantx/live_pipeline.py` - propagate runtime-owned snapshots and runtime events through live orchestration.
- `quantx/bootstrap.py` - recover and reconcile from runtime ledger truth and replay state.
- `quantx/readiness.py` - add rollout-stage checks for replay closure, paper closure, and micro-live readiness.
- `quantx/cli.py` - expose runtime fidelity, replay drift, and stage-gate metadata from the unified runtime path.
- `tests/runtime/test_events_models.py` - metadata and trace-field coverage.
- `tests/runtime/test_runtime_parity.py` - parity tests across `event_backtest`, `paper`, and `live_replay`.
- `tests/test_quantx.py` - integration tests for backtest, paper execution, and CLI compatibility.
- `tests/test_replay.py` - replay drift tests using a separate paper rerun on the same market tape.
- `tests/test_live_readiness.py` - live runtime, recovery, and rollout-stage tests.

## Chunk 1: Strategy And Runtime Contracts

### Task 1: Add trace metadata to runtime intents and tracked orders

**Files:**
- Modify: `quantx/runtime/models.py`
- Modify: `tests/runtime/test_events_models.py`

- [ ] **Step 1: Write the failing test**

```python
def test_model_order_intent_carries_strategy_trace_metadata():
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100000.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
        intent_id='intent-1',
        strategy_id='scalp-v1',
        signal_id='sig-1',
        reason='breakout_retest',
        created_ts='2026-03-12T00:00:00+00:00',
        tags=('scalp', 'event'),
    )

    tracked = TrackedOrder(
        client_order_id='cid-1',
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        order_type='limit',
        time_in_force='gtc',
        strategy_id='scalp-v1',
        intent_id='intent-1',
    )

    assert intent.strategy_id == 'scalp-v1'
    assert tracked.intent_id == 'intent-1'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_events_models.py -k trace_metadata`
Expected: FAIL with `TypeError` for unexpected keyword arguments on `OrderIntent` / `TrackedOrder`.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class OrderIntent:
    ...
    intent_id: str | None = None
    strategy_id: str | None = None
    signal_id: str | None = None
    reason: str | None = None
    created_ts: str | None = None
    tags: tuple[str, ...] = ()

@dataclass(slots=True)
class TrackedOrder:
    ...
    intent_id: str | None = None
    strategy_id: str | None = None
    signal_id: str | None = None
    reason: str | None = None
    created_ts: str | None = None
    tags: tuple[str, ...] = ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_events_models.py -k trace_metadata`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_events_models.py quantx/runtime/models.py
git commit -m "feat: add runtime intent trace metadata"
```

### Task 2: Introduce event/bar strategy contracts and the strategy runtime adapter layer

**Files:**
- Create: `quantx/runtime/strategy_runtime.py`
- Modify: `quantx/runtime/__init__.py`
- Modify: `quantx/strategies.py`
- Create: `tests/runtime/test_strategy_runtime.py`

- [ ] **Step 1: Write the failing tests**

```python
class DummyEventStrategy(BaseEventStrategy):
    strategy_id = 'dummy-event'

    def on_event(self, ctx, event):
        return [
            OrderIntent(
                symbol=event.symbol,
                side='buy',
                position_side='long',
                qty=1.0,
                price=event.payload['price'],
                order_type='limit',
                time_in_force='gtc',
                reduce_only=False,
            )
        ]


def test_strategy_runtime_stamps_intents_from_event_strategy():
    runtime = StrategyRuntime(strategy=DummyEventStrategy())
    intents = runtime.on_event(make_market_event(price=100.0))

    assert len(intents) == 1
    assert intents[0].strategy_id == 'dummy-event'
    assert intents[0].intent_id.startswith('dummy-event-')


def test_legacy_signal_strategy_adapts_to_bar_contract():
    legacy = FixedFlipStrategy()
    adapter = LegacySignalBarStrategyAdapter(legacy, symbol='SOLUSDT')
    intents = adapter.on_bar(make_bar(close=101.0), bar_index=1)

    assert intents[0].side == 'buy'
    assert intents[0].position_side == 'long'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_strategy_runtime.py`
Expected: FAIL with missing imports for `StrategyRuntime`, `BaseEventStrategy`, or `LegacySignalBarStrategyAdapter`.

- [ ] **Step 3: Write minimal implementation**

```python
class BaseEventStrategy:
    strategy_id = 'event'

    def on_event(self, ctx, event):
        raise NotImplementedError


class BaseBarStrategy:
    strategy_id = 'bar'

    def on_bar(self, ctx, bar):
        raise NotImplementedError


@dataclass(slots=True)
class StrategyRuntime:
    strategy: BaseEventStrategy | BaseBarStrategy
    _intent_seq: int = 0

    def on_event(self, event):
        raw = self.strategy.on_event(self._ctx(), event)
        return self._stamp(raw)
```

Keep the implementation small: only create the strategy interfaces, a read-only context object, intent stamping, and the legacy bar adapter needed for existing `signal(...)` strategies.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_strategy_runtime.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_strategy_runtime.py quantx/runtime/strategy_runtime.py quantx/runtime/__init__.py quantx/strategies.py
git commit -m "feat: add strategy runtime contracts"
```

### Task 3: Add a runtime session coordinator for intents, events, and snapshots

**Files:**
- Create: `quantx/runtime/session.py`
- Modify: `quantx/runtime/__init__.py`
- Create: `tests/runtime/test_runtime_session.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_runtime_session_submits_intents_and_records_state_sequence():
    session = RuntimeSession(mode='paper', wallet_balance=1000.0)
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=False,
        strategy_id='dummy-event',
    )

    events = session.submit_intents([intent], exchange='paper', ts='2026-03-12T00:00:00+00:00')
    snapshot = session.snapshot()

    assert any(ev.status == 'risk_accepted' for ev in events if hasattr(ev, 'status'))
    assert list(snapshot['order_state_sequences'].values())[0][0] == 'intent_created'


def test_runtime_session_rejects_bad_reduce_only_before_submission():
    session = RuntimeSession(mode='paper', wallet_balance=1000.0)
    intent = OrderIntent(
        symbol='BTC-USDT-SWAP',
        side='buy',
        position_side='long',
        qty=1.0,
        price=100.0,
        order_type='limit',
        time_in_force='gtc',
        reduce_only=True,
    )

    events = session.submit_intents([intent], exchange='paper', ts='2026-03-12T00:00:00+00:00')
    assert events[-1].status == 'rejected'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_session.py`
Expected: FAIL because `RuntimeSession` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class RuntimeSession:
    mode: str
    wallet_balance: float = 0.0
    order_engine: OrderEngine = field(default_factory=OrderEngine)
    ledger_engine: LedgerEngine = field(init=False)
    risk_validator: RuntimeRiskValidator = field(default_factory=RuntimeRiskValidator)

    def submit_intents(self, intents, *, exchange, ts):
        ...

    def apply_events(self, events):
        ...

    def snapshot(self):
        ...
```

The first version only needs to coordinate existing order, ledger, and risk modules and expose a reusable snapshot format.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_session.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_runtime_session.py quantx/runtime/session.py quantx/runtime/__init__.py
git commit -m "feat: add runtime session coordinator"
```

## Chunk 2: Backtest And Paper Closure

### Task 4: Add `event_backtest` as the primary backtest path on the shared runtime session

**Files:**
- Create: `tests/runtime/test_event_backtest.py`
- Modify: `quantx/backtest.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
class DummyImpulseEventStrategy(BaseEventStrategy):
    strategy_id = 'impulse'

    def on_event(self, ctx, event):
        if event.kind.value == 'market_event' and event.payload['price'] <= 100.0:
            return [
                OrderIntent(
                    symbol=event.symbol,
                    side='buy',
                    position_side='long',
                    qty=1.0,
                    price=event.payload['price'],
                    order_type='market',
                    time_in_force='ioc',
                    reduce_only=False,
                )
            ]
        return []


def test_event_backtest_replays_market_tape_through_runtime_session():
    tape = make_market_tape([101.0, 100.0, 103.0])
    res = run_event_backtest(tape, DummyImpulseEventStrategy(), BacktestConfig(symbol='SOLUSDT', timeframe='event'))

    assert res.extra['runtime']['mode'] == 'event_backtest'
    assert res.extra['runtime']['fidelity'] == 'high'
    assert res.extra['runtime']['orders'][0]['status'] == 'filled'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_event_backtest.py tests/test_quantx.py -k event_backtest`
Expected: FAIL with missing `run_event_backtest`.

- [ ] **Step 3: Write minimal implementation**

```python
def run_event_backtest(event_tape, strategy, config):
    strategy_runtime = StrategyRuntime(strategy=strategy)
    session = RuntimeSession(mode='event_backtest', wallet_balance=config.initial_cash)
    ...
    return BacktestResult(..., extra={'runtime': session.snapshot() | {'fidelity': 'high'}})
```

Keep the first version narrow: only replay normalized market tapes, feed the event strategy, submit intents through `RuntimeSession`, and derive the final runtime snapshot from session state.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_event_backtest.py tests/test_quantx.py -k event_backtest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_event_backtest.py tests/test_quantx.py quantx/backtest.py
git commit -m "feat: add event backtest runtime path"
```

### Task 5: Rebase bar backtest on the same runtime session and label low-fidelity runs

**Files:**
- Modify: `quantx/backtest.py`
- Modify: `tests/runtime/test_runtime_parity.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_bar_backtest_uses_runtime_session_and_reports_low_fidelity():
    res = run_backtest(candles, 'fixed_flip', {}, BacktestConfig(symbol='SOLUSDT', timeframe='1h'))

    assert res.extra['runtime']['mode'] == 'bar_backtest'
    assert res.extra['runtime']['fidelity'] == 'low'
    assert res.extra['runtime']['ledger']['equity'] == pytest.approx(res.equity_curve[-1][1])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_parity.py tests/test_quantx.py -k backtest`
Expected: FAIL because the current backtest path still mirrors legacy cash/position mutations instead of using the runtime session as the source of truth.

- [ ] **Step 3: Write minimal implementation**

```python
def run_backtest(...):
    adapter = LegacySignalBarStrategyAdapter(strategy, symbol=config.symbol)
    session = RuntimeSession(mode='bar_backtest', wallet_balance=config.initial_cash)
    ...
    extra['runtime'] = session.snapshot() | {'fidelity': 'low'}
```

Refactor `run_backtest` so order creation, fills, and ledger updates are runtime-owned. Keep report outputs stable, but remove the old pattern where runtime state is only reconstructed after the fact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_parity.py tests/test_quantx.py -k backtest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_runtime_parity.py tests/test_quantx.py quantx/backtest.py
git commit -m "refactor: route bar backtest through runtime session"
```

### Task 6: Add a paper exchange simulator and route paper execution through it

**Files:**
- Create: `quantx/runtime/paper_exchange.py`
- Create: `tests/runtime/test_paper_exchange.py`
- Modify: `quantx/execution.py`
- Modify: `tests/runtime/test_runtime_parity.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_paper_exchange_emits_ack_partial_fill_and_cancel_events():
    exchange = PaperExchangeSimulator(initial_cash=1000.0, config=PaperExchangeConfig(partial_fill_ratio=0.5))
    events = exchange.submit_intents([make_buy_intent()], exchange_name='paper', ts='2026-03-12T00:00:00+00:00')
    events += exchange.on_market_event(make_mark_event(price=100.0))
    events += exchange.cancel_order(client_order_id='paper-1', ts='2026-03-12T00:00:02+00:00')

    statuses = [ev.status for ev in events if hasattr(ev, 'status')]
    assert 'acked' in statuses
    assert 'partially_filled' in statuses or 'filled' in statuses


def test_paper_executor_uses_paper_exchange_snapshot_as_runtime_state():
    ex = PaperLiveExecutor('paper')
    ex.arm()
    rec = ex.place_order('BTCUSDT', 'BUY', 0.5, order_type='market', market_price=100.0, position_side='long')

    assert rec['accepted'] is True
    assert ex.state.runtime['mode'] == 'paper'
    assert ex.state.runtime['orders']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_paper_exchange.py tests/runtime/test_runtime_parity.py tests/test_quantx.py -k paper`
Expected: FAIL because `PaperExchangeSimulator` does not exist and `PaperLiveExecutor` still owns orchestration logic directly.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class PaperExchangeSimulator:
    session: RuntimeSession
    fill_engine: FillEngine

    def submit_intents(self, intents, *, exchange_name, ts):
        return self.session.submit_intents(intents, exchange=exchange_name, ts=ts)

    def on_market_event(self, event):
        generated = self.fill_engine.on_market_event(event)
        return self.session.apply_events(generated)
```

Move queue delay, partial fills, cancel delay, and reject handling behind this simulator. `PaperLiveExecutor` should become a thin compatibility wrapper that delegates to the simulator.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_paper_exchange.py tests/runtime/test_runtime_parity.py tests/test_quantx.py -k paper`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_paper_exchange.py tests/runtime/test_runtime_parity.py tests/test_quantx.py quantx/runtime/paper_exchange.py quantx/execution.py
git commit -m "feat: add paper exchange simulator"
```

## Chunk 3: Replay And Live Closure

### Task 7: Rework replay so paper-vs-live drift uses separate execution paths on the same market tape

**Files:**
- Create: `tests/fixtures/runtime_market_tape.jsonl`
- Modify: `quantx/replay.py`
- Modify: `quantx/runtime/replay_store.py`
- Modify: `tests/test_replay.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_daily_replay_report_reruns_paper_on_market_tape(tmp_path):
    rep = build_daily_replay_report(
        event_log_path='tests/fixtures/runtime_market_tape.jsonl',
        day='2026-03-12',
    )

    assert rep['runtime_summary']['mode'] == 'live_replay'
    assert rep['paper_summary']['mode'] == 'paper_replay'
    assert 'paper_vs_live' in rep['drift_metrics']
    assert rep['paper_summary'] != rep['runtime_summary']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_replay.py -k market_tape`
Expected: FAIL because `build_daily_replay_report` currently derives `paper_summary` from the same live events instead of rerunning paper.

- [ ] **Step 3: Write minimal implementation**

```python
def _extract_market_tape(events):
    return [ev for ev in events if ev.get('kind') == 'market_event']


def _rerun_paper_summary(market_tape):
    simulator = PaperExchangeSimulator(...)
    ...
    return simulator.snapshot() | {'mode': 'paper_replay'}
```

Keep the first version simple: rebuild the live summary from captured live events, rebuild the paper summary by replaying the market tape through the paper simulator, and expose both summaries in the final report.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_replay.py -k market_tape`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/runtime_market_tape.jsonl tests/test_replay.py quantx/replay.py quantx/runtime/replay_store.py
git commit -m "feat: add market-tape drift replay"
```

### Task 8: Move live execution from normalized output to runtime-owned state

**Files:**
- Modify: `quantx/live_service.py`
- Modify: `quantx/live_pipeline.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_live_execution_service_updates_runtime_snapshot_from_adapter_events():
    svc = LiveExecutionService(_DummyOKXPerpExchange(), config=LiveExecutionConfig(dry_run=False), runtime_adapter=OKXPerpAdapter())
    svc.sync_symbol_rules(['BTC-USDT-SWAP'])

    result = svc.execute_orders([
        {'symbol': 'BTC-USDT-SWAP', 'side': 'BUY', 'qty': 0.01, 'price': 100000.0, 'position_side': 'long'}
    ])

    assert result['runtime_snapshot']['orders']
    assert result['runtime_snapshot']['orders'][0]['status'] in {'acked', 'working', 'filled'}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k runtime_snapshot`
Expected: FAIL because `LiveExecutionService` only returns normalized runtime events and does not keep a runtime-owned snapshot.

- [ ] **Step 3: Write minimal implementation**

```python
class LiveExecutionService:
    def __init__(...):
        ...
        self.runtime_session = RuntimeSession(mode='live', wallet_balance=0.0)

    def _apply_runtime_event(self, event):
        self.runtime_session.apply_events([event])

    def runtime_snapshot(self):
        return self.runtime_session.snapshot()
```

Use adapter-normalized order, fill, and account events to advance the same runtime session that backtest and paper use. `execute_orders` and `reconcile` should return runtime snapshots, not just raw normalized rows.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py -k runtime_snapshot`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py quantx/live_service.py quantx/live_pipeline.py
git commit -m "refactor: make live service runtime owned"
```

### Task 9: Switch bootstrap, readiness, and CLI to runtime truth and rollout stages

**Files:**
- Modify: `quantx/bootstrap.py`
- Modify: `quantx/readiness.py`
- Modify: `quantx/cli.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_bootstrap_recovery_prefers_runtime_ledger_snapshot(tmp_path):
    report = bootstrap_recover_and_reconcile(...)
    assert 'runtime_positions' in report
    assert report['ok'] is True


def test_deploy_reports_runtime_stage_and_fidelity_metadata():
    payload = main(['deploy', '--mode', 'paper', '--exchange', 'okx', '--json'])
    assert payload['runtime']['execution_path'] == 'runtime_core'
    assert payload['runtime']['stage'] == 'paper_closure'
    assert payload['runtime']['fidelity'] in {'high', 'low'}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py tests/test_quantx.py -k deploy`
Expected: FAIL because bootstrap and CLI outputs still depend on OMS-era snapshots and readiness does not expose rollout stages.

- [ ] **Step 3: Write minimal implementation**

```python
def evaluate_readiness(ctx):
    ...
    _append_check(checks, 'replay_closure_ready', ...)
    _append_check(checks, 'paper_closure_ready', ...)
    _append_check(checks, 'micro_live_ready', ...)
```

Update bootstrap to compare runtime-owned positions and order state, then expose stage metadata in `cli deploy` / `cli execute-order` so operators can see whether they are still in replay/paper closure, micro-live, or normal live.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/test_live_readiness.py tests/test_quantx.py -k deploy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_readiness.py tests/test_quantx.py quantx/bootstrap.py quantx/readiness.py quantx/cli.py
git commit -m "refactor: route operators through runtime stage gates"
```

## Chunk 4: Acceptance Coverage

### Task 10: Add parity and operational acceptance coverage for event-first runtime closure

**Files:**
- Modify: `tests/runtime/test_runtime_parity.py`
- Modify: `tests/test_replay.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `tests/test_quantx.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_event_backtest_paper_and_live_replay_share_order_sequences_for_same_intent_family():
    ...
    assert backtest_sequences == paper_sequences == live_replay_sequences


def test_drift_report_flags_non_zero_fill_price_difference_when_paper_slips():
    rep = build_daily_replay_report(...)
    assert rep['drift_metrics']['paper_vs_live']['fill_price_drift'] > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_runtime_parity.py tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py -k "parity or drift or recovery"`
Expected: FAIL until the final glue across replay, paper, and live snapshots is in place.

- [ ] **Step 3: Write minimal implementation**

```python
# Keep implementation changes small and local.
# Only patch the pieces that the new acceptance tests expose:
# - parity snapshot shape mismatches
# - missing replay summary fields
# - live recovery/runtime snapshot gaps
```

This task is intentionally the integration-tightening pass. Do not introduce new abstractions unless the failing acceptance tests force them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan tests/runtime/test_events_models.py tests/runtime/test_strategy_runtime.py tests/runtime/test_runtime_session.py tests/runtime/test_event_backtest.py tests/runtime/test_paper_exchange.py tests/runtime/test_runtime_parity.py tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/test_runtime_parity.py tests/test_replay.py tests/test_live_readiness.py tests/test_quantx.py quantx/backtest.py quantx/execution.py quantx/replay.py quantx/live_service.py quantx/bootstrap.py quantx/cli.py
git commit -m "test: add event-first runtime closure acceptance coverage"
```

## Notes For Execution

- Use @superpowers:using-git-worktrees before implementation, because this plan was written from the shared `work` branch and execution should happen in an isolated worktree.
- Use @superpowers:test-driven-development for every task.
- Use @superpowers:verification-before-completion before claiming any chunk is complete.
- Keep runtime snapshot shapes stable across backtest, paper, replay, and live so parity tests stay readable.
- Prefer adding small coordinator files (`strategy_runtime`, `session`, `paper_exchange`) instead of growing `backtest.py`, `execution.py`, or `live_service.py` further.
- If the harness does not provide the `plan-document-reviewer` subagent, do a manual chunk review before moving to the next chunk and record any assumptions in the commit message or execution notes.

Plan complete and saved to `docs/superpowers/plans/2026-03-12-event-first-runtime-closure.md`. Ready to execute?
