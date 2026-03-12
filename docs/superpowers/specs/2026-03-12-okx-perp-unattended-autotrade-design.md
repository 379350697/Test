# OKX Perp Unattended Auto-Trade Design

## Summary

This spec defines the first production-oriented sub-project for turning the current `OKX` live-truth runtime into an unattended automatic live-trading system.

The target is narrower than "all exchanges, all contract types, all market drivers at once."

The target for this design is:

- `OKX`
- `USDT`-margined perpetual `SWAP`
- `cross margin`
- `net mode`
- single account
- unattended automatic live trading
- multi-symbol watchlist
- project-native existing strategies

The operator input should stay intentionally small:

- strategy
- watchlist
- total margin budget
- optional strategy parameter overrides
- runtime/log/persistence paths

The operator should not need to manually enter per-order size, leverage, or symbol-by-symbol capital slices. Those belong to strategy logic plus system-level capital controls.

This spec also defines the delivery split:

- Phase 1: complete the unattended `OKX` perpetual live chain for the shared runtime path
- Phase 2: attach `tick`, `orderbook`, and `event-first` drivers to the same orchestration without rewriting the live execution core

## Problem

The codebase already has meaningful building blocks:

- a shared runtime truth path
- replay-backed recovery
- readiness and promotion gates
- `OKX` payload normalization
- paper, replay, and backtest runtime parity work

That is enough to say the project has a serious runtime foundation.

It is not enough to say the system can safely run unattended `OKX` perpetual live trading.

Current gaps:

- the real `OKX` execution client is not yet a complete perpetuals client
- `deploy --mode live` now enforces real startup gates, but true unattended strategy execution is still missing
- `execute-order --mode live` still routes through paper semantics
- multi-symbol live scheduling and strategy orchestration are not yet first-class
- total-margin-driven portfolio allocation is not yet a runtime-owned live concern
- private-stream ownership exists, but is not yet fully integrated into a live supervisor loop

The operational risk is not just "missing one endpoint." The real risk is letting `OKX` live execution, recovery, paper semantics, and replay semantics diverge just enough to look healthy while not being safe.

## Goal

Build a system where unattended `OKX` perpetual live trading operates through one explicit chain:

`startup validation -> bootstrap -> readiness -> market driver -> strategy runner -> margin allocator -> execution gate -> OKX perp client -> runtime truth -> supervisor`

Success means:

- the live execution path uses a true `OKX` perpetual contract client
- the system can run one existing strategy across multiple symbols from one total margin budget
- the system continuously maintains runtime truth from private-stream events plus reconcile snapshots
- restart, reconnect, and mismatch handling fail closed by default
- the same `OKX` contract semantics are reflected across live, paper, replay, and backtest consumers

## Scope

### In Scope

- `OKX`
- `USDT`-margined perpetual `SWAP`
- `cross margin`
- `net mode`
- single account
- unattended automatic live trading
- multi-symbol watchlist
- project-existing strategies
- total-margin input with system-owned capital slicing
- persistent replay, OMS recovery, and readiness gating
- private stream plus reconcile-driven runtime truth
- automatic downgrade paths: `reduce_only`, `read_only`, `blocked`
- live operator CLI surfaces and runbooks
- extension hooks for later `hedge mode`
- extension hooks for later `tick`, `orderbook`, and `event-first` live drivers

### Out Of Scope For This Spec

- spot
- options
- coin-margined contracts
- delivery futures
- isolated margin in the first version
- multi-account orchestration
- multiple exchanges in one unattended live runtime
- automatic silent snapshot-based ledger rewrites

## Design Approaches Considered

### Approach 1: Reliability-First Two-Phase Delivery

Phase 1:

- finish the true `OKX` perpetual unattended live chain
- support multi-symbol watchlists
- support total-margin-driven automatic execution
- keep one shared runtime truth and supervisor loop

Phase 2:

- add `tick`, `orderbook`, and `event-first` drivers as pluggable live inputs

Pros:

- reaches unattended live sooner
- limits blast radius in the real execution path
- preserves the existing live-truth runtime architecture
- avoids mixing all research-path upgrades with the first unattended live rollout

Cons:

- final cross-mode unification lands in two stages instead of one

### Approach 2: Full Unification In One Pass

Build one orchestration layer that immediately covers:

- kline live
- tick live
- orderbook live
- event-first live
- paper
- replay
- backtest

Pros:

- elegant final architecture if it succeeds

Cons:

- very high implementation and verification risk
- easiest path to breaking the current live truth and readiness semantics
- slows the first usable unattended `OKX` rollout

### Approach 3: Execution-Only Patch

Only patch:

- `OKXClient`
- `execute-order`
- minimal live command plumbing

Pros:

- fastest path to "it can place a contract order"

Cons:

- does not deliver unattended automatic trading
- leaves strategy scheduling, supervision, and capital management unfinished
- would likely create another temporary execution path to unwind later

### Recommendation

Choose **Approach 1**.

The system already has the right runtime-truth foundation. The next correct step is not another temporary patch. It is to close the unattended `OKX` perpetual live chain first, then plug additional high-fidelity drivers into that same backbone.

## Principles

### 1. One Runtime Truth

All unattended `OKX` live execution must continue to flow through one runtime truth model for:

- orders
- fills
- funding
- positions
- account state
- replay
- recovery
- reconcile

### 2. Fail Closed Before Being Clever

If runtime truth is not trustworthy, the system must downgrade or block. It must not guess, auto-heal silently, or continue because the exchange appears temporarily healthy.

### 3. Strategy Owns Micro Sizing Intent, System Owns Capital Envelope

Strategies may define:

- leverage intent
- single-entry sizing
- signal timing
- add/reduce logic

The system owns:

- total margin envelope
- symbol-level budget caps
- rollout whitelist
- max notional/order limits
- restricted execution modes

### 4. Market Drivers Must Be Replaceable

The orchestration core should not care whether the live input is:

- kline
- tick
- orderbook
- event-first

Phase 1 only ships the first driver needed for unattended rollout, but the driver boundary must be explicit from day one.

### 5. Real Exchange Contract Semantics Must Match The Target Product

The new `OKX` client must reflect actual perpetual contract behavior, not a spot-like approximation.

## Architecture

### 1. `OKXPerpClient`

This becomes the true exchange-facing contract client for unattended `OKX` perpetual live trading.

Responsibilities:

- place contract orders with perpetual-specific fields
- cancel orders
- fetch open orders
- fetch contract positions
- fetch account and margin state
- validate account mode expectations:
  - `SWAP`
  - `cross`
  - `net mode`
- expose raw payloads needed for adapter normalization
- own no strategy logic

The current generic `OKXClient` should not remain the final unattended perpetual execution client. It may survive as a lower-level helper or spot-oriented base, but unattended perpetual live must use a dedicated perp-aware client.

### 2. `LiveMarketDriver`

Responsibilities:

- ingest live market data
- produce strategy-consumable inputs
- isolate transport/source details from strategy and execution layers

Phase 1 ships:

- `OKX` kline/live-candle driver

Phase 2 adds:

- tick driver
- orderbook driver
- event-first driver

### 3. `StrategyRunner`

Responsibilities:

- instantiate existing project strategies
- maintain per-symbol strategy state
- evaluate signals from live driver input
- convert strategy outputs into target exposure or order intent requests

It does not talk to the exchange directly.

### 4. `MarginAllocator`

Responsibilities:

- translate `total_margin` into a symbol-level budget envelope
- enforce portfolio-level limits before execution
- leave strategy-level entry/leverage mechanics intact
- make multi-symbol unattended execution deterministic and auditable

Recommended first-version rule:

- the operator supplies `total_margin`
- the allocator derives symbol budgets from a configurable allocation policy
- strategies consume those budgets when sizing entries

### 5. `ExecutionGate`

Responsibilities:

- convert strategy intents into executable orders
- enforce whitelist and per-cycle constraints
- enforce restricted execution modes:
  - `live_active`
  - `reduce_only`
  - `read_only`
  - `blocked`
- route accepted orders into `LiveExecutionService`

### 6. `Supervisor`

Responsibilities:

- run the unattended state machine
- own startup and restart sequencing
- supervise market driver and private stream health
- schedule periodic reconcile checks
- downgrade or block execution on health failures
- surface operator-readable state and evidence

This is the true unattended owner, not the strategy, not the driver, and not the raw exchange client.

## Startup Flow

The first live startup sequence should be:

1. Load config, strategy, watchlist, and `total_margin`
2. Load and validate `OKX` credentials
3. Validate exchange/account assumptions:
   - perpetual `SWAP`
   - `cross`
   - `net mode`
4. Recover replay and OMS state
5. Run `bootstrap_recover_and_reconcile(...)`
6. Build promotion gates from:
   - backtest evidence
   - paper evidence
   - live runtime/bootstrap evidence
7. Run `assert_ready(...)`
8. Start private stream and market driver
9. Enter `warming`
10. Promote to `live_active` only after health and reconcile checks are green

## Runtime Data Flow

### Live Input Flow

`OKX market data -> LiveMarketDriver -> StrategyRunner`

### Decision Flow

`StrategyRunner -> MarginAllocator -> ExecutionGate -> LiveExecutionService -> OKXPerpClient`

### Truth Flow

`OKX private stream + reconcile snapshots -> runtime adapter -> runtime coordinator -> runtime truth`

### Supervision Flow

`runtime truth + stream health + reconcile results + execution failures -> Supervisor -> execution mode transitions`

## State Machine

### States

- `bootstrap_pending`
- `readiness_blocked`
- `warming`
- `live_active`
- `reduce_only`
- `read_only`
- `blocked`

### State Meanings

#### `bootstrap_pending`

- recovery and reconcile are still in progress
- no new risk allowed

#### `readiness_blocked`

- bootstrap completed
- readiness or promotion gates failed
- the runtime may observe state, but unattended trading stays off

#### `warming`

- streams and drivers are up
- runtime truth is still establishing a trusted live operating baseline
- no new risk until health becomes stable

#### `live_active`

- unattended strategy execution is enabled
- new and reducing orders are allowed within portfolio and risk limits

#### `reduce_only`

- only risk reduction is allowed
- no new exposure may be created

#### `read_only`

- the system continues observing and computing
- no exchange orders are sent

#### `blocked`

- the system has failed closed
- unattended trading is stopped pending recovery or operator review

### Transitions

- `bootstrap_pending -> readiness_blocked`
  when startup gates fail
- `bootstrap_pending -> warming`
  when bootstrap and readiness pass
- `warming -> live_active`
  when stream, reconcile, and runtime truth are healthy
- `live_active -> reduce_only`
  when recoverable risk appears
- `live_active -> blocked`
  when truth or safety is no longer trustworthy
- `reduce_only -> live_active`
  when health is restored over a defined stability window
- `any -> read_only`
  when unattended trading is intentionally soft-disabled
- `any -> blocked`
  on hard fail-closed conditions

### Hard Fail-Closed Conditions

- bootstrap recovery is untrusted
- position mismatch remains after reconcile
- runtime truth application errors occur
- private stream freshness is lost beyond the tolerated window
- repeated execution failures cross threshold
- runtime replay persistence is unavailable

## Phase 1 Delivery

Phase 1 must deliver the unattended `OKX` perpetual live backbone:

- true perp-aware `OKX` client
- multi-symbol live strategy runner
- total-margin-driven allocator
- unattended supervisor loop
- private stream ownership
- reconcile-driven downgrades
- bootstrap + readiness + runtime truth gating
- operator-visible CLI and runbook surfaces

Phase 1 may use the simplest market driver that safely supports unattended rollout, but it must define a stable live-driver interface for the next phase.

## Phase 2 Delivery

Phase 2 adds additional high-fidelity drivers to the same orchestration:

- tick
- orderbook
- event-first

Phase 2 should not create a second live execution stack. It should only add new live input drivers and strategy evaluation adapters on top of the existing unattended supervisor backbone.

## Testing Strategy

The acceptance bar should be "safe unattended live candidate," not merely "callable API."

### 1. `OKXPerpClient` Contract Tests

Must verify:

- perpetual order payloads
- cancel payloads
- open-order queries
- position queries
- account/margin queries
- account-mode validation for `cross + net mode`

### 2. Strategy And Allocation Integration Tests

Must verify:

- one strategy across multiple symbols
- total-margin slicing
- strategy-driven sizing and leverage usage
- per-cycle and per-symbol risk caps

### 3. Supervisor State Tests

Must verify:

- promotion from startup to active live
- downgrade to `reduce_only`
- downgrade to `read_only`
- fail-closed transition to `blocked`
- controlled recovery to `live_active`

### 4. Recovery And Reconcile Tests

Must verify:

- warm restart
- cold restart
- stream gap
- open-order drift
- position mismatch
- funding continuity

### 5. CLI Acceptance Tests

Must verify:

- unattended startup command
- operator status command
- blocked-state inspection
- replay/report evidence surfaces

### 6. Cross-Mode Parity Tests

Must verify that `OKX` contract semantics used in:

- live
- paper
- replay
- backtest

share the same key assumptions around order state, fills, funding, positions, and margin-facing runtime metadata.

## Risks

### 1. Perp Client Drift

If the `OKX` execution client remains partially spot-like, the entire unattended live chain will look correct while being semantically wrong.

### 2. Strategy Contract Ambiguity

If existing strategies are not explicit about sizing/leverage outputs, total-margin allocation will become inconsistent or non-deterministic in live trading.

### 3. Driver Explosion Too Early

If `tick`, `orderbook`, and `event-first` are forced into the first unattended delivery, the live backbone may never stabilize.

### 4. Silent Recovery Logic

If restart or reconcile tries to auto-heal state invisibly, unattended safety will be weaker than the current explicit fail-closed model.

## Open Design Constraints To Preserve

- keep the first version on `cross + net mode`
- leave clean extension points for later `hedge mode`
- avoid creating a second independent live truth path
- avoid patching unattended live through `PaperLiveExecutor`
- avoid coupling the execution backbone to one market-data driver type

## Recommendation

Proceed with:

1. a dedicated `OKX` perpetual unattended live backbone
2. multi-symbol watchlist support
3. total-margin-driven portfolio control
4. shared runtime-truth supervision
5. explicit Phase 2 driver expansion for `tick`, `orderbook`, and `event-first`

This gives the project the fastest path to a real unattended `OKX` perpetual system without sacrificing the architectural clarity needed for the higher-fidelity modes that follow.
