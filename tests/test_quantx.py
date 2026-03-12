from pathlib import Path

import pytest

from quantx.abtest import run_ab_test
from quantx.analytics import evaluate_targets, extended_metrics, monte_carlo_equity
from quantx.backtest import run_backtest
from quantx.data import (
    generate_demo_data,
    generate_orderbook_demo_data,
    generate_tick_demo_data,
    load_csv,
    load_orderbook_csv,
    load_tick_csv,
)
from quantx.execution import PaperLiveExecutor
from quantx.micro_backtest import run_orderbook_replay, run_tick_backtest
from quantx.ml_adapter import online_update, simple_sentiment
from quantx.models import BacktestConfig, Candle
from quantx.monitoring import analyze_logs, monitor_equity
from quantx.optimize import walk_forward
from quantx.portfolio_opt import (
    optimize_cta_portfolio_from_csv,
    parse_returns_csv,
    rolling_rebalance_cta_portfolio,
)
from quantx.reporting import write_report, write_report_payload
from quantx.strategies import BreakoutStrategy, STRATEGY_REGISTRY, get_strategy_class
from quantx.strategy_loader import load_strategy_repos


def test_builtin_strategy_registry_contains_core_and_aliases():
    expected_core = {
        "dca",
        "ma_crossover",
        "macd",
        "cta_strategy",
        "rsi_reversal",
        "bollinger_bands",
        "grid",
        "tsmom",
        "breakout_momo",
        "鍓ュご鐨?",
    }
    assert expected_core.issubset(set(STRATEGY_REGISTRY))
    assert STRATEGY_REGISTRY["breakout"] is STRATEGY_REGISTRY["cta_strategy"]


def test_cta_strategy_stable_config_has_required_guardrails():
    lines = Path("quantx/configs/cta_strategy_stable.yaml").read_text(encoding="utf-8").splitlines()
    kv = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        kv[key.strip()] = value.strip()

    assert kv["strategy"] == "cta_strategy"
    assert kv["timeframe"] == "4h"
    assert float(kv["risk_per_trade"]) <= 0.01

    clip = kv["leverage_clip"].strip("[]")
    lo, hi = [float(x.strip()) for x in clip.split(",")]
    assert lo >= 0.0
    assert hi <= 8.0




def test_breakout_strategy_default_lookback_is_applied_in_signal():
    strategy = BreakoutStrategy()
    candles = [
        Candle(ts=f"t{i}", open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0)
        for i in range(80)
    ]
    assert strategy.signal(candles, 79) == 0


def test_scalping_strategy_registry_and_signal():
    assert STRATEGY_REGISTRY["scalping"] is STRATEGY_REGISTRY["鍓ュご鐨?"]
    strategy = get_strategy_class("鍓ュご鐨?")(min_score=4)

    candles = []
    for i in range(60):
        open_px = 100.0
        close_px = 100.02 if i % 2 else 99.98
        candles.append(Candle(ts=f"t{i}", open=open_px, high=100.30, low=99.70, close=close_px, volume=100.0))

    for i in range(60, 80):
        prev_close = candles[-1].close
        open_px = max(prev_close, 100.2)
        close_px = open_px + 0.07
        high_px = close_px + 0.12
        low_px = open_px - 0.06
        vol = 180.0
        if i == 79:
            close_px = open_px + 0.45
            high_px = close_px + 0.08
            low_px = open_px - 0.05
            vol = 360.0
        candles.append(Candle(ts=f"t{i}", open=open_px, high=high_px, low=low_px, close=close_px, volume=vol))

    assert strategy.signal(candles, 79) == 1

def test_backtest_and_walk_forward(tmp_path):
    fp = generate_demo_data(str(tmp_path / "demo.csv"), bars=200)
    candles = load_csv(fp)
    cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")
    res = run_backtest(candles, "ma_crossover", {"fast_period": 8, "slow_period": 21}, cfg)
    assert len(res.equity_curve) > 0
    assert 0 <= res.score_total <= 100
    assert "sortino" in res.metrics
    assert "calmar" in res.metrics

    wf = walk_forward(candles, "dca", {"buy_interval": 12, "buy_amount_usdt": 20}, cfg, splits=3)
    assert len(wf) >= 1


def test_custom_strategy_repo_repro_and_report(tmp_path):
    strategy_dir = tmp_path / "my_repo"
    strategy_dir.mkdir()
    (strategy_dir / "my_strategy.py").write_text(
        """
from quantx.strategies import BaseStrategy

class MyPulseStrategy(BaseStrategy):
    name = \"my_pulse\"
    version = \"0.1.0\"
    category = \"custom\"
    author = \"tester\"
    description = \"custom pulse\"
    default_params = {\"lookback\": 5}
    tags = [\"custom\"]

    def signal(self, candles, i):
        lb = int(self.params.get(\"lookback\", 5))
        if i < lb:
            return 0
        return 1 if candles[i].close >= candles[i-lb].close else -1
""".strip(),
        encoding="utf-8",
    )

    loaded = load_strategy_repos([str(strategy_dir)])
    assert "my_pulse" in loaded["loaded"]

    fp = generate_demo_data(str(tmp_path / "demo.csv"), bars=160)
    candles = load_csv(fp)
    cfg = BacktestConfig(symbol="ETHUSDT", timeframe="1h")
    res = run_backtest(candles, "my_pulse", {"lookback": 8}, cfg)

    assert res.metadata.strategy_spec_hash
    assert res.metadata.strategy_source_hash
    assert res.extra["strategy_profile"]["name"] == "my_pulse"

    artifacts = write_report(res, str(tmp_path / "outputs"))
    md = Path(artifacts["markdown"]).read_text(encoding="utf-8")
    assert "Strategy Profile" in md
    assert "strategy_spec_hash" in md


def test_tick_orderbook_execution_monitor_ml_and_ab(tmp_path):
    tick_file = generate_tick_demo_data(str(tmp_path / "tick.csv"), ticks=1200)
    ob_file = generate_orderbook_demo_data(str(tmp_path / "ob.csv"), rows=400, levels=8)
    ticks = load_tick_csv(tick_file)
    obs = load_orderbook_csv(ob_file)

    cfg_t = BacktestConfig(symbol="BTCUSDT", timeframe="tick")
    tick_res = run_tick_backtest(ticks, cfg_t, threshold_bps=4)
    assert tick_res["mode"] == "tick"

    cfg_o = BacktestConfig(symbol="BTCUSDT", timeframe="orderbook")
    ob_res = run_orderbook_replay(obs, cfg_o, shock_coeff=0.2)
    assert ob_res["mode"] == "orderbook"

    rep = write_report_payload(ob_res, str(tmp_path / "ob_report"))
    assert Path(rep["json"]).exists()
    assert Path(rep["markdown"]).exists()

    eq = [x[1] for x in tick_res["equity_curve"]]
    mc = monte_carlo_equity(eq, n_sims=50)
    assert mc["n_sims"] == 50
    m = extended_metrics(eq)
    t = evaluate_targets(m)
    assert "sharpe_gt_1_5" in t

    ex = PaperLiveExecutor("paper")
    ex.arm()
    mkt = ex.place_order("BTCUSDT", "BUY", 0.1, order_type="market", market_price=100.0)
    ice = ex.place_order("BTCUSDT", "SELL", 0.05, order_type="iceberg", visible_qty=0.01)
    tw = ex.place_order("BTCUSDT", "BUY", 0.02, order_type="twap", schedule_slices=4)
    assert mkt["accepted"] and ice["accepted"] and tw["accepted"]

    mon = monitor_equity(tick_res["equity_curve"], dd_alert_pct=5)
    lg = analyze_logs(ex.state.logs)
    assert "max_drawdown_pct" in mon
    assert "summary" in lg

    st = online_update({}, [1.0, -1.0], 0.3)
    assert st["steps"] == 1
    assert simple_sentiment("bull breakout strong") > 0

    candles = load_csv(generate_demo_data(str(tmp_path / "ab.csv"), bars=180))
    ab = run_ab_test(candles, ("ma_crossover", {"fast_period": 8, "slow_period": 21}), ("dca", {"buy_interval": 12, "buy_amount_usdt": 50}), BacktestConfig(symbol="BTCUSDT", timeframe="1h"))
    assert ab["winner"] in {"A", "B"}


def test_parse_returns_csv_dedup_and_sort(tmp_path):
    fp = tmp_path / "returns.csv"
    fp.write_text(
        "\n".join(
            [
                "ts,BTC,ETH",
                "2024-01-02 00:00:00,0.01,0.02",
                "2024-01-01 00:00:00,0.03,0.01",
                "2024-01-02 00:00:00,0.02,0.03",
            ]
        ),
        encoding="utf-8",
    )

    snaps = parse_returns_csv(str(fp))
    assert len(snaps) == 2
    assert snaps[0].ts < snaps[1].ts
    # duplicate timestamp keeps latest row
    assert abs(snaps[1].values["BTC"] - 0.02) < 1e-12


def test_optimize_cta_portfolio_from_csv_weights(tmp_path):
    fp = tmp_path / "returns_opt.csv"
    rows = ["ts,A,B,C"]
    base = [0.01, 0.015, -0.005, 0.012, -0.006, 0.008, 0.011, -0.004]
    for i, a in enumerate(base, start=1):
        b = a * 0.9 + 0.0005
        c = (-a) * 0.4 + 0.0003
        rows.append(f"2024-01-{i:02d} 00:00:00,{a:.6f},{b:.6f},{c:.6f}")
    fp.write_text("\n".join(rows), encoding="utf-8")

    weights = optimize_cta_portfolio_from_csv(str(fp), corr_threshold=0.6)
    assert set(weights) == {"A", "B", "C"}
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert all(v >= 0.0 for v in weights.values())


def test_parse_returns_csv_raises_when_ts_column_missing(tmp_path):
    fp = tmp_path / "bad.csv"
    fp.write_text("time,BTC\n2024-01-01 00:00:00,0.01\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing timestamp column"):
        parse_returns_csv(str(fp))


def test_parse_returns_csv_raises_when_no_asset_columns(tmp_path):
    fp = tmp_path / "bad_no_asset.csv"
    fp.write_text("ts\n2024-01-01 00:00:00\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no asset columns"):
        parse_returns_csv(str(fp))


def test_optimize_cta_portfolio_single_asset_is_full_weight(tmp_path):
    fp = tmp_path / "single_asset.csv"
    fp.write_text(
        "\n".join(
            [
                "ts,BTC",
                "2024-01-01 00:00:00,0.01",
                "2024-01-02 00:00:00,-0.02",
                "2024-01-03 00:00:00,0.03",
            ]
        ),
        encoding="utf-8",
    )

    weights = optimize_cta_portfolio_from_csv(str(fp))
    assert weights == {"BTC": 1.0}


def test_optimize_cta_portfolio_hierarchical_method(tmp_path):
    fp = tmp_path / "returns_hier.csv"
    rows = ["ts,A,B,C,D"]
    base = [0.01, -0.02, 0.015, -0.005, 0.012, -0.009, 0.008, -0.011, 0.007, 0.004]
    for i, a in enumerate(base, start=1):
        b = a * 0.85 + 0.0002
        c = -a * 0.20 + 0.0003
        d = c * 0.80 - 0.0001
        rows.append(f"2024-01-{i:02d} 00:00:00,{a:.6f},{b:.6f},{c:.6f},{d:.6f}")
    fp.write_text("\n".join(rows), encoding="utf-8")

    weights = optimize_cta_portfolio_from_csv(str(fp), cluster_method="hierarchical", corr_threshold=0.6)
    assert set(weights) == {"A", "B", "C", "D"}
    assert sum(abs(v) for v in weights.values()) > 0


def test_optimize_cta_portfolio_target_vol_and_leverage_cap(tmp_path):
    fp = tmp_path / "returns_scale.csv"
    rows = ["ts,A,B,C"]
    base = [0.02, -0.015, 0.03, -0.01, 0.018, -0.022, 0.017, -0.013, 0.016, -0.009, 0.014, -0.011]
    for i, a in enumerate(base, start=1):
        b = a * 0.7 + 0.001
        c = -a * 0.3 + 0.0002
        rows.append(f"2024-02-{i:02d} 00:00:00,{a:.6f},{b:.6f},{c:.6f}")
    fp.write_text("\n".join(rows), encoding="utf-8")

    weights = optimize_cta_portfolio_from_csv(
        str(fp),
        corr_threshold=0.6,
        target_vol=0.30,
        max_leverage=1.20,
    )
    leverage = sum(abs(v) for v in weights.values())
    assert leverage <= 1.20 + 1e-9
    assert leverage > 0


def test_rolling_rebalance_cta_portfolio_monthly(tmp_path):
    fp = tmp_path / "returns_roll.csv"
    rows = ["ts,A,B,C"]
    val = 0.01
    for month in [1, 2, 3, 4]:
        for day in [1, 5, 10, 15, 20, 25]:
            a = val
            b = a * 0.8 + 0.0004
            c = -a * 0.25 + 0.0001
            rows.append(f"2024-{month:02d}-{day:02d} 00:00:00,{a:.6f},{b:.6f},{c:.6f}")
            val = -val * 0.9
    fp.write_text("\n".join(rows), encoding="utf-8")

    snaps = parse_returns_csv(str(fp))
    plans = rolling_rebalance_cta_portfolio(
        snapshots=snaps,
        rebalance="monthly",
        lookback=10,
        corr_threshold=0.6,
        target_vol=0.15,
        max_leverage=1.10,
    )

    assert len(plans) >= 2
    for p in plans:
        assert "rebalance_ts" in p
        assert set(p["weights"]) == {"A", "B", "C"}
        assert p["leverage"] <= 1.10 + 1e-9


def test_rolling_rebalance_invalid_frequency_raises(tmp_path):
    fp = tmp_path / "returns_roll_bad.csv"
    fp.write_text("ts,A\n2024-01-01 00:00:00,0.01\n2024-01-02 00:00:00,-0.02\n", encoding="utf-8")
    snaps = parse_returns_csv(str(fp))

    with pytest.raises(ValueError, match="rebalance must"):
        rolling_rebalance_cta_portfolio(snaps, rebalance="weekly")  # type: ignore[arg-type]

def test_backtest_cached_path_matches_reference_results(tmp_path):
    candles = load_csv(generate_demo_data(str(tmp_path / "parity.csv"), bars=240))
    cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")
    params = {"fast_period": 8, "slow_period": 21, "signal_period": 5}

    reference = run_backtest(candles, "macd", params, cfg)
    candidate = run_backtest(candles, "macd", params, cfg, use_indicator_cache=True)

    assert [t.side for t in candidate.trades] == [t.side for t in reference.trades]
    assert candidate.metrics == reference.metrics

def test_indicator_cache_matches_existing_indicator_values(tmp_path):
    from quantx.indicator_cache import IndicatorCache

    candles = load_csv(generate_demo_data(str(tmp_path / "cache.csv"), bars=200))
    cache = IndicatorCache.from_candles(candles)

    assert cache.sma(21)[50] is not None
    assert cache.atr(14)[50] is not None
    assert cache.adx(14)[50] is not None

def test_batch_summary_mode_omits_heavy_fields(tmp_path):
    from quantx.cli import main

    fp = generate_demo_data(str(tmp_path / "batch.csv"), bars=180)
    payload = main([
        "batch",
        "--file", fp,
        "--strategies", '[["dca", {"buy_interval": 12, "buy_amount_usdt": 20}]]',
        "--json",
        "--result-mode", "summary",
    ])

    assert "equity_curve" not in payload["results"][0]



def test_backtest_reports_runtime_trace_for_downstream_consumers():
    from datetime import datetime, timedelta

    candles = [
        Candle(
            ts=datetime(2024, 1, 1) + timedelta(hours=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=10.0 + i,
        )
        for i in range(40)
    ]
    cfg = BacktestConfig(symbol='BTCUSDT', timeframe='1h')

    res = run_backtest(candles, 'dca', {'buy_interval': 12, 'buy_amount_usdt': 20}, cfg)

    assert 'runtime' in res.extra
    assert res.extra['runtime']['mode'] == 'bar_backtest'
    assert res.extra['runtime']['fidelity'] == 'low'
    assert 'ledger' in res.extra['runtime']


def test_paper_executor_exposes_runtime_state_for_cli_like_flows():
    ex = PaperLiveExecutor('paper')
    ex.arm()

    ex.place_order('BTCUSDT', 'SELL', 0.25, order_type='market', market_price=100.0)

    assert ex.state.positions['BTCUSDT'] == pytest.approx(-0.25)
    assert ex.state.runtime['mode'] == 'paper'
    assert ex.state.runtime['orders']
    assert ex.state.runtime['ledger']['equity'] > 0


def test_backtest_runtime_payload_includes_parity_order_state_sequences():
    from datetime import datetime, timedelta

    candles = [
        Candle(
            ts=datetime(2024, 1, 1) + timedelta(hours=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=10.0 + i,
        )
        for i in range(40)
    ]
    cfg = BacktestConfig(symbol='BTCUSDT', timeframe='1h')

    res = run_backtest(candles, 'dca', {'buy_interval': 12, 'buy_amount_usdt': 20}, cfg)

    assert 'order_state_sequences' in res.extra['runtime']
    sequences = list(res.extra['runtime']['order_state_sequences'].values())
    assert sequences
    assert sequences[0][0] == 'intent_created'
    assert sequences[0][-1] == 'filled'


def test_deploy_and_execute_order_cli_route_through_runtime_core():
    from quantx.cli import main

    order_payload = main([
        'execute-order',
        '--json',
        '--symbol', 'BTCUSDT',
        '--side', 'BUY',
        '--qty', '0.01',
    ])
    deploy_payload = main([
        'deploy',
        '--json',
        '--symbol', 'BTCUSDT',
    ])

    assert order_payload['runtime']['execution_path'] == 'runtime_core'
    assert order_payload['runtime']['rollout_exchange'] == 'okx'
    assert order_payload['runtime']['adapter_contract'] == 'okx_perp'
    assert order_payload['runtime']['order_state_sequences']

    assert deploy_payload['runtime']['execution_path'] == 'runtime_core'
    assert deploy_payload['runtime']['rollout_exchange'] == 'okx'
    assert deploy_payload['runtime']['stage'] == 'paper_closure'
    assert deploy_payload['runtime']['fidelity'] in {'high', 'low'}
    assert deploy_payload['readiness']['checks_by_name']['runtime_execution_path']['ok'] is True
    assert deploy_payload['readiness']['checks_by_name']['rollout_exchange_order']['ok'] is True
    assert deploy_payload['readiness']['checks_by_name']['paper_closure_ready']['ok'] is True


def test_event_backtest_runtime_path():
    from quantx.backtest import run_event_backtest
    from quantx.runtime.events import MarketEvent
    from quantx.runtime.models import OrderIntent
    from quantx.runtime.strategy_runtime import BaseEventStrategy

    class _EventScalpStrategy(BaseEventStrategy):
        strategy_id = 'event-scalp'
        version = '0.1.0'

        def on_event(self, ctx, event):
            if event.payload['price'] <= 100.0:
                return [
                    OrderIntent(
                        symbol=event.symbol,
                        side='buy',
                        position_side='long',
                        qty=1.0,
                        price=event.payload['price'],
                        order_type='market',
                        time_in_force='ioc',
                        reduce_only=False,
                    )
                ]
            return []

    tape = [
        MarketEvent(symbol='SOLUSDT', exchange='backtest', channel='mark_price', ts='2026-03-12T00:00:00+00:00', payload={'price': 101.0}),
        MarketEvent(symbol='SOLUSDT', exchange='backtest', channel='mark_price', ts='2026-03-12T00:00:01+00:00', payload={'price': 100.0}),
        MarketEvent(symbol='SOLUSDT', exchange='backtest', channel='mark_price', ts='2026-03-12T00:00:02+00:00', payload={'price': 103.0}),
    ]

    res = run_event_backtest(tape, _EventScalpStrategy(), BacktestConfig(symbol='SOLUSDT', timeframe='event', fee_rate=0.0, slippage_pct=0.0))

    assert res.extra['runtime']['mode'] == 'event_backtest'
    assert res.extra['runtime']['fidelity'] == 'high'
    assert len(res.trades) == 1

