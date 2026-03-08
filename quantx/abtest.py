from __future__ import annotations

from .analytics import extended_metrics
from .backtest import run_backtest
from .models import BacktestConfig


def run_ab_test(candles, strategy_a: tuple[str, dict], strategy_b: tuple[str, dict], cfg: BacktestConfig) -> dict:
    a = run_backtest(candles, strategy_a[0], strategy_a[1], cfg)
    b = run_backtest(candles, strategy_b[0], strategy_b[1], cfg)
    ae = [v for _, v in a.equity_curve]
    be = [v for _, v in b.equity_curve]
    am = extended_metrics(ae)
    bm = extended_metrics(be)
    score_a = a.score_total + am.get("sortino", 0) * 2 + am.get("calmar", 0)
    score_b = b.score_total + bm.get("sortino", 0) * 2 + bm.get("calmar", 0)
    winner = "A" if score_a >= score_b else "B"
    return {
        "winner": winner,
        "A": {"strategy": strategy_a[0], "metrics": {**a.metrics, **am}, "score": score_a},
        "B": {"strategy": strategy_b[0], "metrics": {**b.metrics, **bm}, "score": score_b},
    }
