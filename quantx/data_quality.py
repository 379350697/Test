"""Data-quality checks for OHLCV series (P1)."""

from __future__ import annotations

from typing import Any


REQUIRED_COLS = ("ts", "open", "high", "low", "close", "volume")


def check_ohlcv_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate OHLCV integrity and return issue summary."""

    issues: list[dict[str, Any]] = []
    prev_ts: str | None = None

    for i, row in enumerate(rows):
        for col in REQUIRED_COLS:
            if col not in row:
                issues.append({"row": i, "type": "missing_column", "column": col})
                continue

        try:
            o = float(row.get("open", 0.0))
            h = float(row.get("high", 0.0))
            low_px = float(row.get("low", 0.0))
            c = float(row.get("close", 0.0))
            v = float(row.get("volume", 0.0))
        except Exception:
            issues.append({"row": i, "type": "non_numeric"})
            continue

        if min(o, h, low_px, c) <= 0:
            issues.append({"row": i, "type": "non_positive_price"})
        if h < max(o, c) or low_px > min(o, c):
            issues.append({"row": i, "type": "ohlc_inconsistent"})
        if v < 0:
            issues.append({"row": i, "type": "negative_volume"})

        ts = str(row.get("ts", ""))
        if prev_ts is not None and ts <= prev_ts:
            issues.append({"row": i, "type": "non_monotonic_ts"})
        prev_ts = ts

    return {"ok": len(issues) == 0, "rows": len(rows), "issues": issues}
