# OKX Unattended Live MVP Design

## Goal
Ship the first truly unattended `OKX` live-trading runtime for `SWAP`, `cross` margin, `net` mode, and a limited multi-symbol watchlist. The MVP must run as a real long-lived service, not as a CLI payload builder, and must fail closed through `reduce_only` and `blocked` states.

## Scope
- Exchange: `OKX`
- Product: `USDT`-margined `SWAP`
- Position mode: `net`
- Margin mode: `cross`
- Watchlist size: `2-3` symbols
- Strategy cadence: completed `5m` bars only
- Process model: single machine, `supervisor + worker`
- Degrade policy: critical runtime issues move to `reduce_only`
- Auto-recovery policy: move from `reduce_only` back to `live_active` only after `3` consecutive healthy `5m` cycles

## Non-Goals
- Tick or orderbook-driven live trading
- Cross-exchange routing
- Full portfolio optimizer redesign
- Autonomous recovery from `blocked`
- Remote orchestration or distributed workers

## Current State
The repository already has the core live-truth building blocks:
- perp-aware `OKX` client
- bootstrap takeover and reconcile evidence
- readiness gates for account mode, account snapshot, and stream freshness
- strategy intent generation for multi-symbol live use
- total-margin allocation and budget enforcement
- minimal supervisor state model
- operator CLI surfaces for `deploy --mode live`, `autotrade-start`, and `autotrade-status`

The missing piece is the long-lived runtime loop. `autotrade-start` currently returns a validated startup payload, but it does not start a persistent live supervisor/worker process. The market driver seam also exists, but the `OKX` kline stream is still a stub.

## Approaches Considered
### 1. Extend the existing runtime-truth stack with a real supervisor/worker loop
Recommended.

This keeps one execution truth path across bootstrap, readiness, reconcile, paper, replay, and live. It minimizes semantic drift and reuses the existing `OKXPerpClient`, `LiveExecutionService`, allocation, and readiness layers.

### 2. Build a separate unattended live engine
This gives clean isolation, but duplicates core runtime semantics and would likely drift from the tested truth path. It is higher risk for a personal live rollout.

### 3. Drive unattended live through repeated CLI invocations
This is lighter to implement, but it does not fit the required `supervisor + worker` model and is too weak for prompt degrade and recovery handling.

## Chosen Design
Adopt approach `1`: extend the existing runtime-truth stack into a real long-lived unattended runtime.

## Architecture
The MVP will run as one machine-local service composed of one supervisor and three workers.

### LiveSupervisorProcess
Owns the global execution state:
- `bootstrap_pending`
- `warming`
- `live_active`
- `reduce_only`
- `read_only`
- `blocked`

Responsibilities:
- startup sequencing
- state transitions
- operator-visible status
- health-window counting for auto-recovery
- enforcing whether new risk is allowed

### MarketWorker
Consumes live `OKX` market data and emits completed `5m` bars only.

Responsibilities:
- maintain per-symbol bar aggregation or stream completed klines directly
- publish only closed bars into the strategy path
- stamp bar timestamps deterministically
- expose freshness and stall metrics to the supervisor

### StrategyExecutionWorker
Consumes closed-bar batches and turns them into executable intents.

Responsibilities:
- call `LiveStrategyRunner`
- apply `MarginAllocator` budgets
- submit through `LiveExecutionService`
- honor supervisor execution mode
- allow reduce-only orders when the system is degraded

### HealthWorker
Continuously validates runtime truth.

Responsibilities:
- private stream freshness and gap checks
- reconcile scheduling
- account snapshot presence
- bootstrap/runtime net-position consistency
- healthy-cycle counting while in `reduce_only`

## Data Flow
The live path is:

`OKX market worker -> closed 5m bars -> LiveStrategyRunner -> budget/risk checks -> LiveExecutionService -> runtime events/reconcile -> HealthWorker -> LiveSupervisorProcess`

Two control loops run together:

### Trading Loop
- Trigger only when a new completed `5m` bar arrives.
- Build multi-symbol intent batches from the approved watchlist.
- Reject any order that violates:
  - allowed symbol rollout
  - symbol budget
  - execution mode
  - exchange rules
- In `live_active`, both opening and reducing actions are allowed.
- In `reduce_only`, only exposure-reducing actions are allowed.

### Health Loop
- Runs on a shorter fixed interval than the `5m` strategy cycle.
- Updates runtime truth from:
  - private stream state
  - reconcile results
  - account snapshot presence
  - contract mode checks
- On degradation, signals the supervisor immediately.
- During `reduce_only`, increments a healthy-cycle counter only when the entire health contract is green at a completed `5m` boundary.

## State Model
### Startup
1. Load config and credentials.
2. Build `OKXPerpClient`, runtime adapter, and service.
3. Run bootstrap takeover and reconcile.
4. Evaluate readiness.
5. If bootstrap and readiness pass, enter `warming`, then `live_active`.

### Degrade
`live_active -> reduce_only` when any of the following becomes false:
- private stream freshness
- reconcile health
- account snapshot presence
- runtime truth consistency

`live_active` or `reduce_only -> blocked` when any hard safety failure occurs:
- account mode is not `SWAP + cross + net`
- bootstrap or runtime position mismatch is unresolved
- reduce-only operation cannot be trusted
- repeated degraded operation exceeds configured tolerance

### Recovery
- `reduce_only` never returns to `live_active` immediately.
- Recovery requires `3` consecutive healthy completed `5m` cycles.
- Each unhealthy cycle resets the counter.
- `blocked` requires operator intervention and a new startup decision.

## Order Permissions By State
- `warming`: no new risk; optional read-only warm checks only
- `live_active`: full approved live trading
- `reduce_only`: no new exposure; only risk-reducing orders
- `read_only`: no order placement
- `blocked`: no order placement

## Operator Surfaces
`deploy --mode live` remains the preflight and go/no-go evidence command.

`autotrade-start` should evolve from a payload builder into a real process launcher that:
- validates required artifacts
- starts the supervisor and workers
- returns startup metadata and process state

`autotrade-status` should report:
- supervisor state
- execution mode
- watchlist and venue contract
- stream health
- reconcile health
- last degrade reason
- healthy-cycle recovery counter

## Persistence And Recovery
The runtime must preserve:
- OMS state
- runtime replay events
- supervisor state snapshot
- last closed-bar timestamps per symbol
- degrade/recovery counters

After restart:
- bootstrap remains the source of truth for exchange alignment
- local persisted state accelerates warm recovery
- cold or inconsistent recovery must not bypass readiness gates

## Testing Strategy
### Unit Tests
- supervisor transitions
- reduce-only permission enforcement
- healthy-cycle recovery counter
- bar-close gating
- market-driver timestamp handling

### Integration Tests
- `OKX` closed-bar -> strategy -> order intent -> live service flow
- degrade from stream gap
- degrade from reconcile failure
- auto-recover after `3` healthy `5m` cycles
- blocked escalation on hard mismatch

### Operator Acceptance Tests
- `autotrade-start` starts a real runtime process
- `autotrade-status` reflects live supervisor truth
- restart from persisted state respects bootstrap and readiness outcomes

## Rollout Plan
### Phase 1
- one account
- `2-3` symbols
- small total margin cap
- full operator visibility

### Phase 2
- longer unattended soak
- tighter alerting
- production-like restart drills

Scale beyond that only after observing stable degrade/recover behavior under real exchange conditions.

## Success Criteria
The MVP is ready for small-capital unattended live use only when all of the following are true:
- a real long-lived supervisor/worker runtime exists
- `OKX` closed `5m` bars drive the live strategy loop
- `reduce_only` is enforced automatically on runtime degradation
- `reduce_only` auto-recovers only after `3` healthy completed `5m` cycles
- `blocked` remains manual-clear only
- operator status surfaces expose enough truth to decide whether to continue live trading
