# OKX Perp Unattended Auto-Trade Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 unattended `OKX` perpetual live-trading backbone for `USDT`-margined `SWAP`, `cross` margin, `net mode`, multi-symbol watchlists, and total-margin-driven execution on the shared runtime-truth path.

**Architecture:** Keep one runtime-truth stack and extend it instead of creating a second live path. Add a dedicated perp-aware `OKX` client, a multi-symbol strategy/allocation/supervisor layer, and operator CLI surfaces that fail closed through bootstrap, readiness, private-stream health, and reconcile evidence.

**Tech Stack:** Python 3.10+, pytest, argparse CLI, existing `quantx` runtime/live modules, JSONL OMS/replay logs, OKX REST + private WebSocket transports.

---

## File Map

**Create**
- `quantx/exchanges/okx_perp_client.py` - dedicated `OKX` perpetual contract client for `SWAP + cross + net mode`, including raw snapshot endpoints used by runtime reconcile.
- `quantx/live_market_driver.py` - live market-driver interface plus the first `OKX` kline/candle driver used by unattended rollout.
- `quantx/live_strategy_runner.py` - multi-symbol wrapper that turns existing project strategies into live order intents without letting them talk to the exchange directly.
- `quantx/live_margin_allocator.py` - deterministic `total_margin -> symbol budget` allocator with portfolio caps.
- `quantx/live_supervisor.py` - unattended orchestration state machine owning startup, warmup, degrade, block, and recovery transitions.
- `tests/test_okx_perp_client.py` - contract tests for the new perp-aware `OKX` client.
- `tests/test_live_strategy_runner.py` - multi-symbol strategy-runner coverage.
- `tests/test_live_margin_allocator.py` - total-margin allocation and portfolio-cap coverage.
- `tests/test_live_supervisor.py` - unattended state-machine coverage.

**Modify**
- `quantx/exchanges/okx.py` - keep shared signing/request helpers only if still useful; do not leave perpetual live execution on the spot-like client.
- `quantx/exchanges/okx_perp.py` - extend adapter normalization for `net mode`, account snapshots, and raw reconcile payloads.
- `quantx/exchanges/okx_private_stream.py` - tighten private-stream subscriptions and message normalization for unattended supervisor use.
- `quantx/live_service.py` - accept the new perp client raw endpoints, runtime account snapshots, and execution-mode restrictions from the supervisor.
- `quantx/bootstrap.py` - reconcile multi-symbol `net mode` positions and expose startup evidence the supervisor can trust.
- `quantx/readiness.py` - require `OKX SWAP + cross + net mode`, total-margin inputs, supervisor health, and private-stream freshness before unattended live activation.
- `quantx/runtime/models.py` - extend `OrderIntent` metadata so strategy, allocator, and supervisor can pass budget/leverage context without out-of-band side channels.
- `quantx/runtime/strategy_runtime.py` - support a live-intent contract for signal-based legacy strategies without breaking backtest usage.
- `quantx/risk_engine.py` - add allocation and cross-margin helpers used by unattended portfolio control.
- `quantx/cli.py` - add unattended live start/status surfaces and keep `deploy --mode live` as go/no-go evidence rather than the runtime loop itself.
- `tests/test_exchange_clients.py` - keep lightweight coverage for the legacy helper client if it remains.
- `tests/runtime/test_okx_perp.py` - extend adapter parity coverage for the new perp client outputs.
- `tests/runtime/test_private_stream.py` - cover the `OKX` private-stream health signals used by the supervisor.
- `tests/runtime/test_runtime_parity.py` - prove `OKX` contract metadata stays aligned across backtest, paper, replay, and live-runtime consumers.
- `tests/test_bootstrap.py` - cover multi-symbol `net mode` takeover behavior.
- `tests/test_live_readiness.py` - cover unattended-live readiness gates and fail-closed behavior.
- `tests/test_quantx.py` - CLI acceptance coverage for unattended start/status commands.
- `docs/personal_live_go_no_go_checklist.md` - align operator go/no-go steps with the new unattended surfaces.
- `docs/restart_takeover_runbook.md` - document unattended restart, degrade, and blocked-state recovery.

## Chunk 1: OKX Perpetual Contract Foundation

### Task 1: Replace the spot-like live client with a dedicated `OKX` perpetual contract client

**Files:**
- Create: `quantx/exchanges/okx_perp_client.py`
- Modify: `quantx/exchanges/okx.py`
- Modify: `quantx/exchanges/__init__.py`
- Create: `tests/test_okx_perp_client.py`
- Modify: `tests/test_exchange_clients.py`

- [ ] **Step 1: Write the failing test**

```python
def test_okx_perp_client_places_cross_net_swap_orders_and_exposes_raw_snapshots():
    client = _OKXPerpStub()
    order = ExchangeOrder(
        client_order_id="cid-1",
        symbol="BTC-USDT-SWAP",
        side="BUY",
        qty=1.0,
        order_type="MARKET",
        price=None,
        position_side="net",
        margin_mode="cross",
        reduce_only=False,
    )

    client.place_order(order)
    open_orders = client.get_raw_open_orders("BTC-USDT-SWAP")
    positions = client.get_raw_account_positions("BTC-USDT-SWAP")
    account = client.get_raw_account_snapshot()

    assert client.calls[0][1] == "/api/v5/trade/order"
    assert client.calls[0][2]["tdMode"] == "cross"
    assert client.calls[0][2]["instId"] == "BTC-USDT-SWAP"
    assert open_orders[0]["instId"] == "BTC-USDT-SWAP"
    assert positions[0]["instId"] == "BTC-USDT-SWAP"
    assert account["details"][0]["ccy"] == "USDT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_okx_perp_client.py -k cross_net_swap_orders`
Expected: FAIL with missing `OKXPerpClient` or missing raw snapshot methods.

- [ ] **Step 3: Write minimal implementation**

Create a dedicated client with a perp-specific surface:

```python
class OKXPerpClient:
    def place_order(self, order: ExchangeOrder) -> dict[str, Any]: ...
    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]: ...
    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_raw_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_account_positions(self) -> list[ExchangePosition]: ...
    def get_raw_account_positions(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_raw_account_snapshot(self) -> dict[str, Any]: ...
    def validate_account_mode(self) -> dict[str, str]: ...
```

Implementation rules:
- Keep request-signing helpers shared only if that reduces duplication cleanly.
- Make the new client own perpetual semantics; do not keep `tdMode="cash"` or balance-as-position behavior.
- Expose raw payload methods because reconcile/runtime truth depends on adapter normalization, not lossy client-side flattening.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_okx_perp_client.py -k cross_net_swap_orders`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/exchanges/okx_perp_client.py quantx/exchanges/okx.py quantx/exchanges/__init__.py tests/test_okx_perp_client.py tests/test_exchange_clients.py
git commit -m "feat: add okx perpetual contract client"
```

### Task 2: Teach runtime reconcile and readiness to trust perp snapshots instead of spot-like fallbacks

**Files:**
- Modify: `quantx/live_service.py`
- Modify: `quantx/bootstrap.py`
- Modify: `quantx/readiness.py`
- Modify: `quantx/exchanges/okx_perp.py`
- Modify: `quantx/exchanges/okx_private_stream.py`
- Modify: `tests/runtime/test_okx_perp.py`
- Modify: `tests/runtime/test_private_stream.py`
- Modify: `tests/test_bootstrap.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_execution_service_reconcile_prefers_okx_perp_raw_snapshots_and_account_state():
    client = _OKXPerpRuntimeStub()
    service = LiveExecutionService(client, runtime_adapter=OKXPerpAdapter(), config=LiveExecutionConfig(dry_run=False))

    snapshot = service.reconcile("BTC-USDT-SWAP")

    assert snapshot["open_orders"][0]["symbol"] == "BTC-USDT-SWAP"
    assert snapshot["runtime_positions"][0]["position_side"] == "net"
    assert snapshot["runtime_snapshot"]["ledger"]["available_margin"] == 800.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/runtime/test_okx_perp.py tests/test_bootstrap.py tests/test_live_readiness.py -k "perp_raw_snapshots_and_account_state or cross_net_mode"`
Expected: FAIL because reconcile/readiness do not yet consume perp account-mode and account-snapshot evidence.

- [ ] **Step 3: Write minimal implementation**

Make three changes together:
- `LiveExecutionService.reconcile()` should ingest raw open orders, raw positions, and raw account snapshots whenever the client exposes them.
- `OKXPerpAdapter` should normalize `net mode` position/account rows into runtime events and runtime snapshot fields.
- `readiness`/`bootstrap` should reject unattended live when exchange mode is not `SWAP + cross + net` or when the private stream is stale/gapped.

Add or extend checks such as:
- `okx_perp_contract_mode`
- `okx_private_stream_fresh`
- `okx_account_snapshot_present`
- `bootstrap_net_position_match`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/runtime/test_okx_perp.py tests/runtime/test_private_stream.py tests/test_bootstrap.py tests/test_live_readiness.py -k "perp_raw_snapshots_and_account_state or cross_net_mode or private_stream"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_service.py quantx/bootstrap.py quantx/readiness.py quantx/exchanges/okx_perp.py quantx/exchanges/okx_private_stream.py tests/runtime/test_okx_perp.py tests/runtime/test_private_stream.py tests/test_bootstrap.py tests/test_live_readiness.py
git commit -m "feat: wire okx perp snapshots into runtime readiness"
```

## Chunk 2: Unattended Live Orchestration Core

### Task 3: Add a live strategy contract that can drive multiple symbols from existing strategies

**Files:**
- Create: `quantx/live_strategy_runner.py`
- Create: `tests/test_live_strategy_runner.py`
- Modify: `quantx/strategies.py`
- Modify: `quantx/runtime/models.py`
- Modify: `quantx/runtime/strategy_runtime.py`
- Modify: `tests/runtime/test_strategy_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_strategy_runner_emits_multi_symbol_net_intents_with_strategy_metadata():
    runner = LiveStrategyRunner(
        strategy_name="cta_strategy",
        watchlist=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        strategy_params={"entry_margin_pct": 0.1, "max_leverage": 3.0},
    )

    intents = runner.on_bar_batch({
        "BTC-USDT-SWAP": btc_bars,
        "ETH-USDT-SWAP": eth_bars,
    })

    assert {intent.symbol for intent in intents} <= {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
    assert all(intent.position_side == "net" for intent in intents)
    assert all(intent.metadata["strategy_name"] == "cta_strategy" for intent in intents)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_live_strategy_runner.py tests/runtime/test_strategy_runtime.py -k multi_symbol_net_intents`
Expected: FAIL because there is no multi-symbol live runner and `OrderIntent` lacks the required metadata channel.

- [ ] **Step 3: Write minimal implementation**

Introduce a backward-compatible live-intent contract:

```python
@dataclass(slots=True)
class OrderIntent:
    ...
    metadata: dict[str, Any] = field(default_factory=dict)
```

And a live runner that:
- instantiates one existing strategy per symbol or per shared configuration
- converts legacy `signal()` outputs into `net mode` live intents
- stamps `strategy_name`, `watchlist_symbol`, and sizing hints into `metadata`
- keeps backtest-only strategy usage working unchanged

If a strategy does not expose explicit live sizing hooks yet, add a default hook on `BaseStrategy` that pulls from strategy params rather than inventing ad hoc CLI-only sizing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_live_strategy_runner.py tests/runtime/test_strategy_runtime.py -k multi_symbol_net_intents`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_strategy_runner.py quantx/strategies.py quantx/runtime/models.py quantx/runtime/strategy_runtime.py tests/test_live_strategy_runner.py tests/runtime/test_strategy_runtime.py
git commit -m "feat: add multi-symbol live strategy runner"
```

### Task 4: Add deterministic total-margin allocation and execution gating

**Files:**
- Create: `quantx/live_margin_allocator.py`
- Create: `tests/test_live_margin_allocator.py`
- Modify: `quantx/risk_engine.py`
- Modify: `quantx/live_service.py`
- Modify: `quantx/runtime/models.py`
- Modify: `tests/test_live_readiness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_margin_allocator_slices_total_margin_and_enforces_symbol_caps():
    allocator = MarginAllocator(total_margin=1000.0, max_symbol_weight=0.5)

    budgets = allocator.allocate(
        watchlist=("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"),
        target_scores={"BTC-USDT-SWAP": 1.0, "ETH-USDT-SWAP": 0.5, "SOL-USDT-SWAP": 0.5},
    )

    assert round(sum(item.max_margin for item in budgets.values()), 8) <= 1000.0
    assert budgets["BTC-USDT-SWAP"].max_margin <= 500.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_live_margin_allocator.py tests/test_live_readiness.py -k total_margin`
Expected: FAIL because there is no runtime-owned margin allocator.

- [ ] **Step 3: Write minimal implementation**

Create a focused allocator surface:

```python
@dataclass(slots=True)
class SymbolBudget:
    symbol: str
    max_margin: float
    max_notional: float
    max_leverage: float

class MarginAllocator:
    def allocate(self, *, watchlist: tuple[str, ...], target_scores: dict[str, float]) -> dict[str, SymbolBudget]: ...
```

Wire it so that:
- the operator supplies `total_margin`
- the allocator derives symbol budgets deterministically
- `LiveExecutionService` rejects intents whose `metadata` exceed the active symbol budget or supervisor execution mode
- `risk_engine` exposes helpers for cross-margin budget and account-notional checks

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_live_margin_allocator.py tests/test_live_readiness.py -k total_margin`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_margin_allocator.py quantx/risk_engine.py quantx/live_service.py quantx/runtime/models.py tests/test_live_margin_allocator.py tests/test_live_readiness.py
git commit -m "feat: add total-margin live allocation gates"
```

### Task 5: Add the unattended supervisor state machine and first live market driver

**Files:**
- Create: `quantx/live_market_driver.py`
- Create: `quantx/live_supervisor.py`
- Create: `tests/test_live_supervisor.py`
- Modify: `quantx/live_service.py`
- Modify: `quantx/bootstrap.py`
- Modify: `quantx/readiness.py`
- Modify: `quantx/runtime/private_stream.py`
- Modify: `tests/runtime/test_private_stream.py`
- Modify: `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
def test_live_supervisor_transitions_from_warming_to_reduce_only_and_blocked():
    supervisor = LiveSupervisor(...)

    supervisor.mark_bootstrap_ready()
    assert supervisor.state == "warming"

    supervisor.on_stream_gap_detected()
    assert supervisor.state == "reduce_only"

    supervisor.on_position_mismatch_detected()
    assert supervisor.state == "blocked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_live_supervisor.py tests/runtime/test_private_stream.py tests/test_bootstrap.py -k "warming_to_reduce_only_and_blocked or stream_gap"`
Expected: FAIL because there is no unattended supervisor state machine yet.

- [ ] **Step 3: Write minimal implementation**

Build the first unattended orchestration layer with explicit states:
- `bootstrap_pending`
- `readiness_blocked`
- `warming`
- `live_active`
- `reduce_only`
- `read_only`
- `blocked`

Phase 1 scope:
- the market-driver interface exists and the first shipped implementation is the `OKX` kline driver
- the supervisor owns startup, warmup, reconcile scheduling, and degrade/block transitions
- private-stream stale/gap conditions trigger `reduce_only` or `blocked` instead of silent continue

Do not implement `tick`, `orderbook`, or `event-first` drivers in this task. Only leave the interface seam clean.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_live_supervisor.py tests/runtime/test_private_stream.py tests/test_bootstrap.py -k "warming_to_reduce_only_and_blocked or stream_gap"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/live_market_driver.py quantx/live_supervisor.py quantx/live_service.py quantx/bootstrap.py quantx/readiness.py quantx/runtime/private_stream.py tests/test_live_supervisor.py tests/runtime/test_private_stream.py tests/test_bootstrap.py
git commit -m "feat: add unattended live supervisor for okx perp"
```

## Chunk 3: Operator Surfaces And Cross-Mode Parity

### Task 6: Add unattended live CLI start/status surfaces and runbook coverage

**Files:**
- Modify: `quantx/cli.py`
- Modify: `tests/test_quantx.py`
- Modify: `docs/personal_live_go_no_go_checklist.md`
- Modify: `docs/restart_takeover_runbook.md`

- [ ] **Step 1: Write the failing test**

```python
def test_autotrade_start_requires_strategy_watchlist_total_margin_and_live_artifacts(tmp_path):
    payload = main([
        "autotrade-start",
        "--exchange", "okx",
        "--strategy", "cta_strategy",
        "--watchlist", '["BTC-USDT-SWAP","ETH-USDT-SWAP"]',
        "--total-margin", "1000",
        "--backtest-report", str(tmp_path / "report.json"),
        "--paper-events", str(tmp_path / "paper.jsonl"),
        "--runtime-events", str(tmp_path / "runtime.jsonl"),
        "--oms", str(tmp_path / "oms.jsonl"),
        "--json",
    ])

    assert payload["supervisor"]["state"] in {"warming", "live_active"}
    assert payload["runtime"]["execution_path"] == "runtime_core"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_quantx.py -k autotrade_start`
Expected: FAIL because the unattended CLI surfaces do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add two operator commands:
- `autotrade-start` - validates evidence, starts the unattended supervisor, and returns a structured startup payload
- `autotrade-status` - returns the current supervisor/runtime truth snapshot for operator inspection

CLI rules:
- keep `deploy --mode live` as the preflight/go-no-go path
- do not route unattended live through `PaperLiveExecutor`
- require `strategy`, `watchlist`, `total_margin`, and the existing live evidence artifacts

Update both runbooks so operator steps match the actual command names and failure states.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_quantx.py -k autotrade_start`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/cli.py tests/test_quantx.py docs/personal_live_go_no_go_checklist.md docs/restart_takeover_runbook.md
git commit -m "feat: add okx unattended live cli surfaces"
```

### Task 7: Prove `OKX` semantics stay aligned across backtest, paper, replay, and live runtime truth

**Files:**
- Modify: `tests/runtime/test_runtime_parity.py`
- Modify: `tests/runtime/test_okx_perp.py`
- Modify: `tests/test_live_readiness.py`
- Modify: `quantx/backtest.py`
- Modify: `quantx/reporting.py`
- Modify: `quantx/paper_harness.py`
- Modify: `quantx/replay.py`

- [ ] **Step 1: Write the failing test**

```python
def test_okx_runtime_metadata_matches_across_backtest_paper_replay_and_live_surfaces(tmp_path):
    backtest = build_okx_backtest_summary(...)
    paper = run_paper_harness(...)
    replay = build_daily_replay_report(...)
    live = build_live_status_payload(...)

    assert backtest["runtime_mode"] == "derivatives"
    assert backtest["venue_contract"]["exchange"] == "okx"
    assert paper["venue_contract"]["position_mode"] == "net"
    assert replay["venue_contract"]["margin_mode"] == "cross"
    assert live["venue_contract"]["product"] == "swap"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/runtime/test_runtime_parity.py tests/runtime/test_okx_perp.py tests/test_live_readiness.py -k venue_contract`
Expected: FAIL because the four surfaces do not yet share one explicit `OKX` contract metadata shape.

- [ ] **Step 3: Write minimal implementation**

Surface one compact `venue_contract`/runtime metadata shape everywhere it matters:
- `exchange`
- `product`
- `margin_mode`
- `position_mode`
- `runtime_mode`
- `fidelity`

The goal is not to make backtest pretend it is live. The goal is to ensure every mode states clearly which `OKX` contract semantics it is modeling so promotion decisions do not compare apples to oranges.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/runtime/test_runtime_parity.py tests/runtime/test_okx_perp.py tests/test_live_readiness.py -k venue_contract`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantx/backtest.py quantx/reporting.py quantx/paper_harness.py quantx/replay.py tests/runtime/test_runtime_parity.py tests/runtime/test_okx_perp.py tests/test_live_readiness.py
git commit -m "test: align okx contract metadata across live paper replay and backtest"
```

## Final Verification

- [ ] **Step 1: Run the focused unattended-live suite**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/test_okx_perp_client.py tests/test_live_strategy_runner.py tests/test_live_margin_allocator.py tests/test_live_supervisor.py tests/test_live_readiness.py tests/test_bootstrap.py tests/test_quantx.py tests/runtime/test_okx_perp.py tests/runtime/test_private_stream.py tests/runtime/test_runtime_parity.py`
Expected: PASS.

- [ ] **Step 2: Run the broader runtime regression suite**

Run: `python -m pytest -q -o addopts= --basetemp=.pytest-plan-okx-unattended tests/runtime/test_health.py tests/runtime/test_reconcile.py tests/runtime/test_live_coordinator.py tests/runtime/test_runtime_session.py tests/test_exchange_clients.py tests/test_live_readiness.py tests/test_quantx.py`
Expected: PASS.

- [ ] **Step 3: Commit final acceptance coverage and docs alignment**

```bash
git add quantx tests docs
git commit -m "feat: close okx unattended perp live backbone"
```

## Recommended Order

### Chunk 1
1. Task 1 - dedicated perp client
2. Task 2 - runtime reconcile and readiness wiring

### Chunk 2
1. Task 3 - multi-symbol live strategy runner
2. Task 4 - total-margin allocator and execution gating
3. Task 5 - unattended supervisor and first live driver

### Chunk 3
1. Task 6 - unattended CLI and runbooks
2. Task 7 - cross-mode `OKX` contract parity

## Notes

- Use @superpowers:test-driven-development for every task before touching implementation code.
- Use @superpowers:systematic-debugging before changing code in response to any unexpected test failure.
- Use @superpowers:verification-before-completion before claiming any chunk or the whole plan is done.
- Keep `deploy --mode live` as evidence-driven preflight; unattended execution should run through the supervisor, not `PaperLiveExecutor`.
- Keep Phase 1 limited to the first `OKX` live market driver needed for rollout. Leave clean driver interfaces for `tick`, `orderbook`, and `event-first`, but do not mix those into the first unattended rollout task list.
- Do not add a second live-truth path. All live, bootstrap, replay, and readiness evidence should continue to converge on the existing runtime-truth model.
