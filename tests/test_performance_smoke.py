import time

from quantx.backtest import run_backtest
from quantx.data import generate_demo_data, load_csv
from quantx.exchanges.base import ExchangePosition, SymbolSpec
from quantx.live_service import LiveExecutionConfig, LiveExecutionService
from quantx.models import BacktestConfig
from quantx.rebalance import TradingConstraints, generate_rebalance_orders
from quantx.risk_engine import RiskLimits, exposure_by_symbol, pretrade_check


class _PerfDummyExchange:
    def place_order(self, order):
        return {"ok": True, "clientOrderId": order.client_order_id}

    def cancel_order(self, symbol: str, client_order_id: str):
        return {"ok": True}

    def get_open_orders(self, symbol: str | None = None):
        return []

    def get_account_positions(self):
        return [ExchangePosition(symbol="USDT", qty=1000.0)]

    def get_symbol_specs(self, symbols: list[str] | None = None):
        spec = SymbolSpec(symbol="BTCUSDT", tick_size=0.1, lot_size=0.001, min_qty=0.001, min_notional=5.0)
        if symbols:
            return {s.upper(): spec for s in symbols}
        return {"BTCUSDT": spec}


def test_backtest_smoke_performance(tmp_path):
    fp = generate_demo_data(str(tmp_path / "perf.csv"), bars=500)
    candles = load_csv(fp)
    cfg = BacktestConfig(symbol="BTCUSDT", timeframe="1h")

    t0 = time.perf_counter()
    res = run_backtest(candles, "ma_crossover", {"fast_period": 8, "slow_period": 21}, cfg)
    elapsed = time.perf_counter() - t0

    assert len(res.equity_curve) > 0
    assert elapsed < 3.0, f"performance regression detected: {elapsed:.3f}s"


def test_rebalance_and_risk_smoke_performance():
    symbols = [f"S{i}" for i in range(120)]
    prices = {s: 100.0 + i for i, s in enumerate(symbols)}
    cur = {s: (i % 5) * 0.01 for i, s in enumerate(symbols)}
    tgt = {s: (1.0 / len(symbols)) for s in symbols}

    t0 = time.perf_counter()
    payload = generate_rebalance_orders(
        current_positions=cur,
        target_weights=tgt,
        prices=prices,
        total_equity=200_000.0,
        constraints=TradingConstraints(min_qty=0.0001, min_notional=5.0, lot_size=0.0001),
    )
    exp = exposure_by_symbol({s: cur[s] for s in symbols}, prices=prices, equity=200_000.0)
    ok, _ = pretrade_check({s: v for s, v in tgt.items()}, order_notional=15_000.0, limits=RiskLimits())
    elapsed = time.perf_counter() - t0

    assert payload["summary"]["symbols"] == len(symbols)
    assert isinstance(exp, dict)
    assert ok
    assert elapsed < 1.0, f"rebalance/risk performance regression: {elapsed:.3f}s"


def test_live_execute_dry_run_smoke_performance():
    svc = LiveExecutionService(
        _PerfDummyExchange(),
        config=LiveExecutionConfig(dry_run=True, allowed_symbols=("BTCUSDT",), max_orders_per_cycle=300),
    )
    svc.sync_symbol_rules(["BTCUSDT"])
    orders = [{"symbol": "BTCUSDT", "side": "BUY", "qty": 0.01, "price": 50000.0} for _ in range(200)]

    t0 = time.perf_counter()
    res = svc.execute_orders(orders)
    elapsed = time.perf_counter() - t0

    assert res["ok"]
    assert len(res["accepted"]) == 200
    assert elapsed < 1.2, f"live execute dry-run regression: {elapsed:.3f}s"
