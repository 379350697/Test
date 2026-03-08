import time

from quantx.backtest import run_backtest
from quantx.data import generate_demo_data, load_csv
from quantx.models import BacktestConfig


def test_backtest_smoke_performance(tmp_path):
    fp = generate_demo_data(str(tmp_path / "perf.csv"), bars=500)
    candles = load_csv(fp)
    cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")

    t0 = time.perf_counter()
    res = run_backtest(candles, "ma_crossover", {"fast_period": 8, "slow_period": 21}, cfg)
    elapsed = time.perf_counter() - t0

    assert len(res.equity_curve) > 0
    # Keep threshold loose to avoid CI host variance while still catching large regressions.
    assert elapsed < 3.0, f"performance regression detected: {elapsed:.3f}s"
