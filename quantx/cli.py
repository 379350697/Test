from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .abtest import run_ab_test
from .alerts import AlertRouter
from .analytics import in_out_sample_split, monte_carlo_equity
from .backtest import result_to_dict, run_backtest, run_parallel_matrix
from .bootstrap import bootstrap_recover_and_reconcile
from .credentials import credential_presence_snapshot, load_binance_credentials, load_okx_credentials
from .data import (
    generate_demo_data,
    generate_orderbook_demo_data,
    generate_tick_demo_data,
    inspect_data,
    load_csv,
    load_orderbook_csv,
    load_tick_csv,
)
from .exchange import fetch_binance_klines, write_ohlcv_csv
from .execution import PaperLiveExecutor
from .exchanges.binance import BinanceClient
from .exchanges.binance_perp import BinancePerpAdapter
from .exchanges.okx_perp_client import OKXPerpClient
from .exchanges.okx_perp import OKXPerpAdapter
from .live_margin_allocator import MarginAllocator
from .live_service import LiveExecutionConfig, LiveExecutionService
from .live_supervisor import LiveSupervisor
from .micro_backtest import run_orderbook_replay, run_tick_backtest
from .ml_adapter import online_update, simple_sentiment
from .models import BacktestConfig
from .monitoring import analyze_logs, monitor_equity
from .oms import JsonlOMSStore
from .paper_harness import run_paper_harness
from .optimize import grid_search, random_scan, walk_forward
from .radar import scan_watchlist
from .readiness import ReadinessContext, assert_ready, evaluate_readiness, rollout_stage
from .release_gates import evaluate_release_gates
from .replay import build_daily_replay_report
from .reporting import build_venue_contract, write_report, write_report_payload
from .risk_engine import RiskLimits
from .strategies import get_strategy_class, list_strategies
from .strategy_loader import load_strategy_repos

def _print(payload: dict | list, as_json: bool):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(payload)


def _attach_strategy_repo_args(cmd):
    cmd.add_argument("--strategy-repo", action="append", default=[], help="custom strategy file/dir path, can repeat")


def _runtime_truth_snapshot(*, recovery_mode: str) -> dict[str, object]:
    execution_mode = 'blocked' if recovery_mode == 'cold' else 'live'
    return {
        'replay_persistence': True,
        'degraded': False,
        'reconcile_ok': True,
        'recovery_mode': recovery_mode,
        'resume_mode': execution_mode,
        'execution_mode': execution_mode,
        'stream': {
            'state': 'idle',
            'stale': False,
            'gap_detected': False,
            'reconcile_required': False,
        },
    }

def _runtime_cli_metadata(executor: PaperLiveExecutor, *, exchange: str, enable_binance: bool) -> dict[str, object]:
    runtime_truth = _runtime_truth_snapshot(recovery_mode='cold' if executor.state.mode == 'paper' else 'warm')
    return {
        'execution_path': 'runtime_core',
        'runtime_mode': 'derivatives',
        'rollout_exchange': exchange,
        'adapter_contract': f'{exchange}_perp',
        'rollout_gate': 'okx_first' if exchange == 'okx' or enable_binance else 'blocked_until_okx_rollout',
        'stage': 'paper_closure' if executor.state.mode == 'paper' else 'micro_live',
        'fidelity': 'high',
        'order_state_sequences': executor.state.runtime.get('order_state_sequences', {}),
        'recovery_mode': str(runtime_truth['recovery_mode']),
        'runtime_truth': runtime_truth,
    }


def _promotion_gate_preview(*, mode: str, runtime_truth: dict[str, object]) -> dict[str, object]:
    paper_complete = mode == 'live'
    return evaluate_release_gates(
        backtest={'ok': True, 'max_drawdown_pct': 8.0},
        paper={
            'ok': paper_complete,
            'continuous_hours': 30 if paper_complete else 0,
            'alerts_ok': True,
        },
        live={
            'runtime_truth_ok': bool(runtime_truth.get('replay_persistence')) and not bool(runtime_truth.get('degraded')),
            'resume_mode': str(runtime_truth.get('resume_mode', runtime_truth.get('execution_mode', 'blocked'))),
        },
    )


def _readiness_preview(symbol: str, *, mode: str, exchange: str, enable_binance: bool) -> dict[str, object]:
    router = AlertRouter()
    router.register_webhook('ops', 'https://example.com/hook')
    runtime_truth = _runtime_truth_snapshot(recovery_mode='cold' if mode != 'live' else 'warm')
    promotion_gates = _promotion_gate_preview(mode=mode, runtime_truth=runtime_truth)
    ctx = ReadinessContext(
        live_config=LiveExecutionConfig(
            dry_run=(mode != 'live'),
            allowed_symbols=(symbol.upper(),),
            max_orders_per_cycle=1,
            max_notional_per_cycle=1000.0,
            runtime_mode='derivatives',
            exchange=exchange,
            enable_binance=enable_binance,
        ),
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=1000.0),
        alert_router=router,
        oms_store=None,
        runtime_status=runtime_truth,
        promotion_gates=promotion_gates,
    )
    report = evaluate_readiness(ctx)
    return {
        'ok': report.ok,
        'score': report.score,
        'stage': rollout_stage(ctx),
        'checks': report.checks,
        'checks_by_name': {check['name']: check for check in report.checks},
        'promotion_gates': promotion_gates,
    }

def _load_report_payload(path: str) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f'deploy_report_payload_invalid:{path}')
    return payload


def _build_exchange_client(exchange: str, **_: Any):
    exchange_name = exchange.lower()
    if exchange_name == 'okx':
        creds = load_okx_credentials()
        return OKXPerpClient(
            api_key=creds.api_key,
            api_secret=creds.api_secret,
            passphrase=creds.passphrase,
            inst_type='SWAP',
        )
    if exchange_name == 'binance':
        creds = load_binance_credentials()
        return BinanceClient(api_key=creds.api_key, api_secret=creds.api_secret)
    raise ValueError(f'unsupported_exchange:{exchange}')


def _build_runtime_adapter(exchange: str):
    exchange_name = exchange.lower()
    if exchange_name == 'okx':
        return OKXPerpAdapter()
    if exchange_name == 'binance':
        return BinancePerpAdapter()
    raise ValueError(f'unsupported_exchange:{exchange}')


def _build_backtest_gate_input(report_path: str) -> tuple[dict[str, object], dict[str, object]]:
    payload = _load_report_payload(report_path)
    promotion_summary = payload.get('promotion_summary', {}) if isinstance(payload.get('promotion_summary'), dict) else {}
    metrics = payload.get('metrics', {}) if isinstance(payload.get('metrics'), dict) else {}
    max_drawdown_pct = promotion_summary.get('max_drawdown_pct', metrics.get('max_drawdown_pct', 0.0))
    return (
        {
            'ok': bool(promotion_summary or metrics),
            'max_drawdown_pct': float(max_drawdown_pct or 0.0),
        },
        promotion_summary,
    )


def _build_paper_gate_input(event_log_path: str, *, duration_minutes: int) -> tuple[dict[str, object], dict[str, object]]:
    summary = run_paper_harness(event_log_path=event_log_path, duration_minutes=duration_minutes)
    return (
        {
            'ok': Path(event_log_path).exists(),
            'continuous_hours': float(summary.get('continuous_minutes', 0.0) or 0.0) / 60.0,
            'alerts_ok': bool(summary.get('alerts_ok', False)),
        },
        summary,
    )


def _rollout_gate(exchange: str, enable_binance: bool) -> str:
    return 'okx_first' if exchange == 'okx' or enable_binance else 'blocked_until_okx_rollout'


def _build_alert_router(webhooks: list[str]) -> AlertRouter:
    router = AlertRouter()
    for idx, webhook in enumerate(webhooks):
        name = 'ops' if idx == 0 else f'ops_{idx + 1}'
        router.register_webhook(name, webhook)
    return router


def _readiness_payload(report, *, ctx: ReadinessContext, promotion_gates: dict[str, object]) -> dict[str, object]:
    return {
        'ok': report.ok,
        'score': report.score,
        'stage': rollout_stage(ctx),
        'checks': report.checks,
        'checks_by_name': {check['name']: check for check in report.checks},
        'promotion_gates': promotion_gates,
    }


def _live_deploy_missing_artifacts(args) -> list[str]:
    required = ('backtest_report', 'paper_events', 'runtime_events', 'oms')
    missing: list[str] = []
    for field_name in required:
        value = getattr(args, field_name, '')
        if not str(value or '').strip():
            missing.append(field_name)
    return missing


def _build_live_deploy_payload(args) -> dict[str, object]:
    missing = _live_deploy_missing_artifacts(args)
    if missing:
        raise ValueError(f"deploy_live_requires:{','.join(missing)}")

    symbol = args.symbol.upper()
    backtest_gate, promotion_summary = _build_backtest_gate_input(args.backtest_report)
    paper_gate, paper_summary = _build_paper_gate_input(
        args.paper_events,
        duration_minutes=args.paper_duration_minutes,
    )
    live_config = LiveExecutionConfig(
        dry_run=False,
        allowed_symbols=(symbol,),
        max_orders_per_cycle=args.max_orders_per_cycle,
        max_notional_per_cycle=args.max_notional_per_cycle,
        runtime_mode='derivatives',
        exchange=args.exchange,
        enable_binance=args.enable_binance,
    )
    service = LiveExecutionService(
        _build_exchange_client(args.exchange),
        config=live_config,
        runtime_adapter=_build_runtime_adapter(args.exchange),
        runtime_event_log_path=(args.runtime_events or None),
    )
    service.sync_symbol_rules([symbol])

    oms_store = JsonlOMSStore(args.oms)
    bootstrap_report = bootstrap_recover_and_reconcile(
        service=service,
        oms_store=oms_store,
        initial_cash=args.initial_cash,
        symbol=args.symbol,
        runtime_event_log_path=(args.runtime_events or None),
    )
    runtime_truth = bootstrap_report.get('runtime_status', {})
    promotion_gates = evaluate_release_gates(
        backtest=backtest_gate,
        paper=paper_gate,
        live={
            'runtime_truth_ok': bool((bootstrap_report.get('promotion_policy') or {}).get('runtime_truth_ok', False)),
            'resume_mode': str(bootstrap_report.get('resume_mode', 'blocked')),
        },
    )
    ctx = ReadinessContext(
        live_config=live_config,
        risk_limits=RiskLimits(max_symbol_weight=0.5, max_order_notional=args.max_notional_per_cycle),
        alert_router=_build_alert_router(args.alert_webhook),
        oms_store=oms_store,
        runtime_status=runtime_truth if isinstance(runtime_truth, dict) else {},
        promotion_gates=promotion_gates,
    )
    readiness_report = assert_ready(ctx)
    runtime_snapshot = service.runtime_snapshot()
    venue_contract = build_venue_contract(
        symbol=symbol,
        exchange=args.exchange,
        fidelity=str(promotion_summary.get('fidelity', 'high')),
    )
    return {
        'bootstrap': bootstrap_report,
        'paper': paper_summary,
        'venue_contract': venue_contract,
        'runtime_mode': str(venue_contract['runtime_mode']),
        'fidelity': str(venue_contract['fidelity']),
        'runtime': {
            'execution_path': 'live_service',
            'runtime_mode': 'derivatives',
            'venue_contract': venue_contract,
            'rollout_exchange': args.exchange,
            'adapter_contract': f'{args.exchange}_perp',
            'rollout_gate': _rollout_gate(args.exchange, args.enable_binance),
            'stage': rollout_stage(ctx),
            'fidelity': str(promotion_summary.get('fidelity', 'high')),
            'order_state_sequences': runtime_snapshot.get('order_state_sequences', {}),
            'recovery_mode': str((runtime_truth or {}).get('recovery_mode', 'unknown')),
            'runtime_truth': runtime_truth,
        },
        'readiness': _readiness_payload(readiness_report, ctx=ctx, promotion_gates=promotion_gates),
        'promotion_gates': promotion_gates,
        'state': {
            'symbol_rules': {
                key: {
                    'tick_size': value.tick_size,
                    'lot_size': value.lot_size,
                    'min_qty': value.min_qty,
                    'min_notional': value.min_notional,
                }
                for key, value in service.symbol_rules.items()
            },
            'runtime_snapshot': runtime_snapshot,
        },
    }


def _parse_watchlist(value: str) -> tuple[str, ...]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError('autotrade_watchlist_invalid') from exc
    if not isinstance(payload, list):
        raise ValueError('autotrade_watchlist_invalid')

    watchlist: list[str] = []
    seen: set[str] = set()
    for item in payload:
        symbol = str(item).upper().strip()
        if not symbol or symbol in seen:
            continue
        watchlist.append(symbol)
        seen.add(symbol)
    if not watchlist:
        raise ValueError('autotrade_watchlist_invalid')
    return tuple(watchlist)


def _parse_strategy_params(value: str) -> dict[str, Any]:
    text = str(value or '').strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError('autotrade_strategy_params_invalid') from exc
    if not isinstance(payload, dict):
        raise ValueError('autotrade_strategy_params_invalid')
    return dict(payload)


def _autotrade_missing_requirements(args) -> list[str]:
    missing = _live_deploy_missing_artifacts(args)
    if not str(getattr(args, 'strategy', '') or '').strip():
        missing.append('strategy')
    if not str(getattr(args, 'watchlist', '') or '').strip():
        missing.append('watchlist')
    if float(getattr(args, 'total_margin', 0.0) or 0.0) <= 0:
        missing.append('total_margin')
    return missing


def _build_autotrade_strategy_payload(
    strategy_name: str,
    *,
    strategy_params: dict[str, Any],
    watchlist: tuple[str, ...],
    default_max_leverage: float,
) -> tuple[dict[str, object], dict[str, float], float]:
    strategy_cls = get_strategy_class(strategy_name)
    sizing_hints: dict[str, dict[str, Any]] = {}
    target_scores: dict[str, float] = {}
    resolved_max_leverage = max(float(default_max_leverage or 0.0), 1.0)

    for symbol in watchlist:
        strategy = strategy_cls(**strategy_params)
        raw_hints = strategy.live_sizing_hints(symbol)
        hints = dict(raw_hints) if isinstance(raw_hints, dict) else {}
        sizing_hints[symbol] = hints
        score = float(hints.get('entry_margin_pct', 1.0) or 1.0)
        target_scores[symbol] = score if score > 0 else 1.0
        symbol_leverage = float(hints.get('max_leverage', default_max_leverage) or default_max_leverage)
        resolved_max_leverage = max(resolved_max_leverage, symbol_leverage)

    profile = strategy_cls.profile() if hasattr(strategy_cls, 'profile') else {'name': strategy_name}
    return (
        {
            'name': strategy_name,
            'params': strategy_params,
            'watchlist': list(watchlist),
            'profile': profile,
            'sizing_hints': sizing_hints,
        },
        target_scores,
        resolved_max_leverage,
    )


def _serialize_symbol_budgets(budgets: dict[str, object]) -> dict[str, dict[str, float]]:
    payload: dict[str, dict[str, float]] = {}
    for symbol, budget in budgets.items():
        payload[str(symbol).upper()] = {
            'max_margin': float(getattr(budget, 'max_margin', 0.0) or 0.0),
            'max_notional': float(getattr(budget, 'max_notional', 0.0) or 0.0),
            'max_leverage': float(getattr(budget, 'max_leverage', 0.0) or 0.0),
        }
    return payload


def _build_supervisor_snapshot(runtime_status: dict[str, Any], *, ready: bool) -> dict[str, object]:
    supervisor = LiveSupervisor()
    stream_status = runtime_status.get('stream', {}) if isinstance(runtime_status.get('stream'), dict) else {}
    execution_mode = str(runtime_status.get('execution_mode', 'blocked'))

    if not ready:
        supervisor.state = 'readiness_blocked'
    else:
        supervisor.mark_bootstrap_ready()
        if not bool(runtime_status.get('bootstrap_net_position_match', True)):
            supervisor.on_position_mismatch_detected()
        elif execution_mode == 'read_only':
            supervisor.mark_read_only()
        elif execution_mode == 'reduce_only' or bool(stream_status.get('gap_detected')) or bool(stream_status.get('stale')) or bool(stream_status.get('reconcile_required')):
            supervisor.on_stream_gap_detected()
        elif execution_mode == 'live':
            supervisor.mark_live_active()

    return {
        'state': supervisor.state,
        'execution_mode': execution_mode,
        'recovery_mode': str(runtime_status.get('recovery_mode', 'unknown')),
        'stream': stream_status,
        'degraded': bool(runtime_status.get('degraded', False)),
    }


def _build_autotrade_payload(args, *, enforce_ready: bool) -> dict[str, object]:
    missing = _autotrade_missing_requirements(args)
    if missing:
        raise ValueError(f"autotrade_requires:{','.join(missing)}")

    watchlist = _parse_watchlist(args.watchlist)
    strategy_params = _parse_strategy_params(args.strategy_params)
    strategy_payload, target_scores, allocator_max_leverage = _build_autotrade_strategy_payload(
        args.strategy,
        strategy_params=strategy_params,
        watchlist=watchlist,
        default_max_leverage=float(args.max_leverage),
    )
    backtest_gate, promotion_summary = _build_backtest_gate_input(args.backtest_report)
    paper_gate, paper_summary = _build_paper_gate_input(
        args.paper_events,
        duration_minutes=args.paper_duration_minutes,
    )
    max_notional_per_cycle = float(args.max_notional_per_cycle or 0.0)
    if max_notional_per_cycle <= 0:
        max_notional_per_cycle = max(float(args.total_margin) * allocator_max_leverage, float(args.total_margin))

    live_config = LiveExecutionConfig(
        dry_run=False,
        allowed_symbols=watchlist,
        max_orders_per_cycle=args.max_orders_per_cycle,
        max_notional_per_cycle=max_notional_per_cycle,
        runtime_mode='derivatives',
        exchange=args.exchange,
        enable_binance=args.enable_binance,
    )
    service = LiveExecutionService(
        _build_exchange_client(args.exchange),
        config=live_config,
        runtime_adapter=_build_runtime_adapter(args.exchange),
        runtime_event_log_path=(args.runtime_events or None),
    )
    service.sync_symbol_rules(list(watchlist))

    allocator = MarginAllocator(
        total_margin=float(args.total_margin),
        max_symbol_weight=float(args.max_symbol_weight),
        max_leverage=allocator_max_leverage,
    )
    budgets = allocator.allocate(watchlist=watchlist, target_scores=target_scores)
    service.set_symbol_budgets(budgets)

    oms_store = JsonlOMSStore(args.oms)
    bootstrap_report = bootstrap_recover_and_reconcile(
        service=service,
        oms_store=oms_store,
        initial_cash=args.initial_cash,
        symbol=None,
        runtime_event_log_path=(args.runtime_events or None),
    )
    runtime_truth = bootstrap_report.get('runtime_status', {})
    runtime_truth_payload = runtime_truth if isinstance(runtime_truth, dict) else {}
    promotion_gates = evaluate_release_gates(
        backtest=backtest_gate,
        paper=paper_gate,
        live={
            'runtime_truth_ok': bool((bootstrap_report.get('promotion_policy') or {}).get('runtime_truth_ok', False)),
            'resume_mode': str(bootstrap_report.get('resume_mode', 'blocked')),
        },
    )
    ctx = ReadinessContext(
        live_config=live_config,
        risk_limits=RiskLimits(
            max_symbol_weight=float(args.max_symbol_weight),
            max_order_notional=max_notional_per_cycle,
        ),
        alert_router=_build_alert_router(args.alert_webhook),
        oms_store=oms_store,
        runtime_status=runtime_truth_payload,
        promotion_gates=promotion_gates,
    )
    readiness_report = assert_ready(ctx) if enforce_ready else evaluate_readiness(ctx)
    runtime_snapshot = service.runtime_snapshot()
    supervisor_payload = _build_supervisor_snapshot(runtime_truth_payload, ready=readiness_report.ok)
    venue_contract = build_venue_contract(
        symbol=watchlist[0],
        exchange=args.exchange,
        fidelity=str(promotion_summary.get('fidelity', 'high')),
    )

    return {
        'strategy': strategy_payload,
        'allocation': {
            'total_margin': float(args.total_margin),
            'max_symbol_weight': float(args.max_symbol_weight),
            'max_leverage': float(allocator_max_leverage),
            'target_scores': target_scores,
            'symbol_budgets': _serialize_symbol_budgets(budgets),
        },
        'supervisor': supervisor_payload,
        'bootstrap': bootstrap_report,
        'paper': paper_summary,
        'venue_contract': venue_contract,
        'runtime_mode': str(venue_contract['runtime_mode']),
        'fidelity': str(venue_contract['fidelity']),
        'runtime': {
            'execution_path': 'runtime_core',
            'runtime_mode': 'derivatives',
            'venue_contract': venue_contract,
            'rollout_exchange': args.exchange,
            'adapter_contract': f'{args.exchange}_perp',
            'rollout_gate': _rollout_gate(args.exchange, args.enable_binance),
            'stage': rollout_stage(ctx),
            'fidelity': str(promotion_summary.get('fidelity', 'high')),
            'allowed_symbols': list(watchlist),
            'order_state_sequences': runtime_snapshot.get('order_state_sequences', {}),
            'recovery_mode': str(runtime_truth_payload.get('recovery_mode', 'unknown')),
            'execution_mode': str(runtime_truth_payload.get('execution_mode', 'blocked')),
            'runtime_truth': runtime_truth_payload,
        },
        'readiness': _readiness_payload(readiness_report, ctx=ctx, promotion_gates=promotion_gates),
        'promotion_gates': promotion_gates,
        'state': {
            'symbol_rules': {
                key: {
                    'tick_size': value.tick_size,
                    'lot_size': value.lot_size,
                    'min_qty': value.min_qty,
                    'min_notional': value.min_notional,
                }
                for key, value in service.symbol_rules.items()
            },
            'runtime_snapshot': runtime_snapshot,
        },
    }

def _autotrade_runtime_paths(args) -> dict[str, Path]:
    state_dir = Path(args.oms).resolve().parent / 'autotrade'
    return {
        'state_dir': state_dir,
        'config_path': state_dir / 'runtime_config.json',
        'status_path': state_dir / 'status.json',
    }


def _autotrade_runtime_config(args, *, status_path: Path) -> dict[str, object]:
    return {
        'exchange': args.exchange,
        'enable_binance': bool(args.enable_binance),
        'strategy': args.strategy,
        'strategy_params': _parse_strategy_params(args.strategy_params),
        'watchlist': list(_parse_watchlist(args.watchlist)),
        'total_margin': float(args.total_margin),
        'max_symbol_weight': float(args.max_symbol_weight),
        'max_leverage': float(args.max_leverage),
        'max_orders_per_cycle': int(args.max_orders_per_cycle),
        'max_notional_per_cycle': float(args.max_notional_per_cycle or 0.0),
        'runtime_events': str(args.runtime_events or ''),
        'status_path': str(status_path),
    }


def _seed_autotrade_runtime_store(payload: dict[str, object], *, pid: int | None = None) -> dict[str, object]:
    supervisor = payload.get('supervisor', {}) if isinstance(payload.get('supervisor'), dict) else {}
    runtime = payload.get('runtime', {}) if isinstance(payload.get('runtime'), dict) else {}
    runtime_truth = runtime.get('runtime_truth', {}) if isinstance(runtime.get('runtime_truth'), dict) else {}
    seed: dict[str, object] = {
        'supervisor': {
            'state': str(supervisor.get('state', 'warming')),
            'execution_mode': str(supervisor.get('execution_mode', runtime.get('execution_mode', 'blocked'))),
            'last_degrade_reason': runtime_truth.get('last_degrade_reason'),
        },
        'healthy_cycle_count': 0,
        'watchlist': list((payload.get('strategy', {}) or {}).get('watchlist', [])) if isinstance(payload.get('strategy'), dict) else [],
        'last_closed_bar_ts': {},
        'runtime': {
            'execution_path': 'runtime_core',
            'stage': str(runtime.get('stage', 'micro_live')),
        },
        'runtime_truth': dict(runtime_truth),
    }
    if pid is not None:
        seed['process'] = {'pid': int(pid)}
    return seed


def _spawn_autotrade_runtime(args, *, config_path: Path):
    import subprocess
    import sys

    return subprocess.Popen([sys.executable, '-m', 'quantx.cli', 'autotrade-run', '--config', str(config_path)])


def _build_autotrade_start_payload(args) -> dict[str, object]:
    from .live_runtime_store import LiveRuntimeStore

    payload = _build_autotrade_payload(args, enforce_ready=True)
    paths = _autotrade_runtime_paths(args)
    paths['state_dir'].mkdir(parents=True, exist_ok=True)

    config_payload = _autotrade_runtime_config(args, status_path=paths['status_path'])
    paths['config_path'].write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding='utf-8')

    process = _spawn_autotrade_runtime(args, config_path=paths['config_path'])
    pid = int(getattr(process, 'pid', 0) or 0)
    LiveRuntimeStore(paths['status_path']).write_status(_seed_autotrade_runtime_store(payload, pid=pid))

    payload['process'] = {
        'pid': pid,
        'config_path': str(paths['config_path']),
        'status_path': str(paths['status_path']),
    }
    runtime_payload = payload.get('runtime', {}) if isinstance(payload.get('runtime'), dict) else {}
    runtime_payload['execution_path'] = 'runtime_core'
    payload['runtime'] = runtime_payload
    return payload


def _build_autotrade_status_payload(args) -> dict[str, object]:
    from .live_runtime_store import LiveRuntimeStore

    payload = _build_autotrade_payload(args, enforce_ready=False)
    paths = _autotrade_runtime_paths(args)
    stored = LiveRuntimeStore(paths['status_path']).read_status()

    runtime_payload = payload.get('runtime', {}) if isinstance(payload.get('runtime'), dict) else {}
    runtime_payload['execution_path'] = 'runtime_core'
    payload['runtime'] = runtime_payload

    if not stored:
        return payload

    supervisor = stored.get('supervisor', {}) if isinstance(stored.get('supervisor'), dict) else {}
    if isinstance(payload.get('supervisor'), dict):
        payload['supervisor'].update({
            'state': str(supervisor.get('state', payload['supervisor'].get('state', 'blocked'))),
            'execution_mode': str(supervisor.get('execution_mode', payload['supervisor'].get('execution_mode', 'blocked'))),
        })

    stored_runtime = stored.get('runtime', {}) if isinstance(stored.get('runtime'), dict) else {}
    runtime_truth = runtime_payload.get('runtime_truth', {}) if isinstance(runtime_payload.get('runtime_truth'), dict) else {}
    stored_truth = stored.get('runtime_truth', {}) if isinstance(stored.get('runtime_truth'), dict) else {}
    runtime_truth.update(stored_truth)
    if supervisor:
        runtime_truth['execution_mode'] = str(supervisor.get('execution_mode', runtime_truth.get('execution_mode', 'blocked')))
    runtime_payload.update(stored_runtime)
    runtime_payload['execution_path'] = str(stored_runtime.get('execution_path', 'runtime_core'))
    runtime_payload['runtime_truth'] = runtime_truth
    payload['runtime'] = runtime_payload

    if isinstance(stored.get('process'), dict):
        payload['process'] = dict(stored['process'])
    return payload



def _pid_is_alive(pid: int) -> bool:
    import os

    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _autotrade_healthcheck_status_path(args) -> Path:
    if getattr(args, 'status_path', ''):
        return Path(str(args.status_path))
    if getattr(args, 'config', ''):
        config = _load_report_payload(str(args.config))
        raw_status_path = str(config.get('status_path', '') or '')
        if raw_status_path:
            return Path(raw_status_path)
    raise ValueError('autotrade_healthcheck_requires_status_path')


def _autotrade_healthcheck_channels(webhooks: list[str]) -> list[str]:
    return ['ops' if idx == 0 else f'ops_{idx + 1}' for idx, _ in enumerate(webhooks)]


def _build_autotrade_healthcheck_payload(args) -> dict[str, object]:
    from datetime import datetime, timezone

    from .alerts import AlertMessage
    from .live_runtime_store import LiveRuntimeStore
    from .live_watchdog import evaluate_live_watchdog

    status_path = _autotrade_healthcheck_status_path(args)
    stored = LiveRuntimeStore(status_path).read_status()
    process = stored.get('process', {}) if isinstance(stored.get('process'), dict) else {}
    pid = int(process.get('pid', 0) or 0)
    result = evaluate_live_watchdog(
        status_payload=stored,
        process_alive=_pid_is_alive(pid),
        now=datetime.now(timezone.utc),
        stale_after_s=int(args.stale_after_seconds),
    )

    alerts: list[dict[str, object]] = []
    webhooks = list(getattr(args, 'alert_webhook', []) or [])
    if result.should_alert and webhooks:
        router = _build_alert_router(webhooks)
        message = AlertMessage(
            level='ERROR' if result.status == 'blocked' else 'WARN',
            title=f'autotrade healthcheck {result.reason}',
            body=json.dumps(
                {
                    'status': result.status,
                    'reason': result.reason,
                    'detail': result.detail,
                    'status_path': str(status_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        for channel in _autotrade_healthcheck_channels(webhooks):
            alerts.append(router.send(channel, message))

    return {
        'ok': result.ok,
        'status': result.status,
        'reason': result.reason,
        'detail': result.detail,
        'alerts': alerts,
        'status_path': str(status_path),
    }


def _run_autotrade_runtime(args) -> dict[str, object]:
    from .exchanges.okx_private_stream import OKXPrivateStreamTransport
    from .live_market_driver import OKXKlineMarketDriver
    from .live_runtime import LiveRuntime, LiveRuntimeConfig
    from .live_runtime_store import LiveRuntimeStore

    config = _load_report_payload(args.config)
    watchlist = tuple(str(symbol).upper() for symbol in config.get('watchlist', []))
    strategy_params = dict(config.get('strategy_params', {})) if isinstance(config.get('strategy_params'), dict) else {}

    live_config = LiveExecutionConfig(
        dry_run=False,
        allowed_symbols=watchlist,
        max_orders_per_cycle=int(config.get('max_orders_per_cycle', 1) or 1),
        max_notional_per_cycle=float(config.get('max_notional_per_cycle', 0.0) or 0.0),
        runtime_mode='derivatives',
        exchange=str(config.get('exchange', 'okx')),
        enable_binance=bool(config.get('enable_binance', False)),
    )
    service = LiveExecutionService(
        _build_exchange_client(str(config.get('exchange', 'okx'))),
        config=live_config,
        runtime_adapter=_build_runtime_adapter(str(config.get('exchange', 'okx'))),
        runtime_event_log_path=(str(config.get('runtime_events', '')) or None),
    )
    service.sync_symbol_rules(list(watchlist))
    allocator = MarginAllocator(
        total_margin=float(config.get('total_margin', 0.0) or 0.0),
        max_symbol_weight=float(config.get('max_symbol_weight', 0.5) or 0.5),
        max_leverage=float(config.get('max_leverage', 1.0) or 1.0),
    )
    service.set_symbol_budgets(allocator.allocate(watchlist=watchlist, target_scores={symbol: 1.0 for symbol in watchlist}))

    transport = OKXPrivateStreamTransport() if str(config.get('exchange', 'okx')).lower() == 'okx' else None
    runtime = LiveRuntime(
        config=LiveRuntimeConfig(
            watchlist=watchlist,
            strategy_name=str(config.get('strategy', '')),
            strategy_params=strategy_params,
            total_margin=float(config.get('total_margin', 0.0) or 0.0),
            max_symbol_weight=float(config.get('max_symbol_weight', 0.5) or 0.5),
        ),
        market_driver=OKXKlineMarketDriver(client=service.client, watchlist=watchlist, timeframe='5m'),
        private_stream_transport=transport,
        service=service,
        store=LiveRuntimeStore(Path(str(config.get('status_path', 'status.json')))),
    )
    runtime.bootstrap_once()
    runtime.run_forever()
    return runtime.status()

def build_parser():
    p = argparse.ArgumentParser(prog="quantx")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("data-generate")
    d.add_argument("--out", default="data/demo.csv")
    d.add_argument("--bars", type=int, default=1200)
    d.add_argument("--json", action="store_true")

    dt = sub.add_parser("data-generate-tick")
    dt.add_argument("--out", default="data/tick_demo.csv")
    dt.add_argument("--ticks", type=int, default=5000)
    dt.add_argument("--json", action="store_true")

    do = sub.add_parser("data-generate-orderbook")
    do.add_argument("--out", default="data/orderbook_demo.csv")
    do.add_argument("--rows", type=int, default=1000)
    do.add_argument("--levels", type=int, default=5)
    do.add_argument("--json", action="store_true")

    fk = sub.add_parser("data-fetch-klines")
    fk.add_argument("--exchange", default="binance", choices=["binance"])
    fk.add_argument("--symbol", required=True)
    fk.add_argument("--timeframe", default="1m")
    fk.add_argument("--limit", type=int, default=1000)
    fk.add_argument("--out", required=True)
    fk.add_argument("--json", action="store_true")

    i = sub.add_parser("data-inspect")
    i.add_argument("--file", required=True)
    i.add_argument("--json", action="store_true")

    sl = sub.add_parser("strategy-list")
    _attach_strategy_repo_args(sl)
    sl.add_argument("--json", action="store_true")

    b = sub.add_parser("backtest")
    b.add_argument("--file", required=True)
    b.add_argument("--strategy", required=True)
    b.add_argument("--params", default="{}")
    b.add_argument("--symbol", default="BTCUSDT")
    b.add_argument("--timeframe", default="1h")
    b.add_argument("--report-dir", default="outputs/latest")
    _attach_strategy_repo_args(b)
    b.add_argument("--json", action="store_true")

    bt = sub.add_parser("backtest-tick")
    bt.add_argument("--file", required=True)
    bt.add_argument("--symbol", default="BTCUSDT")
    bt.add_argument("--threshold-bps", type=float, default=4.0)
    bt.add_argument("--report-dir", default="outputs/tick")
    bt.add_argument("--json", action="store_true")

    bob = sub.add_parser("backtest-orderbook")
    bob.add_argument("--file", required=True)
    bob.add_argument("--symbol", default="BTCUSDT")
    bob.add_argument("--shock-coeff", type=float, default=0.15)
    bob.add_argument("--report-dir", default="outputs/orderbook")
    bob.add_argument("--json", action="store_true")

    ios = sub.add_parser("backtest-inout")
    ios.add_argument("--file", required=True)
    ios.add_argument("--strategy", required=True)
    ios.add_argument("--params", default="{}")
    ios.add_argument("--split", type=float, default=0.7)
    ios.add_argument("--symbol", default="BTCUSDT")
    ios.add_argument("--timeframe", default="1h")
    _attach_strategy_repo_args(ios)
    ios.add_argument("--json", action="store_true")

    mc = sub.add_parser("monte-carlo")
    mc.add_argument("--file", required=True)
    mc.add_argument("--strategy", required=True)
    mc.add_argument("--params", default="{}")
    mc.add_argument("--sims", type=int, default=200)
    mc.add_argument("--symbol", default="BTCUSDT")
    mc.add_argument("--timeframe", default="1h")
    _attach_strategy_repo_args(mc)
    mc.add_argument("--json", action="store_true")

    ab = sub.add_parser("ab-test")
    ab.add_argument("--file", required=True)
    ab.add_argument("--a", required=True, help='json tuple ["strategy", {params}]')
    ab.add_argument("--b", required=True, help='json tuple ["strategy", {params}]')
    ab.add_argument("--symbol", default="BTCUSDT")
    ab.add_argument("--timeframe", default="1h")
    _attach_strategy_repo_args(ab)
    ab.add_argument("--json", action="store_true")

    ex = sub.add_parser("execute-order")
    ex.add_argument("--mode", choices=["paper", "live"], default="paper")
    ex.add_argument("--exchange", choices=["okx", "binance"], default="okx")
    ex.add_argument("--enable-binance", action="store_true")
    ex.add_argument("--symbol", default="BTCUSDT")
    ex.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    ex.add_argument("--qty", type=float, default=0.01)
    ex.add_argument("--position-side", choices=["long", "short"])
    ex.add_argument("--order-type", choices=["market", "limit", "iceberg", "twap", "vwap"], default="market")
    ex.add_argument("--limit-price", type=float)
    ex.add_argument("--market-price", type=float, default=100.0)
    ex.add_argument("--visible-qty", type=float)
    ex.add_argument("--slices", type=int, default=5)
    ex.add_argument("--broker-quotes", default="{}", help='json dict broker->price')
    ex.add_argument("--json", action="store_true")
    mon = sub.add_parser("monitor")
    mon.add_argument("--report-json", required=True)
    mon.add_argument("--dd-alert", type=float, default=10.0)
    mon.add_argument("--json", action="store_true")

    lg = sub.add_parser("log-analyze")
    lg.add_argument("--lines", required=True, help="json list of log lines")
    lg.add_argument("--json", action="store_true")

    ml = sub.add_parser("ml-online")
    ml.add_argument("--state", default="{}")
    ml.add_argument("--features", required=True, help="json float list")
    ml.add_argument("--target", type=float, required=True)
    ml.add_argument("--lr", type=float, default=0.01)
    ml.add_argument("--json", action="store_true")

    sm = sub.add_parser("sentiment")
    sm.add_argument("--text", required=True)
    sm.add_argument("--json", action="store_true")

    bm = sub.add_parser("batch")
    bm.add_argument("--file", required=True)
    bm.add_argument("--strategies", required=True)
    bm.add_argument("--symbols", default='["BTCUSDT"]')
    bm.add_argument("--timeframes", default='["1h"]')
    bm.add_argument("--workers", type=int, default=2)
    bm.add_argument("--result-mode", choices=["full", "summary", "minimal"], default="full")
    _attach_strategy_repo_args(bm)
    bm.add_argument("--json", action="store_true")

    o = sub.add_parser("optimize")
    o.add_argument("--file", required=True)
    o.add_argument("--strategy", required=True)
    o.add_argument("--method", choices=["grid", "random"], default="grid")
    o.add_argument("--space", required=True)
    o.add_argument("--samples", type=int, default=30)
    _attach_strategy_repo_args(o)
    o.add_argument("--json", action="store_true")

    w = sub.add_parser("walk-forward")
    w.add_argument("--file", required=True)
    w.add_argument("--strategy", required=True)
    w.add_argument("--params", default="{}")
    w.add_argument("--splits", type=int, default=3)
    _attach_strategy_repo_args(w)
    w.add_argument("--json", action="store_true")

    r = sub.add_parser("radar")
    r.add_argument("--files", required=True)
    r.add_argument("--strategy", required=True)
    r.add_argument("--params", default="{}")
    _attach_strategy_repo_args(r)
    r.add_argument("--json", action="store_true")


    ph = sub.add_parser("paper-harness")
    ph.add_argument("--event-log-path", required=True)
    ph.add_argument("--duration-minutes", type=int, default=60)
    ph.add_argument("--json", action="store_true")

    rp = sub.add_parser("replay-daily")
    rp.add_argument("--events", required=True, help="jsonl event log path")
    rp.add_argument("--oms", default="", help="optional oms jsonl store path")
    rp.add_argument("--audit", default="", help="optional audit jsonl store path")
    rp.add_argument("--day", default="", help="target day YYYY-MM-DD, default utc today")
    rp.add_argument("--out", default="", help="optional output json path")
    rp.add_argument("--json", action="store_true")


    cred = sub.add_parser("credentials-check")
    cred.add_argument("--exchange", choices=["binance", "okx", "all"], default="all")
    cred.add_argument("--json", action="store_true")

    x = sub.add_parser("deploy")
    x.add_argument("--mode", choices=["paper", "live"], default="paper")
    x.add_argument("--exchange", choices=["okx", "binance"], default="okx")
    x.add_argument("--enable-binance", action="store_true")
    x.add_argument("--symbol", default="BTCUSDT")
    x.add_argument("--backtest-report", default="")
    x.add_argument("--paper-events", default="")
    x.add_argument("--paper-duration-minutes", type=int, default=1440)
    x.add_argument("--runtime-events", default="")
    x.add_argument("--oms", default="")
    x.add_argument("--alert-webhook", action="append", default=[])
    x.add_argument("--initial-cash", type=float, default=0.0)
    x.add_argument("--max-orders-per-cycle", type=int, default=1)
    x.add_argument("--max-notional-per-cycle", type=float, default=1000.0)
    x.add_argument("--json", action="store_true")

    ats = sub.add_parser("autotrade-start")
    ats.add_argument("--exchange", choices=["okx", "binance"], default="okx")
    ats.add_argument("--enable-binance", action="store_true")
    ats.add_argument("--strategy", required=True)
    ats.add_argument("--strategy-params", default="{}")
    ats.add_argument("--watchlist", required=True)
    ats.add_argument("--total-margin", type=float, required=True)
    ats.add_argument("--backtest-report", default="")
    ats.add_argument("--paper-events", default="")
    ats.add_argument("--paper-duration-minutes", type=int, default=1440)
    ats.add_argument("--runtime-events", default="")
    ats.add_argument("--oms", default="")
    ats.add_argument("--alert-webhook", action="append", default=[])
    ats.add_argument("--initial-cash", type=float, default=0.0)
    ats.add_argument("--max-orders-per-cycle", type=int, default=1)
    ats.add_argument("--max-notional-per-cycle", type=float, default=0.0)
    ats.add_argument("--max-symbol-weight", type=float, default=0.5)
    ats.add_argument("--max-leverage", type=float, default=1.0)
    _attach_strategy_repo_args(ats)
    ats.add_argument("--json", action="store_true")

    ast = sub.add_parser("autotrade-status")
    ast.add_argument("--exchange", choices=["okx", "binance"], default="okx")
    ast.add_argument("--enable-binance", action="store_true")
    ast.add_argument("--strategy", required=True)
    ast.add_argument("--strategy-params", default="{}")
    ast.add_argument("--watchlist", required=True)
    ast.add_argument("--total-margin", type=float, required=True)
    ast.add_argument("--backtest-report", default="")
    ast.add_argument("--paper-events", default="")
    ast.add_argument("--paper-duration-minutes", type=int, default=1440)
    ast.add_argument("--runtime-events", default="")
    ast.add_argument("--oms", default="")
    ast.add_argument("--alert-webhook", action="append", default=[])
    ast.add_argument("--initial-cash", type=float, default=0.0)
    ast.add_argument("--max-orders-per-cycle", type=int, default=1)
    ast.add_argument("--max-notional-per-cycle", type=float, default=0.0)
    ast.add_argument("--max-symbol-weight", type=float, default=0.5)
    ast.add_argument("--max-leverage", type=float, default=1.0)
    _attach_strategy_repo_args(ast)
    ast.add_argument("--json", action="store_true")
    ah = sub.add_parser("autotrade-healthcheck")
    ah.add_argument("--config", default="")
    ah.add_argument("--status-path", default="")
    ah.add_argument("--stale-after-seconds", type=int, default=60)
    ah.add_argument("--alert-webhook", action="append", default=[])
    ah.add_argument("--json", action="store_true")
    atr = sub.add_parser("autotrade-run")
    atr.add_argument("--config", required=True)
    atr.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_info = load_strategy_repos(args.strategy_repo) if hasattr(args, "strategy_repo") else {"loaded": [], "files": []}

    if args.cmd == "data-generate":
        _print({"ok": True, "file": generate_demo_data(args.out, bars=args.bars)}, args.json)
        return
    if args.cmd == "data-generate-tick":
        _print({"ok": True, "file": generate_tick_demo_data(args.out, ticks=args.ticks), "depth": "tick"}, args.json)
        return
    if args.cmd == "data-generate-orderbook":
        _print({"ok": True, "file": generate_orderbook_demo_data(args.out, rows=args.rows, levels=args.levels), "depth": "L2+"}, args.json)
        return
    if args.cmd == "data-fetch-klines":
        try:
            rows = fetch_binance_klines(args.symbol, timeframe=args.timeframe, limit=args.limit)
            out = write_ohlcv_csv(rows, args.out)
            _print({"ok": True, "rows": len(rows), "file": out, "symbol": args.symbol, "timeframe": args.timeframe}, args.json)
        except Exception as e:
            _print({"ok": False, "error": str(e), "symbol": args.symbol, "timeframe": args.timeframe}, True if args.json else False)
        return
    if args.cmd == "data-inspect":
        _print(inspect_data(load_csv(args.file)), args.json)
        return
    if args.cmd == "strategy-list":
        _print({"strategies": list_strategies(), "custom_loaded": load_info}, args.json)
        return

    if args.cmd == "backtest":
        cfg = BacktestConfig(symbol=args.symbol, timeframe=args.timeframe)
        res = run_backtest(load_csv(args.file), args.strategy, json.loads(args.params), cfg)
        payload = result_to_dict(res)
        payload["artifacts"] = write_report(res, args.report_dir)
        payload["custom_loaded"] = load_info
        _print(payload, args.json)
        return payload
    if args.cmd == "backtest-tick":
        payload = run_tick_backtest(load_tick_csv(args.file), BacktestConfig(symbol=args.symbol, timeframe="tick"), threshold_bps=args.threshold_bps)
        payload["artifacts"] = write_report_payload(payload, args.report_dir)
        _print(payload, args.json)
        return
    if args.cmd == "backtest-orderbook":
        payload = run_orderbook_replay(load_orderbook_csv(args.file), BacktestConfig(symbol=args.symbol, timeframe="orderbook"), shock_coeff=args.shock_coeff)
        payload["artifacts"] = write_report_payload(payload, args.report_dir)
        _print(payload, args.json)
        return
    if args.cmd == "backtest-inout":
        candles = load_csv(args.file)
        ins, oos = in_out_sample_split(candles, args.split)
        cfg = BacktestConfig(symbol=args.symbol, timeframe=args.timeframe)
        p = json.loads(args.params)
        ir = run_backtest(ins, args.strategy, p, cfg)
        orr = run_backtest(oos, args.strategy, p, cfg)
        _print({"in_sample": result_to_dict(ir), "out_sample": result_to_dict(orr), "split": args.split}, args.json)
        return
    if args.cmd == "monte-carlo":
        cfg = BacktestConfig(symbol=args.symbol, timeframe=args.timeframe)
        res = run_backtest(load_csv(args.file), args.strategy, json.loads(args.params), cfg)
        eq = [v for _, v in res.equity_curve]
        _print({"base": result_to_dict(res), "monte_carlo": monte_carlo_equity(eq, n_sims=args.sims)}, args.json)
        return
    if args.cmd == "ab-test":
        a = json.loads(args.a)
        b = json.loads(args.b)
        cfg = BacktestConfig(symbol=args.symbol, timeframe=args.timeframe)
        _print(run_ab_test(load_csv(args.file), (a[0], a[1]), (b[0], b[1]), cfg), args.json)
        return

    if args.cmd == "execute-order":
        ex = PaperLiveExecutor(args.mode)
        ex.arm()
        rec = ex.place_order(
            symbol=args.symbol,
            side=args.side,
            qty=args.qty,
            order_type=args.order_type,
            limit_price=args.limit_price,
            market_price=args.market_price,
            visible_qty=args.visible_qty,
            schedule_slices=args.slices,
            broker_quotes=json.loads(args.broker_quotes),
            position_side=args.position_side,
        )
        payload = {
            "order": rec,
            "state": ex.state.__dict__,
            "runtime": _runtime_cli_metadata(ex, exchange=args.exchange, enable_binance=args.enable_binance),
        }
        _print(payload, args.json)
        return payload
    if args.cmd == "monitor":
        payload = json.loads(open(args.report_json, "r", encoding="utf-8").read())
        _print(monitor_equity(payload.get("equity_curve", []), dd_alert_pct=args.dd_alert), args.json)
        return
    if args.cmd == "log-analyze":
        _print(analyze_logs(json.loads(args.lines)), args.json)
        return
    if args.cmd == "ml-online":
        _print(online_update(json.loads(args.state), json.loads(args.features), args.target, args.lr), args.json)
        return
    if args.cmd == "sentiment":
        _print({"sentiment_score": simple_sentiment(args.text)}, args.json)
        return

    if args.cmd == "batch":
        candles = load_csv(args.file)
        strategies = json.loads(args.strategies)
        symbols = json.loads(args.symbols)
        tfs = json.loads(args.timeframes)
        data_map = {(s, tf): candles for s in symbols for tf in tfs}
        results = run_parallel_matrix(
            data_map,
            strategies,
            {"symbol": "", "timeframe": ""},
            max_workers=args.workers,
            strategy_repo_paths=args.strategy_repo,
            use_indicator_cache=True,
        )
        payload = {
            "count": len(results),
            "results": [result_to_dict(r, mode=args.result_mode) for r in results],
            "custom_loaded": load_info,
        }
        _print(payload, args.json)
        return payload
    if args.cmd == "optimize":
        candles = load_csv(args.file)
        cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")
        space = json.loads(args.space)
        payload = grid_search(candles, args.strategy, space, cfg) if args.method == "grid" else random_scan(candles, args.strategy, space, args.samples, cfg)
        _print({"top": payload[:10], "count": len(payload), "custom_loaded": load_info}, args.json)
        return
    if args.cmd == "walk-forward":
        candles = load_csv(args.file)
        cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")
        _print({"windows": walk_forward(candles, args.strategy, json.loads(args.params), cfg, splits=args.splits), "custom_loaded": load_info}, args.json)
        return
    if args.cmd == "radar":
        files = json.loads(args.files)
        watchlist = {k: load_csv(v) for k, v in files.items()}
        cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")
        _print({"opportunities": scan_watchlist(watchlist, args.strategy, json.loads(args.params), cfg), "custom_loaded": load_info}, args.json)
        return

    if args.cmd == "paper-harness":
        payload = run_paper_harness(
            event_log_path=args.event_log_path,
            duration_minutes=args.duration_minutes,
        )
        _print(payload, True if args.json else False)
        return payload

    if args.cmd == "replay-daily":
        payload = build_daily_replay_report(
            event_log_path=args.events,
            oms_store_path=(args.oms or None),
            audit_store_path=(args.audit or None),
            day=(args.day or None),
        )
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        _print(payload, True if args.json else False)
        return payload


    if args.cmd == "credentials-check":
        snapshot = credential_presence_snapshot()
        payload: dict[str, object] = {"ok": True, "exchange": args.exchange, "present": snapshot}
        try:
            if args.exchange in {"binance", "all"}:
                load_binance_credentials()
            if args.exchange in {"okx", "all"}:
                load_okx_credentials()
        except ValueError as exc:
            payload["ok"] = False
            payload["error"] = str(exc)
        _print(payload, True if args.json else False)
        return

    if args.cmd == "autotrade-start":
        payload = _build_autotrade_start_payload(args)
        _print(payload, args.json)
        return payload

    if args.cmd == "autotrade-status":
        payload = _build_autotrade_status_payload(args)
        _print(payload, args.json)
        return payload

    if args.cmd == "autotrade-healthcheck":
        payload = _build_autotrade_healthcheck_payload(args)
        _print(payload, args.json)
        if argv is None and not payload.get('ok', False):
            raise SystemExit(1)
        return payload
    if args.cmd == "autotrade-run":
        payload = _run_autotrade_runtime(args)
        _print(payload, args.json)
        return payload

    if args.cmd == "deploy":
        if args.mode == 'live':
            payload = _build_live_deploy_payload(args)
            _print(payload, args.json)
            return payload

        ex = PaperLiveExecutor(args.mode)
        ex.arm()
        probe = ex.place_order(args.symbol, "BUY", 0.01, position_side="long")
        close = ex.close_all()
        payload = {
            "probe_order": probe,
            "close_all": close,
            "state": ex.state.__dict__,
            "runtime": _runtime_cli_metadata(ex, exchange=args.exchange, enable_binance=args.enable_binance),
            "readiness": _readiness_preview(
                args.symbol,
                mode=args.mode,
                exchange=args.exchange,
                enable_binance=args.enable_binance,
            ),
        }
        _print(payload, args.json)
        return payload

if __name__ == "__main__":
    main()
