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
5. Use `autotrade-status` to inspect the current unattended supervisor state before restarting `autotrade-start`.

## Bootstrap Policy Mapping
Use bootstrap output to feed the live promotion contract:

- `resume_mode` maps to readiness check `bootstrap_resume_mode_gate`
- `promotion_policy.runtime_truth_ok` should agree with the `live_truth_*` readiness checks
- `promotion_policy.live_capital_allowed` is only advisory; the final decision still depends on readiness checks and shared promotion gates

## Resume Rules
- `resume_mode=blocked`: do not place live orders; investigate cold recovery or missing runtime truth. Expect `autotrade-status` to stay in `readiness_blocked` or `blocked`.
- `resume_mode=read_only`: reconcile positions/orders first; do not add new risk. Keep `autotrade-start` disabled until the state clears.
- `resume_mode=reduce_only`: close or reduce risk only until the rest of the contract is green. `autotrade-status` should show `supervisor.state=reduce_only`.
- `resume_mode=live`: bootstrap takeover allows progression to the full readiness review and then `autotrade-start`.

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

Suggested operator sequence:

```bash
quantx deploy --mode live --exchange okx --symbol BTC-USDT-SWAP --backtest-report outputs/latest/report.json --paper-events runtime/paper/events.jsonl --runtime-events runtime/events.jsonl --oms runtime/oms/events.jsonl --alert-webhook https://example.com/hook --json
quantx autotrade-status --exchange okx --strategy cta_strategy --watchlist '["BTC-USDT-SWAP","ETH-USDT-SWAP"]' --total-margin 1000 --backtest-report outputs/latest/report.json --paper-events runtime/paper/events.jsonl --runtime-events runtime/events.jsonl --oms runtime/oms/events.jsonl --alert-webhook https://example.com/hook --json
quantx autotrade-start --exchange okx --strategy cta_strategy --watchlist '["BTC-USDT-SWAP","ETH-USDT-SWAP"]' --total-margin 1000 --backtest-report outputs/latest/report.json --paper-events runtime/paper/events.jsonl --runtime-events runtime/events.jsonl --oms runtime/oms/events.jsonl --alert-webhook https://example.com/hook --json
```
