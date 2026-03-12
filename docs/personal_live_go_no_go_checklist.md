# Personal Live Go/No-Go Checklist (QuantX)

## Goal
Use the same machine-readable contract for backtest, paper soak, bootstrap takeover, and first live capital.

## Promotion Contract
Before any live rollout, all of the following readiness checks must be `ok=true`:

- `promotion_stage_gate`
- `bootstrap_resume_mode_gate`
- `live_truth_replay_persistence`
- `live_truth_not_degraded`
- `live_truth_reconcile_ok`
- `live_truth_stream_fresh`
- `live_truth_execution_mode_allowed`
- `paper_closure_ready`
- `oms_persistence_enabled`

## What `promotion_stage_gate` Means
`promotion_stage_gate` is only green when the shared promotion report says the strategy is ready for live review.

Required promotion sub-checks:

- `backtest_quality`
- `paper_soak_duration`
- `paper_alerts`
- `runtime_truth`
- `resume_mode`

Expected shared gate output:

- `eligible_stage` is `live_ready` or `live`
- `failed_gates` is empty

## What `bootstrap_resume_mode_gate` Means
Run `bootstrap_recover_and_reconcile(...)` before enabling live capital.

The bootstrap output should show:

- `resume_mode` is `reduce_only` or `live`
- `promotion_policy.resume_mode` matches the same value
- `promotion_policy.live_capital_allowed` is `true` only after warm recovery and healthy runtime truth

If bootstrap returns `resume_mode=blocked` or `resume_mode=read_only`, stay in paper or operator review.

## Operator Checklist
- Confirm the latest backtest and paper soak reports were generated from the same strategy/config you plan to trade.
- Confirm `replay-daily` shows no blocking incidents and no recommendation to hold in paper.
- Confirm bootstrap takeover produced no unresolved position or open-order mismatches.
- Start with a small whitelist and capped notional even after all gates are green.

## Suggested Commands
```bash
pytest -q
quantx replay-daily --events runtime/events.jsonl --oms runtime/oms/events.jsonl --audit runtime/audit/events.jsonl --json
```
