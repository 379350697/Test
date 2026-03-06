from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _binance_interval(tf: str) -> str:
    mapping = {
        "1s": "1s",
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }
    if tf not in mapping:
        raise ValueError(f"unsupported timeframe: {tf}")
    return mapping[tf]


def fetch_binance_klines(symbol: str, timeframe: str = "1m", limit: int = 1000) -> list[dict]:
    interval = _binance_interval(timeframe)
    q = urllib.parse.urlencode({"symbol": symbol.upper(), "interval": interval, "limit": max(1, min(limit, 1500))})
    url = f"https://api.binance.com/api/v3/klines?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "quantx/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rows = []
    for item in data:
        open_ts = datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc).isoformat()
        rows.append(
            {
                "ts": open_ts,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            }
        )
    return rows


def write_ohlcv_csv(rows: list[dict], path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
    return str(p)
