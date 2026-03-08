from __future__ import annotations

import itertools
import random
from dataclasses import asdict

from .backtest import run_backtest
from .models import BacktestConfig


def grid_search(candles, strategy_name: str, param_grid: dict[str, list], base_config: BacktestConfig):
    keys = list(param_grid.keys())
    combos = [dict(zip(keys, values)) for values in itertools.product(*(param_grid[k] for k in keys))]
    out = []
    for params in combos:
        res = run_backtest(candles, strategy_name, params, base_config)
        out.append({"params": params, "score": res.score_total, "return_pct": res.metrics["total_return_pct"]})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def random_scan(candles, strategy_name: str, param_space: dict[str, tuple], n_samples: int, base_config: BacktestConfig, seed: int = 42):
    random.seed(seed)
    out = []
    for _ in range(n_samples):
        params = {}
        for k, bounds in param_space.items():
            lo, hi, tp = bounds
            if tp == "int":
                params[k] = random.randint(int(lo), int(hi))
            elif tp == "float":
                params[k] = random.uniform(float(lo), float(hi))
            else:
                params[k] = random.choice(list(bounds[0]))
        res = run_backtest(candles, strategy_name, params, base_config)
        out.append({"params": params, "score": res.score_total, "return_pct": res.metrics["total_return_pct"]})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def walk_forward(candles, strategy_name: str, params: dict, base_config: BacktestConfig, splits: int = 3, train_ratio: float = 0.7):
    n = len(candles)
    step = n // splits
    windows = []
    for i in range(splits):
        start = i * step
        end = min(n, (i + 1) * step)
        seg = candles[start:end]
        if len(seg) < 30:
            continue
        cut = int(len(seg) * train_ratio)
        train = seg[:cut]
        test = seg[cut:]
        train_res = run_backtest(train, strategy_name, params, base_config)
        test_res = run_backtest(test, strategy_name, params, base_config)
        windows.append(
            {
                "window": i,
                "train": {"score": train_res.score_total, "metrics": train_res.metrics},
                "test": {"score": test_res.score_total, "metrics": test_res.metrics},
                "config": asdict(base_config),
            }
        )
    return windows
