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

## First-Week Micro-Live Envelope
Treat the first week as a fixed recommendation, not an application default:

- Watchlist: `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, `XRP-USDT-SWAP`
- `total_margin=1000`
- `max_symbol_weight=0.30`
- `max_leverage=1.0`
- `max_orders_per_cycle=1`
- `max_notional_per_cycle=400`

Do not widen multiple knobs at once. Keep the first live week explicit and operator-reviewed.

## Operator Checklist
- Confirm the latest backtest and paper soak reports were generated from the same strategy/config you plan to trade.
- Confirm `replay-daily` shows no blocking incidents and no recommendation to hold in paper.
- Confirm bootstrap takeover produced no unresolved position or open-order mismatches.
- Run `deploy --mode live` first and treat it as the go/no-go evidence contract, not the unattended runtime loop.
- Start the unattended loop with `autotrade-start` only after the deploy evidence is green, record the returned `process.pid` and `status_path`, then inspect `autotrade-status` for `supervisor.state`, `runtime.execution_mode`, `pilot_risk`, and `venue_contract`.
- Run `autotrade-healthcheck` after start and before leaving the runtime unattended overnight. If it reports `process_dead`, `status_stale`, `blocked`, `read_only`, or `reduce_only`, hold live expansion and remediate before restart.
- Keep the first-week rollout on the `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, `XRP-USDT-SWAP` watchlist and stay inside the recommended `total_margin=1000`, `max_symbol_weight=0.30`, and `max_notional_per_cycle=400` envelope.

## Suggested Commands
```bash
pytest -q
quantx deploy --mode live --exchange okx --symbol BTC-USDT-SWAP --backtest-report outputs/latest/report.json --paper-events runtime/paper/events.jsonl --runtime-events runtime/events.jsonl --oms runtime/oms/events.jsonl --alert-webhook https://example.com/hook --json
quantx autotrade-start --exchange okx --strategy cta_strategy --watchlist '["BTC-USDT-SWAP","ETH-USDT-SWAP","XRP-USDT-SWAP"]' --total-margin 1000 --max-symbol-weight 0.30 --max-notional-per-cycle 400 --backtest-report outputs/latest/report.json --paper-events runtime/paper/events.jsonl --runtime-events runtime/events.jsonl --oms runtime/oms/events.jsonl --alert-webhook https://example.com/hook --json
quantx autotrade-status --exchange okx --strategy cta_strategy --watchlist '["BTC-USDT-SWAP","ETH-USDT-SWAP","XRP-USDT-SWAP"]' --total-margin 1000 --backtest-report outputs/latest/report.json --paper-events runtime/paper/events.jsonl --runtime-events runtime/events.jsonl --oms runtime/oms/events.jsonl --alert-webhook https://example.com/hook --json
quantx autotrade-healthcheck --status-path runtime/autotrade/status.json --stale-after-seconds 60 --alert-webhook https://example.com/hook --json
quantx replay-daily --events runtime/events.jsonl --oms runtime/oms/events.jsonl --audit runtime/audit/events.jsonl --json
```
