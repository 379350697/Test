# OKX Unattended Live Gap Design

## Summary

This spec defines the gap between the current `OKX live truth` milestone and the higher operational target of unattended live trading.

The current runtime has cleared an important milestone:

- one shared runtime truth path exists for `order`, `fill`, `funding`, and reconciliation snapshots
- replay, warm recovery, readiness, and CLI visibility now speak a common runtime model
- live truth semantics are test-covered across session, adapter, coordinator, replay, bootstrap, readiness, and CLI surfaces

That is enough to call the event model real.

It is not yet enough to call the system unattended.

For unattended live trading, the requirement is stricter:

- the system must continuously maintain truthful state from live exchange events
- detect when that truth is no longer trustworthy
- automatically fail closed when truth cannot be trusted
- preserve enough evidence and operator context for safe takeover

This spec focuses on the missing operational closure, not on re-explaining the runtime architecture already captured in `2026-03-12-okx-live-truth-design.md`.

## Current Position

### What Is Already Done

The current codebase now has:

- replay-backed `RuntimeSession` truth for live order and ledger state
- explicit normalized `funding`, `position_snapshot`, and `account_snapshot` handling
- a `LiveRuntimeCoordinator` that persists normalized runtime events
- replay-based warm recovery
- reconciliation reporting without snapshot-driven auto-heal
- readiness gates for replay persistence, degraded state, and reconcile health
- acceptance coverage showing replay and CLI surfaces expose runtime truth metadata

### What That Means Operationally

The system is now beyond prototype level.

It can:

- build and persist one runtime truth
- reconstruct that truth after restart
- compare runtime truth with observed exchange state
- expose rollout metadata and stage gates

However, the system still behaves more like a supervised operator tool than a self-protecting unattended service.

## Target

The target for this spec is:

`OKX unattended live`, meaning the system can run for extended periods with minimal active supervision and will prefer safety over continuity whenever runtime truth becomes uncertain.

This does **not** mean "fully autonomous strategy operation under all failures."

It means:

- truth is maintained continuously, not only at submit/reconcile boundaries
- health is measured from real runtime state, not manually injected metadata
- drift, event loss, or recovery uncertainty automatically changes system behavior
- the system can stop itself safely and alert an operator with enough context to intervene

## Design Approaches Considered

### Approach 1: Reliability-First Fail-Closed Closure

Build the missing unattended layer around the current runtime truth components:

- real runtime health derivation
- continuous private-stream ownership
- reconcile-driven degrade/block logic
- startup and recovery decision policy
- stronger operator alerting and evidence capture

Pros:

- builds directly on the implemented runtime truth milestone
- minimizes semantic churn
- improves safety before convenience

Cons:

- does not immediately maximize automation
- requires adding operational state machines and background loops

### Approach 2: Full Automation First

Add aggressive self-healing:

- auto-rebuild from snapshots
- auto-resume after cold recovery
- auto-override local truth from exchange snapshots

Pros:

- superficially closer to "hands-off" operation

Cons:

- contradicts current truth-first design
- raises the risk of silent state corruption
- makes failures harder to audit

### Approach 3: Operator-Tool Only

Stop after current milestone and treat the system as permanently supervised:

- manual reconcile
- manual restart evaluation
- manual live gating

Pros:

- low implementation effort

Cons:

- does not meet the unattended target
- leaves the most dangerous failure modes dependent on human vigilance

### Recommendation

Choose **Approach 1**.

The current runtime truth work created the right foundation. The next step should not be more semantic expansion. It should be operational closure around that truth.

## Core Gap Categories

### 1. Runtime Health Is Not Yet Self-Derived

Current rollout metadata and readiness checks expose `runtime_truth` health, but the most important values are still effectively declarative rather than system-owned.

For unattended live, runtime health must be computed from actual runtime conditions, including:

- replay append availability
- recent private-stream activity freshness
- most recent reconciliation result
- recovery mode and recovery confidence
- runtime event application errors
- degraded or restricted execution mode

The system must be able to answer:

- Is runtime truth healthy?
- Why?
- Since when?
- What behavior is now allowed?

without relying on external manual wiring.

### 2. Private Stream Ownership Is Not Yet a Continuous Service

The code can normalize and ingest live events, but unattended live requires a continuously running exchange-private-stream owner that handles:

- authentication
- subscription lifecycle
- heartbeat monitoring
- reconnect logic
- gap detection
- post-reconnect recovery behavior
- deduplication / resume safety

Without this layer, runtime truth is valid only when upstream delivery is perfect or manually supervised.

### 3. Reconciliation Does Not Yet Control Execution

Reconciliation reports exist, but unattended live requires reconcile outcomes to directly influence system behavior.

At minimum, reconcile outcomes must be able to:

- set `degraded`
- block new live orders
- permit only risk-reducing actions when configured
- trigger high-priority alerts
- demand operator takeover after repeated failures

In unattended mode, reconcile cannot remain informational only.

### 4. Runtime Event Failures Are Not Yet First-Class Faults

Runtime event application currently has soft-failure behavior in parts of the stack.

For unattended live, failures during event ingestion or application must become explicit operational faults, not silently swallowed implementation details.

Expected behavior:

- record the event and error
- transition runtime health downward
- mark the system as degraded or restricted
- stop unsafe continuation automatically

### 5. Bootstrap Recovery Reports Do Not Yet Drive Policy

Warm/cold recovery now exists, but unattended live needs recovery policy, not only recovery reporting.

The system must define what happens when:

- warm recovery succeeds cleanly
- warm recovery succeeds but reconcile mismatches remain
- only cold recovery is possible
- replay is missing or partially corrupt
- observed exchange state is present but runtime continuity is uncertain

Those outcomes must map to automatic behavior such as:

- continue
- continue read-only
- continue reduce-only
- refuse live startup
- switch to dry-run

### 6. Operational Envelope Is Not Yet Defined

Unattended live is partly a code problem and partly an operations problem.

The current milestone does not yet define:

- process supervision expectations
- alert escalation and paging thresholds
- log retention / replay retention policy
- health summary surfaces for operators
- takeover runbooks
- post-incident evidence capture

Without these, the system may be functionally correct but not operationally trustworthy.

## Prioritized Gap Backlog

### P0: Must Exist Before Claiming Unattended Readiness

1. Real runtime health snapshot derived from live system state
2. Continuous private-stream worker with reconnect and freshness tracking
3. Reconcile-driven degrade and execution blocking
4. Event-application faults that explicitly downgrade runtime health
5. Startup policy that converts bootstrap outcomes into automatic safe behavior

### P1: Required for Sustained Low-Touch Operation

1. Gap-detection and event backfill strategy after reconnect
2. Replay retention and rotation policy for long-running processes
3. Reduced-capability execution modes such as `reduce_only` or `read_only`
4. Resilience tests for disconnect, duplication, disorder, and partial corruption
5. Unified health/alert summary surface for operators

### P2: Required for Durable Production-Like Operations

1. Process supervision and restart policy
2. Alert routing, severity tiers, and escalation timing
3. Operator takeover runbook
4. Daily health summary and audit-oriented reporting
5. Config / credential hygiene expectations for long-lived deployment

## Recommended Delivery Sequence

### Phase 1: Fail-Closed Runtime Health

Goal:

- the system automatically refuses unsafe live continuation

Deliverables:

- runtime-owned health snapshot
- explicit degraded transitions on replay or runtime-event faults
- reconcile outcome wired into execution gating
- bootstrap policy mapping recovery results to allowed execution modes

Exit condition:

- if runtime truth becomes uncertain, the system blocks unsafe live activity automatically

### Phase 2: Continuous Live-Truth Maintenance

Goal:

- the system can survive ordinary connectivity and process interruptions without losing trustworthy state

Deliverables:

- private-stream service loop
- disconnect detection and reconnect policy
- gap handling / replay continuity policy
- resilience tests for long-running event integrity

Exit condition:

- short-lived stream interruptions no longer force immediate human intervention, unless truth confidence is lost

### Phase 3: Unattended Operations Closure

Goal:

- the system becomes practically operable without constant supervision

Deliverables:

- alerting and health dashboards or summary outputs
- operator runbooks
- retention / rotation policy
- soak-run verification criteria

Exit condition:

- the system can run for extended periods, fail closed predictably, and hand operators enough evidence for safe recovery

## Safety Rules

The unattended design should preserve these rules:

1. Exchange snapshots remain reconciliation inputs, not silent ledger mutators.
2. Runtime truth health must degrade on uncertainty, not on confirmed loss only.
3. Recovery policy must prefer restricted behavior over optimistic continuation.
4. A system that cannot prove truth continuity must not present itself as fully live-ready.

## Success Criteria

This work should only be called "unattended-ready" when all of the following are true:

- runtime health is computed from real runtime signals
- private-stream continuity is actively monitored
- reconcile failures automatically affect execution permissions
- replay / recovery uncertainty automatically restricts execution
- operators receive actionable health and incident context without manual forensic work
- long-running resilience scenarios are test-covered and soak-validated

## Non-Goals

This spec does not cover:

- strategy alpha quality
- exchange expansion beyond `OKX`
- multi-account orchestration
- automatic PnL optimization
- snapshot-based ledger auto-heal

## Follow-On Output

The next artifact after this spec should be an implementation plan focused on:

- fail-closed runtime health
- continuous private-stream operation
- reconcile-driven execution restrictions
- unattended operations support surfaces