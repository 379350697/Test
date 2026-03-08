from pathlib import Path

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
from quantx.models import BacktestConfig
from quantx.monitoring import analyze_logs, monitor_equity
from quantx.optimize import walk_forward
from quantx.reporting import write_report, write_report_payload
from quantx.strategies import STRATEGY_REGISTRY
from quantx.strategy_loader import load_strategy_repos


def test_builtin_strategy_count():
    assert len(STRATEGY_REGISTRY) == 7


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
