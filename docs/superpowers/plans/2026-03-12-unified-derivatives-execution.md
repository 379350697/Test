# Unified Derivatives Execution Core Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared event-driven derivatives execution core so `backtest`, `paper`, and `live` use the same order, fill, ledger, and risk semantics for `USDT perpetual + hedge mode + cross margin`, with OKX first and Binance second.

**Architecture:** Introduce a focused `quantx/runtime/` package for normalized events, order state, ledger state, risk checks, and simulated fills. Existing entry points in `quantx/backtest.py`, `quantx/execution.py`, `quantx/live_service.py`, and `quantx/live_pipeline.py` become thin wrappers over that core, while OKX and Binance adapters map exchange-specific payloads into the shared event model.

**Tech Stack:** Python 3.10+, dataclasses, pytest, existing QuantX CLI, JSONL replay logs, OKX/Binance WebSocket and REST adapters.

---

## File Map

**Create**
- `quantx/runtime/__init__.py` - package exports for the shared runtime.
- `quantx/runtime/events.py` - normalized event dataclasses and event enums.
- `quantx/runtime/models.py` - order intent, order state, position leg, account ledger, runtime config.
- `quantx/runtime/order_engine.py` - order state machine and idempotent transition logic.
- `quantx/runtime/ledger_engine.py` - hedge-mode leg accounting and cross-margin account math.
- `quantx/runtime/runtime_risk.py` - runtime-facing risk checks and reduce-only validation.
- `quantx/runtime/fill_engine.py` - paper/backtest fill simulation for queue, latency, and partial fill behavior.
- `quantx/runtime/replay_store.py` - append-only event store and replay loader for normalized runtime events.
- `quantx/exchanges/okx_perp.py` - OKX perpetual adapter that maps public/private exchange payloads to normalized events.
- `quantx/exchanges/binance_perp.py` - Binance perpetual adapter using the same normalized event contract.
- `tests/runtime/test_events_models.py` - event/model invariants.
- `tests/runtime/test_order_engine.py` - order state transition tests.
- `tests/runtime/test_ledger_engine.py` - hedge-mode and cross-margin ledger tests.
- `tests/runtime/test_fill_engine.py` - paper/backtest fill simulation tests.
- `tests/runtime/test_okx_perp.py` - OKX adapter normalization tests.
- `tests/runtime/test_binance_perp.py` - Binance adapter normalization tests.
- `tests/runtime/test_runtime_parity.py` - parity tests across backtest, paper, and live-replay paths.

**Modify**
- `quantx/backtest.py` - replace direct position/cash mutation with runtime core calls.
- `quantx/execution.py` - replace `PaperLiveExecutor` semantics with runtime-backed paper execution.
- `quantx/live_service.py` - route order submission, fills, and reconciliation through the runtime core.
- `quantx/live_pipeline.py` - construct rebalance/live cycles from normalized events and runtime services.
- `quantx/bootstrap.py` - recover and reconcile runtime ledger state instead of pair-vs-asset shortcuts.
- `quantx/replay.py` - consume normalized runtime events for day replay and drift analysis.
- `quantx/cli.py` - add runtime-backed entry points and keep compatibility wrappers thin.
- `quantx/exchanges/base.py` - extend exchange base types for derivatives-specific fields if needed.
- `tests/test_quantx.py` - update broad integration tests to use the new runtime-backed flows.
- `tests/test_live_readiness.py` - update readiness/recovery tests around runtime state and adapters.
- `tests/test_replay.py` - update daily replay tests to use normalized runtime events.

## Chunk 1: Shared Runtime Primitives

### Task 1: Create the runtime package and normalized event schema

**Files:**
- Create: `quantx/runtime/__init__.py`
- Create: `quantx/runtime/events.py`
- Test: `tests/runtime/test_events_models.py`

- [ ] **Step 1: Write failing tests for normalized event families and required fields**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_events_models.py -k event` and confirm the tests fail because the runtime package does not exist yet**
- [ ] **Step 3: Implement normalized event dataclasses for `market_event`, `order_event`, `fill_event`, and `account_event`**
- [ ] **Step 4: Re-run the same test command and confirm the event tests pass**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime event schema"`**

### Task 2: Create shared order, position, and account models

**Files:**
- Create: `quantx/runtime/models.py`
- Test: `tests/runtime/test_events_models.py`

- [ ] **Step 1: Write failing tests for `OrderIntent`, hedge-mode position-leg keys, and cross-margin account fields**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_events_models.py -k model` and confirm the failures mention missing runtime models**
- [ ] **Step 3: Implement dataclasses for order intent, tracked order, position leg, and account ledger with explicit `position_side` and `reduce_only` fields**
- [ ] **Step 4: Re-run the same test command and confirm the model tests pass**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime order and ledger models"`**

## Chunk 2: Order State Machine and Ledger Core

### Task 3: Implement the order engine state machine

**Files:**
- Create: `quantx/runtime/order_engine.py`
- Test: `tests/runtime/test_order_engine.py`

- [ ] **Step 1: Write failing tests covering `IntentCreated -> RiskAccepted -> Submitted -> Acked/Working -> PartiallyFilled -> Filled` and terminal branches `Rejected`, `Canceled`, `Expired`**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_order_engine.py` and verify the state machine tests fail**
- [ ] **Step 3: Implement a small single-writer order engine that only advances state from normalized events and rejects invalid transitions**
- [ ] **Step 4: Re-run the same test command and verify it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime order state machine"`**

### Task 4: Implement hedge-mode cross-margin ledger updates

**Files:**
- Create: `quantx/runtime/ledger_engine.py`
- Test: `tests/runtime/test_ledger_engine.py`

- [ ] **Step 1: Write failing tests for separate `LONG`/`SHORT` legs on the same symbol, realized PnL on fills, unrealized PnL on mark-price updates, and account-level available/used margin updates**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_ledger_engine.py` and verify the ledger tests fail**
- [ ] **Step 3: Implement the ledger engine so fills update per-leg state while mark-price and funding events update account-level state without bypassing the event model**
- [ ] **Step 4: Re-run the same test command and verify it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime hedge-mode ledger engine"`**

### Task 5: Add runtime risk checks for cross-margin derivatives execution

**Files:**
- Create: `quantx/runtime/runtime_risk.py`
- Modify: `quantx/risk_engine.py`
- Test: `tests/runtime/test_ledger_engine.py`
- Test: `tests/test_live_readiness.py`

- [ ] **Step 1: Write failing tests for `reduce_only` validation, hedge-mode position-side validation, and account-level cross-margin health checks**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_ledger_engine.py tests/test_live_readiness.py -k risk` and confirm the risk behavior is missing**
- [ ] **Step 3: Implement runtime-level risk checks without reusing spot/net-position shortcuts**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime derivatives risk checks"`**

## Chunk 3: High-Fidelity Paper and Backtest

### Task 6: Build the paper/backtest fill engine

**Files:**
- Create: `quantx/runtime/fill_engine.py`
- Test: `tests/runtime/test_fill_engine.py`

- [ ] **Step 1: Write failing tests for queue delay, partial fills, cancel delay, and simple slippage behavior on normalized market events**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_fill_engine.py` and verify the fill tests fail**
- [ ] **Step 3: Implement a deterministic fill engine that emits synthetic `order_event`, `fill_event`, and `account_event` records for `paper` and `backtest`**
- [ ] **Step 4: Re-run the same test command and verify it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime fill engine"`**

### Task 7: Refactor backtest execution onto the runtime core

**Files:**
- Modify: `quantx/backtest.py`
- Modify: `quantx/models.py`
- Test: `tests/runtime/test_runtime_parity.py`
- Test: `tests/test_quantx.py`

- [ ] **Step 1: Write failing parity tests proving a simple strategy run now depends on the runtime order and ledger engines instead of direct `cash/pos` mutation**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_runtime_parity.py tests/test_quantx.py -k backtest` and confirm the parity tests fail**
- [ ] **Step 3: Replace the direct trading loop in `quantx/backtest.py` with runtime event emission, order handling, and ledger updates while keeping report outputs stable**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "refactor: move backtest execution onto runtime core"`**

### Task 8: Replace `PaperLiveExecutor` semantics with the runtime-backed paper path

**Files:**
- Modify: `quantx/execution.py`
- Modify: `quantx/cli.py`
- Test: `tests/runtime/test_runtime_parity.py`
- Test: `tests/test_quantx.py`

- [ ] **Step 1: Write failing tests that assert paper mode no longer clamps naked sells to zero and instead follows the same hedge-mode and risk semantics as the runtime core**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_runtime_parity.py tests/test_quantx.py -k paper` and confirm the legacy paper semantics are still present**
- [ ] **Step 3: Rebuild `PaperLiveExecutor` as a thin compatibility wrapper over the runtime order, fill, and ledger engines**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "refactor: back paper execution with runtime core"`**

## Chunk 4: Replay, Recovery, and OKX Live

### Task 9: Add normalized runtime replay storage

**Files:**
- Create: `quantx/runtime/replay_store.py`
- Modify: `quantx/replay.py`
- Test: `tests/test_replay.py`

- [ ] **Step 1: Write failing tests that require replay files to consume normalized runtime events instead of ad-hoc mixed log formats**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/test_replay.py` and confirm replay expectations fail**
- [ ] **Step 3: Implement append-only normalized event persistence and adapt `quantx/replay.py` to summarize from that store**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add runtime replay store"`**

### Task 10: Implement the OKX perpetual adapter and wire live execution to it

**Files:**
- Create: `quantx/exchanges/okx_perp.py`
- Modify: `quantx/exchanges/base.py`
- Modify: `quantx/live_service.py`
- Modify: `quantx/live_pipeline.py`
- Test: `tests/runtime/test_okx_perp.py`
- Test: `tests/test_live_readiness.py`

- [ ] **Step 1: Write failing tests for normalized OKX order, fill, position, and account events, including hedge-mode and cross-margin fields**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_okx_perp.py tests/test_live_readiness.py -k okx` and confirm the adapter tests fail**
- [ ] **Step 3: Implement the OKX adapter and refactor `live_service`/`live_pipeline` so live trading advances through normalized runtime events instead of direct ad-hoc dict handling**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add OKX perpetual runtime adapter"`**

### Task 11: Fix recovery and takeover around runtime ledger truth

**Files:**
- Modify: `quantx/bootstrap.py`
- Modify: `quantx/live_service.py`
- Test: `tests/test_live_readiness.py`

- [ ] **Step 1: Write failing tests that prove restart recovery uses runtime ledger state and exchange-normalized positions instead of comparing pair symbols to raw asset balances**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/test_live_readiness.py -k recover` and confirm the current recovery mismatch remains**
- [ ] **Step 3: Refactor recovery to rebuild runtime state from replay storage and exchange truth, then produce a deterministic takeover report**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "fix: align recovery with runtime ledger truth"`**

## Chunk 5: Binance Adapter, Parity, and Rollout Gates

### Task 12: Implement the Binance perpetual adapter on the same normalized contract

**Files:**
- Create: `quantx/exchanges/binance_perp.py`
- Modify: `quantx/exchanges/base.py`
- Test: `tests/runtime/test_binance_perp.py`

- [ ] **Step 1: Write failing tests for Binance order, fill, account, and depth payload normalization into the same runtime events used by OKX**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_binance_perp.py` and verify the Binance adapter tests fail**
- [ ] **Step 3: Implement the Binance adapter without changing the runtime event schema or strategy interfaces**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "feat: add Binance perpetual runtime adapter"`**

### Task 13: Add parity and drift acceptance tests across modes

**Files:**
- Create: `tests/runtime/test_runtime_parity.py`
- Modify: `tests/test_quantx.py`
- Modify: `tests/test_replay.py`

- [ ] **Step 1: Write failing tests for shared order-state sequences, shared ledger outcomes, and bounded paper-vs-live replay drift on the same event tape**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/runtime/test_runtime_parity.py tests/test_quantx.py tests/test_replay.py -k parity` and confirm the parity tests fail before the final glue is in place**
- [ ] **Step 3: Finish the remaining integration glue so all three runtime modes report through the same core and drift metrics become available**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "test: add runtime parity and drift acceptance coverage"`**

### Task 14: Update CLI entry points and readiness checks for the runtime architecture

**Files:**
- Modify: `quantx/cli.py`
- Modify: `quantx/readiness.py`
- Test: `tests/test_live_readiness.py`
- Test: `tests/test_quantx.py`

- [ ] **Step 1: Write failing tests that require `deploy` and `execute-order` to route through the runtime-backed execution path rather than legacy local-only semantics**
- [ ] **Step 2: Run `python -m pytest -q -o addopts= --basetemp=.pytest-tmp tests/test_live_readiness.py tests/test_quantx.py -k deploy` and confirm the CLI/readiness tests fail**
- [ ] **Step 3: Update CLI and readiness code to reflect the new runtime architecture and rollout gates for OKX first, then Binance**
- [ ] **Step 4: Re-run the same test command and confirm it passes**
- [ ] **Step 5: Commit with `git commit -m "refactor: route CLI and readiness through runtime core"`**

## Notes for Execution

- Use @superpowers:test-driven-development for each task.
- Use @superpowers:verification-before-completion before claiming any chunk is done.
- Keep runtime-core commits small and isolated so parity regressions are easy to bisect.
- Do not delete the current legacy modules until the runtime-backed replacements have passing parity coverage.
- If a task reveals missing exchange-specific fields, update the normalized schema first instead of adding exchange-specific branches downstream.

Plan complete and saved to `docs/superpowers/plans/2026-03-12-unified-derivatives-execution.md`. Ready to execute?
