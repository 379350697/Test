# Restart Takeover Runbook (Live Positions Present)

## Goal
Recover local OMS state, reconcile against exchange truth, and fail closed unless bootstrap policy allows safe resumption.

## Standard Flow
1. Recover local state with `JsonlOMSStore` and `OrderManager.recover(...)`.
2. Call `bootstrap_recover_and_reconcile(...)` before any live order submission.
3. Inspect the takeover report fields:
   - `position_diffs`
   - `missing_on_exchange`
   - `unmanaged_on_exchange`
   - `resume_mode`
   - `promotion_policy`
4. Keep live capital disabled until readiness checks are green.

## Bootstrap Policy Mapping
Use bootstrap output to feed the live promotion contract:

- `resume_mode` maps to readiness check `bootstrap_resume_mode_gate`
- `promotion_policy.runtime_truth_ok` should agree with the `live_truth_*` readiness checks
- `promotion_policy.live_capital_allowed` is only advisory; the final decision still depends on readiness checks and shared promotion gates

## Resume Rules
- `resume_mode=blocked`: do not place live orders; investigate cold recovery or missing runtime truth.
- `resume_mode=read_only`: reconcile positions/orders first; do not add new risk.
- `resume_mode=reduce_only`: close or reduce risk only until the rest of the contract is green.
- `resume_mode=live`: bootstrap takeover allows progression to the full readiness review.

## Final Gate Before Orders
After takeover, run readiness and confirm these checks are green before live rollout:

- `promotion_stage_gate`
- `bootstrap_resume_mode_gate`
- `live_truth_replay_persistence`
- `live_truth_not_degraded`
- `live_truth_reconcile_ok`
- `live_truth_stream_fresh`
- `live_truth_execution_mode_allowed`

If any of the checks above is red, stay in paper or operator review and rerun takeover after remediation.
