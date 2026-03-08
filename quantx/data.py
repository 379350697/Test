from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from .models import Candle


def load_csv(path: str) -> list[Candle]:
    rows: list[Candle] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                Candle(
                    ts=datetime.fromisoformat(row["ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                )
            )
    return rows


def load_tick_csv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "ts": datetime.fromisoformat(row["ts"]),
                    "price": float(row["price"]),
                    "size": float(row.get("size", 0.0)),
                    "side": row.get("side", "trade"),
                }
            )
    return rows


def load_orderbook_csv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bids = [float(x) for x in row["bids"].split("|") if x]
            asks = [float(x) for x in row["asks"].split("|") if x]
            rows.append(
                {
                    "ts": datetime.fromisoformat(row["ts"]),
                    "mid": float(row["mid"]),
                    "bids": bids,
                    "asks": asks,
                    "bid_sizes": [float(x) for x in row.get("bid_sizes", "").split("|") if x],
                    "ask_sizes": [float(x) for x in row.get("ask_sizes", "").split("|") if x],
                }
            )
    return rows


def inspect_data(candles: list[Candle]) -> dict:
    gaps = 0
    for i in range(1, len(candles)):
        if candles[i].ts <= candles[i - 1].ts:
            gaps += 1
    closes = [c.close for c in candles]
    outliers = sum(1 for c in closes if c <= 0)
    return {
        "bars": len(candles),
        "start": candles[0].ts.isoformat() if candles else None,
        "end": candles[-1].ts.isoformat() if candles else None,
        "ordering_issues": gaps,
        "invalid_close_count": outliers,
    }


def generate_demo_data(path: str, bars: int = 1000, seed: int = 7) -> str:
    random.seed(seed)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime(2024, 1, 1)
    price = 100.0
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for _ in range(bars):
            drift = random.uniform(-0.02, 0.02)
            o = price
            c = max(1.0, price * (1 + drift))
            h = max(o, c) * (1 + random.uniform(0, 0.005))
            low_price = min(o, c) * (1 - random.uniform(0, 0.005))
            v = random.uniform(100, 500)
            writer.writerow([ts.isoformat(), f"{o:.4f}", f"{h:.4f}", f"{low_price:.4f}", f"{c:.4f}", f"{v:.4f}"])
            ts += timedelta(hours=1)
            price = c
    return str(p)


def generate_tick_demo_data(path: str, ticks: int = 5000, seed: int = 17) -> str:
    random.seed(seed)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime(2024, 1, 1)
    price = 100.0
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "price", "size", "side"])
        for _ in range(ticks):
            price = max(1.0, price * (1 + random.uniform(-0.0015, 0.0015)))
            size = random.uniform(0.01, 2)
            side = random.choice(["buy", "sell"])
            w.writerow([ts.isoformat(), f"{price:.5f}", f"{size:.5f}", side])
            ts += timedelta(seconds=1)
    return str(p)


def generate_orderbook_demo_data(path: str, rows: int = 1000, levels: int = 5, seed: int = 27) -> str:
    random.seed(seed)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime(2024, 1, 1)
    mid = 100.0
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "mid", "bids", "asks", "bid_sizes", "ask_sizes"])
        for _ in range(rows):
            mid = max(1.0, mid * (1 + random.uniform(-0.001, 0.001)))
            bids = [mid * (1 - 0.0005 * (i + 1)) for i in range(levels)]
            asks = [mid * (1 + 0.0005 * (i + 1)) for i in range(levels)]
            bs = [random.uniform(0.5, 10) for _ in range(levels)]
            a_s = [random.uniform(0.5, 10) for _ in range(levels)]
            w.writerow(
                [
                    ts.isoformat(),
                    f"{mid:.6f}",
                    "|".join(f"{x:.6f}" for x in bids),
                    "|".join(f"{x:.6f}" for x in asks),
                    "|".join(f"{x:.5f}" for x in bs),
                    "|".join(f"{x:.5f}" for x in a_s),
                ]
            )
            ts += timedelta(seconds=1)
    return str(p)
