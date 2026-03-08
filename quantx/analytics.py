from __future__ import annotations

import random
from math import sqrt
from statistics import mean


def extended_metrics(equity: list[float], rf: float = 0.0) -> dict[str, float]:
    if len(equity) < 2:
        return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0, "calmar": 0.0}
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if not rets:
        return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0, "calmar": 0.0}
    avg = mean(rets)
    stdev = (sum((r - avg) ** 2 for r in rets) / max(1, len(rets))) ** 0.5
    downside = [min(0.0, r - rf) for r in rets]
    downside_dev = (sum(d * d for d in downside) / max(1, len(downside))) ** 0.5
    sharpe = (avg - rf) / stdev * sqrt(252) if stdev > 0 else 0.0
    sortino = (avg - rf) / downside_dev * sqrt(252) if downside_dev > 0 else 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    total_ret = equity[-1] / equity[0] - 1 if equity[0] > 0 else 0.0
    years = max(1 / 252, len(rets) / 252)
    cagr = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": abs(mdd) * 100,
        "calmar": calmar,
    }


def evaluate_targets(metrics: dict[str, float]) -> dict[str, bool]:
    return {
        "sharpe_gt_1_5": metrics.get("sharpe", 0.0) > 1.5,
        "maxdd_lt_20pct": metrics.get("max_drawdown_pct", 100.0) < 20.0,
    }


def in_out_sample_split(candles: list, ratio: float = 0.7) -> tuple[list, list]:
    cut = max(1, int(len(candles) * ratio))
    return candles[:cut], candles[cut:]


def monte_carlo_equity(equity: list[float], n_sims: int = 200, seed: int = 7) -> dict:
    if len(equity) < 3:
        return {"n_sims": n_sims, "p5": 0.0, "p50": 0.0, "p95": 0.0}
    random.seed(seed)
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    finals = []
    for _ in range(n_sims):
        v = equity[0]
        for _ in range(len(rets)):
            v *= 1 + random.choice(rets)
        finals.append(v)
    finals.sort()
    def q(p: float):
        return finals[min(len(finals) - 1, max(0, int((len(finals) - 1) * p)))]
    return {"n_sims": n_sims, "p5": q(0.05), "p50": q(0.5), "p95": q(0.95)}
