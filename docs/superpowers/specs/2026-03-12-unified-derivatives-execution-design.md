# Unified Derivatives Execution Design

## Summary

This spec defines the next-generation QuantX runtime for derivatives trading.

The design target is:

- highest-fidelity paper trading and backtesting
- shared execution semantics across `backtest`, `paper`, and `live`
- support for both low-frequency and high-frequency strategies
- account model aligned to real usage:
  `USDT-margined perpetuals + hedge mode + cross margin`
- OKX Swap as the primary truth source for live semantics
- Binance Futures as the second exchange adapter on the same core

The core principle is simple:

`strategies may differ, execution semantics may not`

## Goals

- Make paper trading and backtesting converge toward live trading behavior.
- Ensure low-frequency and high-frequency strategies can run on the same core.
- Replace the current split behavior across `quantx/backtest.py`, `quantx/execution.py`, and `quantx/live_service.py`.
- Build one event-driven order, fill, ledger, and risk model that all runtime modes use.
- Keep hot-path performance high enough for latency-sensitive derivatives strategies.
- Make every material trading action replayable, auditable, and calibratable.

## Non-Goals

- Spot trading support in the first version.
- Multi-account orchestration in the first version.
- Full order-type coverage in the first version.
- Exchange-specific behavior leaking into strategy code.
- Exact exchange-matching parity in all microstructure edge cases.

## Design Decisions

### Strategy Model

The system supports two strategy interfaces:

- `bar_strategy`
- `event_strategy`

Both interfaces produce the same output type: `OrderIntent`.

Strategies are not allowed to:

- mutate positions
- mutate account balances
- mark orders as filled
- bypass risk checks
- call exchange clients directly

This preserves one execution truth across all modes.

### Account Model

The unified account model is:

- `USDT-margined perpetuals`
- `hedge mode`
- `cross margin`

This means:

- a symbol can hold both `LONG` and `SHORT` positions at the same time
- positions are keyed by `(symbol, position_side)`
- risk is evaluated at the account level, not isolated per leg
- available margin, maintenance margin, unrealized PnL, fees, and funding are account-scoped state

### Runtime Modes

The system exposes three runtime modes:

- `backtest`
- `paper`
- `live`

The runtime mode changes only:

- market data source
- fill source
- account event source

The runtime mode does not change:

- order state machine
- ledger semantics
- risk checks
- position semantics
- fee and funding accounting model
- audit event model

## Architecture

### Core Modules

The runtime is split into eight modules.

#### 1. `event_bus`

The shared event transport inside the engine.

Accepted event families:

- `market_event`
- `order_event`
- `fill_event`
- `account_event`

This module is the only way information moves between subsystems on the hot path.

#### 2. `strategy_runtime`

Hosts both:

- `bar_strategy`
- `event_strategy`

Responsibilities:

- feed strategies the correct event or bar context
- maintain strategy-local state only
- emit `OrderIntent`

#### 3. `risk_engine`

Applies:

- pre-order risk checks
- post-fill account risk updates
- cross-margin health calculations
- hedge-mode validations
- `reduce_only` validations

#### 4. `order_engine`

Turns `OrderIntent` into tracked order lifecycle objects.

Responsibilities:

- assign `client_order_id`
- manage cancel/amend/submit paths
- own order state transitions
- never mutate balances or positions directly

#### 5. `fill_engine`

Used only in `backtest` and `paper`.

Responsibilities:

- generate simulated acknowledgements
- model queue delay
- model partial fills
- model cancel delay
- model slippage and rejection

#### 6. `ledger_engine`

The single accounting truth.

Responsibilities:

- maintain per-leg position state
- maintain account-level equity and margin
- book fees
- book realized and unrealized PnL
- book funding payments

#### 7. `exchange_adapters`

Adapters only, not business logic.

Initial order:

- `OKX Swap`
- `Binance Futures`

Responsibilities:

- protocol mapping
- symbol and instrument metadata normalization
- public market stream ingestion
- private order/fill/account stream ingestion

#### 8. `replay_audit`

Persistent append-only event capture for:

- historical replay
- incident review
- live-vs-paper drift analysis
- parameter calibration

## Order Lifecycle

The unified order state machine is:

`IntentCreated -> RiskAccepted -> Submitted -> Acked/Working -> PartiallyFilled -> Filled`

Failure and terminal branches:

- `Rejected`
- `Canceled`
- `Expired`

### Rules

- Only events can advance order state.
- Exchange adapters may emit `ack`, `reject`, `fill`, `cancel_ack`, and `expire`.
- The fill engine may emit the same event types in `paper` and `backtest`.
- Strategy code never moves an order to a later state.

### Required `OrderIntent` Fields

- `symbol`
- `side`
- `position_side`
- `qty`
- `price`
- `order_type`
- `time_in_force`
- `reduce_only`

Additional optional fields may be added later, but these fields define the first stable core.

## Ledger Model

### Position Ledger

Key:

- `(symbol, position_side)`

Tracked state per leg:

- quantity
- average entry price
- realized PnL
- unrealized PnL
- fee totals
- funding totals

### Account Ledger

Tracked state:

- total equity
- wallet balance
- available margin
- used margin
- maintenance margin
- account risk ratio

### Update Rules

- `fill_event` updates position quantity, average price, fees, and realized PnL
- `market_event` with mark price updates unrealized PnL
- `account_event` with funding updates funding ledger entries
- cross-margin health is recalculated after every material ledger mutation

## Market Data and Fill Semantics

### Live Mode

`live` mode uses real exchange public and private streams.

For OKX Swap, the truth hierarchy is:

- private order and fill streams for order state
- private account and position streams for account truth
- public orderbook, trades, mark price, and funding streams for market context

Local code must never assume an order is filled until a matching fill or final order-state event arrives from the private stream.

### Paper Mode

`paper` mode uses live public market streams and a local fill engine.

The fill engine produces synthetic:

- `order_event`
- `fill_event`
- `account_event`

Paper mode therefore imitates exchange behavior instead of directly mutating positions.

### Backtest Mode

Preferred backtest input:

- historical event stream
- orderbook updates
- trades
- mark price
- funding events

Fallback mode:

- bar-only backtest for `bar_strategy`

Fallback mode is explicitly lower fidelity and should be labeled as such in reports.

## Performance Constraints

Performance is a primary design constraint.

### Hot Path Requirements

The hot path should be limited to:

- receive event
- strategy decision
- risk check
- order state transition
- ledger update

The hot path must not block on:

- markdown reporting
- JSON summary generation
- replay indexing
- heavy aggregation
- long-running persistence batches

### Performance Principles

- single-writer state updates for deterministic ledgers
- incremental indicators for event strategies
- minimal object churn on high-frequency paths
- async or deferred processing for cold-path reporting and analytics
- shared normalized event structs across runtime modes

### Optimization Strategy

Recommended implementation path:

- Python orchestration
- hotspot modules optimized separately when needed

Do not over-commit to exchange-specific micro-optimizations before the unified core is stable.

## Verification and Drift Calibration

The system is considered successful only if drift can be measured and explained.

### Primary Fidelity Metrics

- order state transition match rate
- fill price drift
- partial fill ratio drift
- cancel success drift
- equity and margin drift
- funding booking drift

### Rollout Stages

1. `historical replay`
2. `paper realtime`
3. `micro-live`
4. `normal live`

### Daily Feedback Loop

For each live trading day:

- capture live market, order, fill, and account events
- replay the same day through the paper/backtest core
- measure divergence
- recalibrate latency, queue, slippage, and cancel-delay parameters

## Milestones

### Milestone 1: Unified Core

Build:

- `event_bus`
- `order_engine`
- `ledger_engine`
- `risk_engine`

Outcome:

- current fragmented execution semantics are removed

### Milestone 2: High-Fidelity Paper and Backtest

Build:

- `fill_engine`
- bar-mode compatibility layer
- event-driven replay path

Outcome:

- `paper` and `backtest` share the same execution semantics

### Milestone 3: OKX Live

Build:

- OKX Swap adapter
- private stream reconciliation
- live event persistence

Outcome:

- first full end-to-end derivatives trading loop

### Milestone 4: Binance Live

Build:

- Binance Futures adapter
- mapping to the same internal event model

Outcome:

- second exchange on the same core without strategy-layer changes

## Risks

- microstructure modeling can become overfit if calibrated too tightly to short windows
- hedge-mode cross-margin accounting is materially more complex than spot or net-position models
- live exchange private streams may deliver events out of order or with temporary disconnect gaps
- high-frequency event volume may force hotspot optimization earlier than planned

## Acceptance Criteria

This design is accepted when the implementation can demonstrate:

- one shared order state machine across `backtest`, `paper`, and `live`
- one shared ledger model across `backtest`, `paper`, and `live`
- explicit support for `USDT perpetual + hedge mode + cross margin`
- OKX live support as the reference exchange
- Binance support as a second adapter on the same core
- measured, explainable paper/live and backtest/live drift
- no direct position mutation from strategy code

## Open Items For The Implementation Plan

- exact internal event schema definitions
- snapshot and replay storage format
- symbol normalization contract between OKX and Binance
- first-version funding-rate and liquidation approximation model
- first-version queue-position model for simulated fills
- backpressure strategy for very high event throughput
