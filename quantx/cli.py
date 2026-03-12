from __future__ import annotations

import argparse
import json

from .abtest import run_ab_test
from .alerts import AlertRouter
from .analytics import in_out_sample_split, monte_carlo_equity
from .backtest import result_to_dict, run_backtest, run_parallel_matrix
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
from .live_service import LiveExecutionConfig
from .micro_backtest import run_orderbook_replay, run_tick_backtest
from .ml_adapter import online_update, simple_sentiment
from .models import BacktestConfig
from .monitoring import analyze_logs, monitor_equity
from .optimize import grid_search, random_scan, walk_forward
from .radar import scan_watchlist
from .readiness import ReadinessContext, evaluate_readiness, rollout_stage
from .release_gates import evaluate_release_gates
from .replay import build_daily_replay_report
from .reporting import write_report, write_report_payload
from .risk_engine import RiskLimits
from .strategies import list_strategies
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
    x.add_argument("--json", action="store_true")
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
        return
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
        return


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

    if args.cmd == "deploy":
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








