# OKX Micro Live Pilot Gap And Params Design

## Goal
Define the smallest additional product and operational closure needed to promote the current unattended `OKX` runtime into a real `micro-live` pilot for `BTC`, `ETH`, and `XRP` with small capital, overnight fail-closed behavior, and next-morning operator recovery.

## Scope
- Exchange: `OKX`
- Product: `USDT`-margined `SWAP`
- Symbols: `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, `XRP-USDT-SWAP`
- Pilot style: small-capital real trading, low-touch overnight, manual next-morning intervention
- Runtime posture: prefer `reduce_only` / `blocked` over continuity
- Primary success criteria: execution-chain reliability plus evidence of basic positive expectancy after real trading friction

## Non-Goals
- Fully autonomous overnight recovery back into unrestricted trading
- Production-grade orchestration, distributed workers, or remote control planes
- More symbols, higher leverage, or larger notional scaling during the first pilot week
- Redesign of the strategy, optimizer, or core unattended runtime loop shipped by the MVP
- A new live engine parallel to the existing runtime-truth path

## Current State
The repository now has the first unattended live MVP:
- a real long-lived `autotrade-run` path
- an `OKX` closed-`5m` market driver
- supervisor states with `reduce_only` and `blocked`
- persisted runtime truth through `autotrade-status`
- bootstrap/readiness contracts and operator runbooks

That is enough to call the system an unattended runtime.

It is not yet enough to call it a safe first `micro-live` pilot for real money.

The remaining gap is not a second runtime architecture. It is the last-mile operating contract for a personal pilot:
- reliable detection when the runtime is stale or dead
- alertable operator truth even if the trading process has already exited
- hard pilot risk gates beyond symbol budgeting
- a documented parameter envelope and upgrade discipline for the first week

## Design Approaches Considered

### Approach 1: Add an external watchdog and hard pilot risk gates around the current runtime
Recommended.

Keep the current unattended runtime as the trading core, then add:
- persisted heartbeats and richer runtime status
- an external healthcheck/watchdog surface that reads status truth and checks process liveness
- pilot-specific circuit-breaker enforcement
- operator docs and promotion rules for the first live week

Pros:
- builds directly on the shipped MVP
- addresses the real pilot gaps without replacing working runtime code
- fits the user's desired operating mode: stop safely at night, review in the morning

Cons:
- adds one more operator-facing surface
- requires carefully defining which faults become `reduce_only` versus `blocked`

### Approach 2: Push more self-healing into the unattended runtime itself
Add automatic restarts, automatic recovery from degraded states, and autonomous resume into `live_active`.

Pros:
- looks more automated on paper

Cons:
- mismatches the stated pilot goal
- raises the risk of silent continuation after uncertain recovery
- increases the number of failure branches to trust before the first live week

### Approach 3: Stay with manual status inspection only
Rely on `autotrade-status`, operator discipline, and existing runbooks without adding new watchdog or pilot-specific gates.

Pros:
- minimal implementation effort

Cons:
- does not close the "process died while I was asleep" gap
- weakens the promise of low-touch overnight operation
- leaves too much safety dependent on an operator remembering to poll

### Recommendation
Choose **Approach 1**.

The MVP runtime should remain the execution core. The next step is to harden the operator contract around it rather than invent a more autonomous recovery model.

## Chosen Design

### 1. Persist Operator-Useful Runtime Heartbeats
The existing runtime status store should evolve from "latest supervisor snapshot" into "operator truth for a live pilot".

The persisted status should include:
- `process.pid`
- `process.started_at`
- `runtime.updated_at`
- `runtime.last_market_iteration_at`
- `runtime.last_health_iteration_at`
- `runtime.execution_mode`
- `supervisor.state`
- `supervisor.last_degrade_reason`
- `healthy_cycle_count`
- `last_closed_bar_ts`

This makes the status file useful even when the trading process is no longer running. A separate watcher can then determine whether the runtime is alive, stale, degraded, or blocked.

### 2. Add an External Watchdog / Healthcheck Surface
The pilot needs a status reader that is independent from the trading process.

Add a dedicated watchdog component with a CLI surface such as `autotrade-healthcheck` that:
- loads the persisted runtime status
- verifies that the recorded process is still alive
- classifies stale runtime status based on elapsed time since `runtime.updated_at`
- treats `reduce_only`, `read_only`, and `blocked` as distinct operator outcomes
- optionally dispatches alerts through the existing webhook router
- exits non-zero when the pilot should be considered unhealthy

This watchdog is the key to the user's intended operating mode. If the trading process dies, the watchdog can still detect and report that failure. The system does not need to auto-heal overnight; it needs to fail visible and closed.

### 3. Promote Pilot Risk Limits From Soft Guidance To Hard Runtime Gates
The existing symbol budgets and per-cycle notional cap are necessary, but they are not enough for real-money pilot protection.

Integrate the existing `RiskCircuitBreaker` into the live runtime path so the pilot can enforce:
- `max_orders_per_day`
- `max_daily_loss`
- explicit circuit state in runtime status

Expected behavior:
- once the circuit is tripped, new risk must stop immediately
- the runtime should surface the trip reason in persisted status
- the resulting execution mode should be operator-visible and fail closed

The first pilot week does not need a sophisticated portfolio risk engine. It does need hard, machine-enforced stop conditions.

### 4. Keep Pilot Parameterization Simple And Explicit
Do not add a new preset system for the first iteration. Reuse the existing `autotrade-start` arguments and document one recommended parameter envelope.

Recommended starting envelope for the first pilot week:
- watchlist: `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, `XRP-USDT-SWAP`
- `total_margin=1000`
- `max_symbol_weight=0.30`
- `max_leverage=1.0`
- `max_orders_per_cycle=1`
- `max_notional_per_cycle=400`

Parameter semantics:
- `total_margin`: total margin budget for the pilot
- `max_symbol_weight`: maximum share of that budget allocated to any one symbol
- `max_notional_per_cycle`: total notional that may be sent in one execution cycle

The implementation should avoid hiding these behind magic defaults. The pilot should remain explicit, inspectable, and easy to reason about.

### 5. Encode A First-Week Upgrade Discipline
The system should support one clear operating rule:
- do not increase multiple risk knobs at once
- do not scale until runtime truth, OMS, replay, and alert evidence remain aligned for several days

This is primarily a documentation and operator-flow requirement, not a new automation feature.

## Architecture
The micro-live pilot architecture keeps the current unattended runtime intact and adds two surrounding layers.

### Trading Core
The existing unattended runtime remains responsible for:
- closed-bar market ingestion
- strategy execution
- supervisor state transitions
- runtime status persistence

### Watchdog Layer
A new pure status-classification layer should:
- read persisted runtime truth
- inspect local process liveness
- classify health into a small set of operator outcomes
- publish alerts using the existing webhook router

This logic should live in a focused module rather than being buried directly in the CLI so it is easy to test in isolation.

### Pilot Risk Layer
The risk-circuit-breaker layer should sit on the execution path and expose a serializable snapshot back to runtime status so operators can tell whether the pilot stopped because of market conditions, execution degradation, or a hard risk budget.

## Data Flow
The desired control loop is:

`autotrade-run -> runtime status store -> watchdog healthcheck -> webhook/operator`

and, for execution safety:

`strategy intents -> live execution service -> circuit breaker -> supervisor/runtime status -> watchdog/operator`

This keeps one source of trading truth while allowing health visibility to survive process exit.

## Failure Model

### Degrade To `reduce_only`
Use `reduce_only` when runtime continuity is uncertain but the system can still safely reduce exposure:
- stream freshness concerns
- temporary reconcile uncertainty
- operator-requested caution mode

### Escalate To `blocked`
Use `blocked` when the pilot should not continue trading at all:
- process no longer alive
- runtime status stale past the configured threshold
- circuit breaker tripped
- bootstrap or reconcile hard mismatch
- repeated execution failures beyond tolerated limits

### No Overnight Auto-Resume
The first pilot week should not attempt to automatically return from `blocked` to unrestricted live trading. Restart decisions stay with the operator after morning review.

## Testing Strategy

### Unit Tests
- watchdog classifies healthy, degraded, blocked, stale, and dead-process states
- runtime status persistence includes heartbeat fields
- circuit-breaker snapshot serialization is stable

### Integration Tests
- live runtime writes fresh timestamps on bootstrap, market, and health iterations
- healthcheck CLI reads the status store and emits the right outcome
- circuit-breaker breaches block new risk and surface the reason in status

### Operator Acceptance Tests
- `autotrade-start` returns `pid` and `status_path`
- `autotrade-healthcheck` reports healthy for a running fresh process
- `autotrade-healthcheck` reports blocked for stale or dead process state
- go/no-go docs and restart runbook reflect the recommended `BTC/ETH/XRP` pilot envelope

## Success Criteria
This design is successful when all of the following are true:
- the unattended runtime publishes enough heartbeat truth for an external monitor to judge health
- the operator can detect `process_dead`, `status_stale`, `reduce_only`, and `blocked` without attaching to the runtime process
- the pilot can hard-stop on daily loss or order-count circuit limits
- the first pilot-week parameter envelope is documented and consistent with the code path
- the system still prefers fail-closed behavior over autonomous continuation
