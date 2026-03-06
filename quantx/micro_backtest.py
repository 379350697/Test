from __future__ import annotations

from math import sqrt
from statistics import mean

from .models import BacktestConfig
from .repro import now_utc_iso, python_fingerprint, stable_hash


def _basic_metrics(equity: list[float], initial_cash: float, fee_paid: float) -> dict[str, float]:
    if not equity:
        equity = [initial_cash]
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    avg_ret = mean(rets) if rets else 0.0
    vol = (sum((r - avg_ret) ** 2 for r in rets) / max(1, len(rets))) ** 0.5
    sharpe = (avg_ret / vol * sqrt(252)) if vol > 0 else 0.0
    peak, mdd = equity[0], 0.0
    for x in equity:
        peak = max(peak, x)
        if peak > 0:
            mdd = min(mdd, (x - peak) / peak)
    pnl = equity[-1] - initial_cash
    return {
        "total_return_pct": (equity[-1] / initial_cash - 1) * 100,
        "pnl": pnl,
        "max_drawdown_pct": abs(mdd) * 100,
        "sharpe": sharpe,
        "fee_paid": fee_paid,
        "fee_ratio": fee_paid / max(1e-9, abs(pnl) + 1),
    }


def run_tick_backtest(ticks: list[dict], config: BacktestConfig, threshold_bps: float = 4.0) -> dict:
    cash = config.initial_cash
    qty = 0.0
    fee_paid = 0.0
    trades = []
    equity_curve = []
    window = 30
    for i in range(len(ticks)):
        px = ticks[i]["price"]
        if i < window:
            equity_curve.append((ticks[i]["ts"].isoformat(), cash + qty * px))
            continue
        ref = ticks[i - window]["price"]
        momentum = (px - ref) / ref * 10000
        if momentum > threshold_bps and qty == 0:
            fill = px * (1 + config.slippage_pct)
            buy_cash = cash * 0.3
            q = buy_cash / fill
            fee = q * fill * config.fee_rate
            cash -= q * fill + fee
            qty += q
            fee_paid += fee
            trades.append({"ts": ticks[i]["ts"].isoformat(), "side": "BUY", "qty": q, "price": fill, "reason": "tick_momentum"})
        elif momentum < -threshold_bps and qty > 0:
            fill = px * (1 - config.slippage_pct)
            fee = qty * fill * config.fee_rate
            cash += qty * fill - fee
            fee_paid += fee
            trades.append({"ts": ticks[i]["ts"].isoformat(), "side": "SELL", "qty": qty, "price": fill, "reason": "tick_momentum"})
            qty = 0
        equity_curve.append((ticks[i]["ts"].isoformat(), cash + qty * px))

    if ticks:
        equity_curve.append((ticks[-1]["ts"].isoformat(), cash + qty * ticks[-1]["price"]))
    metrics = _basic_metrics([v for _, v in equity_curve], config.initial_cash, fee_paid)
    return {
        "mode": "tick",
        "config": {"symbol": config.symbol, "timeframe": "tick", "initial_cash": config.initial_cash},
        "metadata": {
            "strategy_name": "tick_momentum",
            "strategy_version": "1.0.0",
            "strategy_spec_hash": stable_hash({"threshold_bps": threshold_bps, "window": window}),
            "strategy_source_hash": stable_hash("tick_momentum_builtin"),
            "param_hash": stable_hash({"threshold_bps": threshold_bps}),
            "data_hash": stable_hash([(t["ts"].isoformat(), t["price"], t["size"], t["side"]) for t in ticks]),
            "python_version": python_fingerprint(),
            "created_at": now_utc_iso(),
        },
        "metrics": metrics,
        "trades": trades,
        "equity_curve": equity_curve,
        "drawdown_curve": [],
        "score": {"total": max(0.0, min(100.0, 50 + metrics["sharpe"] * 20)), "breakdown": {}},
        "extra": {"threshold_bps": threshold_bps, "data_depth": "tick-level"},
    }


def run_orderbook_replay(snapshots: list[dict], config: BacktestConfig, shock_coeff: float = 0.15) -> dict:
    cash = config.initial_cash
    qty = 0.0
    fee_paid = 0.0
    trades = []
    equity_curve = []

    def impact(levels: list[float], sizes: list[float], notional: float) -> float:
        available = sum(sizes) if sizes else 1.0
        pressure = min(2.0, notional / max(1e-9, available * levels[0]))
        return pressure * shock_coeff / 100

    for s in snapshots:
        bid = s["bids"][0]
        ask = s["asks"][0]
        mid = s["mid"]
        spread = max(1e-9, ask - bid) / mid
        signal = 1 if spread < 0.0012 else -1

        if signal > 0 and qty == 0:
            buy_cash = cash * 0.25
            imp = impact(s["asks"], s.get("ask_sizes", []), buy_cash)
            fill = ask * (1 + config.slippage_pct + imp)
            q = buy_cash / fill
            fee = q * fill * config.fee_rate
            cash -= q * fill + fee
            qty += q
            fee_paid += fee
            trades.append({"ts": s["ts"].isoformat(), "side": "BUY", "qty": q, "price": fill, "reason": "orderbook_spread"})
        elif signal < 0 and qty > 0:
            imp = impact(s["bids"], s.get("bid_sizes", []), qty * bid)
            fill = bid * (1 - config.slippage_pct - imp)
            fee = qty * fill * config.fee_rate
            cash += qty * fill - fee
            fee_paid += fee
            trades.append({"ts": s["ts"].isoformat(), "side": "SELL", "qty": qty, "price": fill, "reason": "orderbook_spread"})
            qty = 0

        equity_curve.append((s["ts"].isoformat(), cash + qty * mid))

    if snapshots:
        equity_curve.append((snapshots[-1]["ts"].isoformat(), cash + qty * snapshots[-1]["mid"]))

    metrics = _basic_metrics([v for _, v in equity_curve], config.initial_cash, fee_paid)
    return {
        "mode": "orderbook",
        "config": {"symbol": config.symbol, "timeframe": "orderbook", "initial_cash": config.initial_cash},
        "metadata": {
            "strategy_name": "orderbook_spread_replay",
            "strategy_version": "1.0.0",
            "strategy_spec_hash": stable_hash({"shock_coeff": shock_coeff}),
            "strategy_source_hash": stable_hash("orderbook_spread_replay_builtin"),
            "param_hash": stable_hash({"shock_coeff": shock_coeff}),
            "data_hash": stable_hash([(x["ts"].isoformat(), x["mid"], x["bids"], x["asks"]) for x in snapshots]),
            "python_version": python_fingerprint(),
            "created_at": now_utc_iso(),
        },
        "metrics": metrics,
        "trades": trades,
        "equity_curve": equity_curve,
        "drawdown_curve": [],
        "score": {"total": max(0.0, min(100.0, 50 + metrics["sharpe"] * 20)), "breakdown": {}},
        "extra": {"shock_coeff": shock_coeff, "data_depth": "L2+ orderbook snapshots"},
    }
