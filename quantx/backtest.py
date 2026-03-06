from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime
from math import sqrt
from statistics import mean

from .models import BacktestConfig, BacktestResult, Position, RunMetadata, Trade
from .analytics import evaluate_targets, extended_metrics
from .repro import now_utc_iso, python_fingerprint, stable_hash
from .strategies import get_strategy_class
from .strategy_loader import load_strategy_repos


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 0
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return abs(mdd)


def _stability_score(metrics: dict[str, float], n_trades: int) -> tuple[dict[str, float], float]:
    quality = min(100.0, max(0.0, metrics.get("sharpe", 0) * 20 + 50))
    risk = max(0.0, 100 - metrics.get("max_drawdown_pct", 100) * 200)
    robustness = min(100.0, 40 + metrics.get("win_rate", 0) * 60)
    cost = max(0.0, 100 - metrics.get("fee_ratio", 1) * 500)
    overtrade = max(0.0, 100 - max(0, n_trades - 200) * 0.5)
    breakdown = {
        "quality": round(quality, 2),
        "risk": round(risk, 2),
        "robustness": round(robustness, 2),
        "cost_sensitivity": round(cost, 2),
        "anti_overtrading": round(overtrade, 2),
    }
    total = round(mean(breakdown.values()), 2)
    return breakdown, total


def run_backtest(candles, strategy_name: str, strategy_params: dict, config: BacktestConfig) -> BacktestResult:
    strategy_cls = get_strategy_class(strategy_name)
    strategy = strategy_cls(**strategy_params)

    cash = config.initial_cash
    pos = Position(config.symbol)
    trades: list[Trade] = []
    equity_curve: list[tuple[datetime, float]] = []
    drawdown_curve: list[tuple[datetime, float]] = []
    peak_eq = cash
    orders_per_day = defaultdict(int)
    last_trade_idx: int | None = None

    for i in range(len(candles) - 1):
        c = candles[i]
        nxt = candles[i + 1]
        signal = strategy.signal(candles, i)
        day_key = c.ts.date().isoformat()

        mark = cash + pos.qty * c.close
        peak_eq = max(peak_eq, mark)
        dd = (mark - peak_eq) / peak_eq if peak_eq else 0.0
        equity_curve.append((c.ts, mark))
        drawdown_curve.append((c.ts, dd))

        if abs(dd) >= config.risk.max_drawdown_pct:
            if pos.qty > 0:
                px = nxt.open * (1 - config.slippage_pct)
                fee = pos.qty * px * config.fee_rate
                cash += pos.qty * px - fee
                trades.append(Trade(nxt.ts, config.symbol, "SELL", pos.qty, px, fee, "max_drawdown_stop"))
                pos.qty = 0
            break

        if orders_per_day[day_key] >= config.risk.max_orders_per_day:
            continue

        if signal > 0:
            position_value = pos.qty * c.close
            if position_value / max(mark, 1e-9) >= config.risk.max_position_pct:
                continue
            if last_trade_idx is not None and (i - last_trade_idx) < config.risk.cooldown_bars:
                continue
            buy_cash = cash * 0.2
            if strategy_name == "dca":
                buy_cash = min(cash, float(strategy_params.get("buy_amount_usdt", 100)))
            if buy_cash <= 0:
                continue
            px = nxt.open * (1 + config.slippage_pct)
            qty = buy_cash / px
            fee = qty * px * config.fee_rate
            cash -= qty * px + fee
            pos.qty += qty
            pos.avg_price = px if pos.avg_price == 0 else (pos.avg_price + px) / 2
            pos.last_trade_ts = c.ts
            last_trade_idx = i
            orders_per_day[day_key] += 1
            trades.append(Trade(nxt.ts, config.symbol, "BUY", qty, px, fee, f"signal:{signal}"))

        elif signal < 0 and pos.qty > 0:
            px = nxt.open * (1 - config.slippage_pct)
            fee = pos.qty * px * config.fee_rate
            cash += pos.qty * px - fee
            trades.append(Trade(nxt.ts, config.symbol, "SELL", pos.qty, px, fee, f"signal:{signal}"))
            pos.qty = 0
            pos.last_trade_ts = c.ts
            last_trade_idx = i
            orders_per_day[day_key] += 1

    if candles:
        end_equity = cash + pos.qty * candles[-1].close
        equity_curve.append((candles[-1].ts, end_equity))
    eq_values = [v for _, v in equity_curve] or [config.initial_cash]
    rets = [eq_values[i] / eq_values[i - 1] - 1 for i in range(1, len(eq_values)) if eq_values[i - 1] > 0]
    avg_ret = mean(rets) if rets else 0.0
    vol = (sum((r - avg_ret) ** 2 for r in rets) / max(1, len(rets))) ** 0.5
    sharpe = (avg_ret / vol * sqrt(252)) if vol > 0 else 0.0
    pnl = eq_values[-1] - config.initial_cash
    win = sum(1 for t in trades if t.side == "SELL" and t.reason.startswith("signal"))
    fee_paid = sum(t.fee for t in trades)

    metrics = {
        "total_return_pct": (eq_values[-1] / config.initial_cash - 1) * 100,
        "pnl": pnl,
        "max_drawdown_pct": _max_drawdown(eq_values) * 100,
        "sharpe": sharpe,
        "trades": float(len(trades)),
        "win_rate": win / max(1, len([t for t in trades if t.side == "SELL"])),
        "fee_paid": fee_paid,
        "fee_ratio": fee_paid / max(1e-9, abs(pnl) + 1),
    }
    metrics.update(extended_metrics(eq_values))
    metrics.update({k: float(v) for k, v in evaluate_targets(metrics).items()})
    breakdown, total = _stability_score(metrics, len(trades))
    strategy_profile = strategy_cls.profile()
    metadata = RunMetadata(
        strategy_name=strategy_name,
        strategy_version=strategy.version,
        strategy_spec_hash=stable_hash(strategy_profile),
        strategy_source_hash=strategy_cls.source_hash(),
        param_hash=stable_hash(strategy_params),
        data_hash=stable_hash([(c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume) for c in candles]),
        python_version=python_fingerprint(),
        created_at=now_utc_iso(),
    )
    return BacktestResult(
        config,
        metadata,
        equity_curve,
        drawdown_curve,
        trades,
        metrics,
        breakdown,
        total,
        extra={"strategy_profile": strategy_profile},
    )


def _run_job(job):
    candles, strategy_name, params, config_dict, strategy_repo_paths = job
    if strategy_repo_paths:
        load_strategy_repos(strategy_repo_paths)
    config = BacktestConfig(**config_dict)
    return run_backtest(candles, strategy_name, params, config)


def run_parallel_matrix(
    candles_by_symbol_tf: dict,
    strategy_grid: list[tuple[str, dict]],
    config_template: dict,
    max_workers: int = 4,
    strategy_repo_paths: list[str] | None = None,
):
    jobs = []
    for (symbol, tf), candles in candles_by_symbol_tf.items():
        for strategy_name, params in strategy_grid:
            cfg = dict(config_template)
            cfg["symbol"] = symbol
            cfg["timeframe"] = tf
            jobs.append((candles, strategy_name, params, cfg, strategy_repo_paths or []))
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_run_job, jobs))
    return results


def result_to_dict(res: BacktestResult) -> dict:
    return {
        "config": asdict(res.config),
        "metadata": asdict(res.metadata),
        "metrics": res.metrics,
        "score": {"total": res.score_total, "breakdown": res.score_breakdown},
        "trades": [asdict(t) for t in res.trades],
        "equity_curve": [(t.isoformat(), v) for t, v in res.equity_curve],
        "drawdown_curve": [(t.isoformat(), v) for t, v in res.drawdown_curve],
        "extra": res.extra,
    }
