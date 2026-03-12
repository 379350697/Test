# OKX Live Truth Design

## Summary

This spec defines the first sub-project required to turn the current runtime into a real live-trading closure for a personal derivatives system.

The immediate target is not "all realism everywhere at once." The target is narrower and more important:

- make `OKX` live trading use one runtime truth
- make live order and ledger state auditable from normalized events
- make restart, reconcile, and replay operate from that same truth
- keep `paper` and `backtest` as downstream consumers of the same semantics

This spec intentionally treats `OKX live truth` as the first milestone under the broader event-first runtime program.

## Problem

The codebase now has a shared runtime core, event backtest, a paper simulator, and replay drift reporting. That is meaningful progress, but it is not yet a fully real live closure.

Current strengths:

- `OrderIntent`, `RuntimeSession`, `PaperExchangeSimulator`, and replay drift paths exist
- `event_backtest`, `paper`, and `live` expose comparable runtime snapshots
- `OKX` payloads can already be normalized into runtime events

Current live gap:

- live execution still relies on exchange request/response plus partial normalized events
- runtime ledger truth is not yet fully driven by the live private stream
- exchange position and account snapshots are not yet cleanly separated into "truth-driving" vs "reconciliation-only" roles
- restart/recovery is not yet defined around a single event-sourced live truth model

For a personal system, the real risk is not "missing one more feature." The real risk is letting live, paper, replay, and recovery drift into separate meanings.

## Goal

Build a live execution path where:

- `OrderIntent -> RuntimeSession -> OKX private events -> ledger/replay/reconcile`

is the only truth-bearing chain.

Success means:

- live order states are driven by normalized `order_event` and `fill_event`
- live ledger state is driven by normalized `fill_event` plus booking events such as funding
- exchange snapshots are used for reconciliation, alerting, and takeover checks, not silent auto-healing
- replay can reconstruct the same live state lineage from persisted runtime events

## Scope

### In Scope

- `OKX`
- `USDT` perpetuals
- `cross margin`
- single account
- single active runtime owner
- normalized live event classes for:
  - `order`
  - `fill`
  - `position`
  - `account`
  - `funding`
- replay persistence for normalized live events
- restart recovery from replay plus exchange reconciliation
- stage gates for `paper_closure -> micro_live -> normal_live`

### Out Of Scope For This First Live Truth Spec

- Binance live truth
- spot/options
- multi-account orchestration
- automatic exchange-snapshot-based ledger rewrites
- full exchange microstructure emulation for paper/backtest
- orderbook-level fill modeling in live truth itself

## Core Principles

### 1. Runtime Ledger Is The Only Internal Truth

Internal order state and internal ledger state must be owned by the runtime, not by ad hoc exchange mirrors or OMS leftovers.

### 2. Only Some Events May Change The Ledger

The live ledger may only be changed by normalized events with booking meaning:

- `fill_event`
- `funding` booking event
- future explicit fee/accounting events, if introduced

`position` or `account` snapshots must not directly mutate the live ledger in the first version.

### 3. Exchange Snapshots Are Reconciliation Inputs

Exchange `open_orders`, `positions`, and `account` snapshots exist to:

- compare
- detect drift
- block unsafe continuation
- support operator takeover decisions

They do not exist to silently rewrite runtime history.

### 4. No Auto-Heal In The First Version

If runtime and exchange disagree, the system should:

- report the mismatch
- enter or remain in a restricted stage if needed
- optionally degrade to read-only

It should not fabricate fills or directly edit ledger state to make the mismatch disappear.

## Architecture

### Components

#### `live_gateway`

Responsibilities:

- submit exchange orders
- own `client_order_id`
- own `OKX` private WebSocket subscription lifecycle
- reconnect and resume subscriptions

#### `okx_runtime_adapter`

Responsibilities:

- normalize raw `OKX` order messages into `OrderEvent`
- normalize raw `OKX` fills into `FillEvent`
- normalize raw `OKX` positions into reconciliation-only `AccountEvent`
- normalize raw `OKX` account snapshots into reconciliation-only `AccountEvent`
- normalize funding into booking events

#### `live_runtime_coordinator`

Responsibilities:

- accept `OrderIntent`
- call `RuntimeSession.submit_intents(...)`
- dispatch exchange orders
- feed normalized private-stream events into `RuntimeSession.apply_events(...)`
- write normalized events to replay storage
- own reconcile and recovery orchestration

#### `RuntimeSession`

Responsibilities:

- maintain the order state machine
- apply risk checks before submission
- apply booking events to the ledger
- expose runtime snapshots used by CLI, readiness, replay, and recovery

#### `replay_store`

Responsibilities:

- persist normalized live events in sequence
- preserve enough information to reconstruct live runtime state
- provide inputs for replay, drift, and recovery

#### `reconcile_service`

Responsibilities:

- compare runtime order/position/account views with exchange snapshots
- produce mismatch reports
- gate stage promotion
- trigger degraded mode when mismatch severity exceeds thresholds

## Event Model

### Truth-Bearing Events

#### `OrderEvent`

Advances order lifecycle:

- `acked`
- `working`
- `partially_filled`
- `filled`
- `canceled`
- `rejected`

#### `FillEvent`

Carries booking meaning:

- symbol
- client/exchange order ids
- side
- position side
- quantity
- price
- fee
- timestamp

This is the primary driver of position quantity, average entry price, realized PnL, fee totals, and downstream equity state.

#### `FundingEvent`

Must be included in the first live truth version.

Funding must:

- be normalized from exchange payloads
- be persisted in replay storage
- affect runtime ledger booking
- appear in drift and reconciliation reports

### Reconciliation-Only Events

#### `Position Snapshot Event`

Used to compare:

- runtime position qty
- runtime average entry
- exchange reported position

#### `Account Snapshot Event`

Used to compare:

- equity
- available margin
- used margin
- maintenance margin

These events may update an observed-exchange-state view, but they must not overwrite runtime ledger state in the first version.

## Live Data Flow

### Normal Order Path

1. Strategy emits `OrderIntent`
2. `live_runtime_coordinator` calls `RuntimeSession.submit_intents(...)`
3. Runtime produces:
   - `intent_created`
   - `risk_accepted`
   - `submitted`
4. Exchange request is sent to `OKX`
5. `OKX` private order stream emits order state updates
6. Adapter normalizes them into `OrderEvent`
7. Runtime applies those events
8. `OKX` private fill stream emits executions
9. Adapter normalizes them into `FillEvent`
10. Runtime applies fills to order state and ledger
11. All normalized events are persisted to replay storage

### Reconciliation Path

1. Poll or refresh exchange open orders / positions / account
2. Normalize snapshots into observed-exchange-state views
3. Compare against runtime snapshot
4. Emit mismatch report
5. If mismatch severity is high:
   - block stage promotion
   - optionally force read-only degraded mode

## Recovery Model

### Warm Recovery

Use when replay storage is intact.

Process:

1. load recent normalized runtime events
2. rebuild runtime session from replay
3. fetch current exchange open orders / positions / account
4. compare rebuilt runtime state with observed exchange state
5. continue only if mismatches are acceptable

### Cold Recovery

Use when replay storage is incomplete or local state is not trusted.

Process:

1. build observed exchange state only
2. mark runtime ownership as degraded
3. emit a takeover report
4. require operator review before resuming live trading

Cold recovery must default to safety, not convenience.

## Funding Rules

Funding is a required part of first-version live truth.

Rules:

- funding must be normalized as an explicit booking event
- funding must be replayable
- funding must affect wallet/equity consistently with runtime ledger semantics
- funding mismatches must show up in reconciliation and drift outputs

First version simplification:

- funding may be modeled at position-leg granularity
- if exchange payloads are incomplete, missing fields must be surfaced as reconciliation warnings, not guessed silently

## Error Handling

### Duplicate Events

The runtime must ignore duplicate fills and idempotently handle repeated order states where possible.

### Out-Of-Order Events

The coordinator must tolerate normal exchange sequencing noise, while logging state transition anomalies clearly.

### Event Gaps

If the system detects likely missing private-stream segments after reconnect:

- mark the runtime as degraded
- fetch reconciliation snapshots
- prevent unrestricted continuation until mismatch checks pass

### Unsupported Exchange Payloads

Unknown or partially unmapped payloads must be logged and persisted for analysis. They must not be silently discarded if they may carry booking meaning.

## Testing Strategy

### Unit

- adapter state mapping
- runtime order transition rules
- funding booking behavior
- duplicate fill idempotency

### Integration

- `submit intent -> OKX ack -> OKX fill -> ledger`
- `submit intent -> reject`
- `submit intent -> partial fill -> full fill`
- `funding event -> replay -> ledger consistency`

### Recovery

- warm restart from replay
- cold restart into read-only degraded mode
- duplicate event replay
- out-of-order order/fill sequences
- reconnect with missing event window

### Acceptance

- live runtime snapshot contains the same order lineage reconstructable from replay
- funding appears in ledger and drift metrics
- exchange snapshot mismatches block stage promotion
- bootstrap recovery prefers runtime truth and uses exchange state only for reconciliation

## Rollout Gates

### `paper_closure`

Required before live:

- shared runtime path enabled
- replay closure available
- paper closure checks passing

### `micro_live`

Required before broader live:

- `OKX` only
- symbol whitelist
- tiny max notional
- alerts configured
- replay persistence enabled
- reconciliation mismatch below threshold

### `normal_live`

Allowed only after:

- repeated successful warm recovery
- stable funding booking
- stable replay/reconcile outputs
- acceptable paper-vs-live drift under the calibrated model

## Deliverables

The implementation of this spec should produce:

- a live coordinator that owns runtime submission and private-stream application
- explicit funding booking support in the runtime live path
- replay persistence sufficient for warm recovery
- reconciliation reports that compare runtime truth to exchange snapshots
- degraded-mode behavior for unsafe recovery or mismatch conditions

## Follow-On Work

Once `OKX live truth` is complete, the next sub-projects should be:

1. `paper realism`
2. `event backtest realism`
3. `cross-mode drift calibration tightening`

Those should follow this live truth model rather than define their own semantics.
