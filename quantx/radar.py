from __future__ import annotations

from .backtest import run_backtest
from .models import BacktestConfig


def scan_watchlist(watchlist_data: dict, strategy_name: str, params: dict, base_config: BacktestConfig):
    results = []
    for symbol, candles in watchlist_data.items():
        cfg = BacktestConfig(
            symbol=symbol,
            timeframe=base_config.timeframe,
            initial_cash=base_config.initial_cash,
            fee_rate=base_config.fee_rate,
            slippage_pct=base_config.slippage_pct,
            risk=base_config.risk,
        )
        res = run_backtest(candles, strategy_name, params, cfg, use_indicator_cache=True)
        last_signal = "neutral"
        if res.trades:
            last_signal = "buy" if res.trades[-1].side == "BUY" else "sell"
        risk_explain = {
            "max_drawdown_pct": res.metrics["max_drawdown_pct"],
            "fee_ratio": res.metrics["fee_ratio"],
            "score": res.score_total,
        }
        results.append(
            {
                "symbol": symbol,
                "signal": last_signal,
                "history_context": {
                    "trades": len(res.trades),
                    "return_pct": res.metrics["total_return_pct"],
                },
                "risk_explanation": risk_explain,
            }
        )
    return sorted(results, key=lambda x: x["risk_explanation"]["score"], reverse=True)
