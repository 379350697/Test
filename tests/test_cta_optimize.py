from __future__ import annotations

import csv
from pathlib import Path

from quantx.cta_optimize import (
    build_returns_matrix,
    dedupe_and_sort_returns,
    optimize_cta_from_csv,
    optimize_cta_risk_parity_with_clustering,
    read_returns_csv,
    risk_parity_weights,
)


def _write_returns_csv(path: Path) -> Path:
    rows = [
        ("2024-01-01 00:00:00", "BTC", 0.0100),
        ("2024-01-01 00:00:00", "ETH", 0.0105),
        ("2024-01-01 00:00:00", "XRP", -0.0020),
        ("2024-01-02 00:00:00", "BTC", -0.0030),
        ("2024-01-02 00:00:00", "ETH", -0.0025),
        ("2024-01-02 00:00:00", "XRP", 0.0040),
        ("2024-01-03 00:00:00", "BTC", 0.0080),
        ("2024-01-03 00:00:00", "ETH", 0.0085),
        ("2024-01-03 00:00:00", "XRP", -0.0010),
        # duplicate row to verify dedupe (latest wins)
        ("2024-01-03 00:00:00", "XRP", -0.0015),
        ("2024-01-04 00:00:00", "BTC", 0.0020),
        ("2024-01-04 00:00:00", "ETH", 0.0021),
        ("2024-01-04 00:00:00", "XRP", 0.0060),
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "symbol", "ret"])
        writer.writerows(rows)
    return path


def test_csv_pipeline_read_dedupe_sort_and_matrix(tmp_path):
    fp = _write_returns_csv(tmp_path / "returns.csv")

    raw = read_returns_csv(str(fp))
    assert len(raw) == 13

    cleaned = dedupe_and_sort_returns(raw)
    assert len(cleaned) == 12

    timestamps, assets, matrix = build_returns_matrix(cleaned)
    assert assets == ["BTC", "ETH", "XRP"]
    assert len(timestamps) == 4
    assert len(matrix) == 4
    assert len(matrix[0]) == 3


def test_risk_parity_weights_normalization():
    cov = [
        [0.0400, 0.0100, 0.0080],
        [0.0100, 0.0225, 0.0060],
        [0.0080, 0.0060, 0.0100],
    ]
    w = risk_parity_weights(cov)
    assert len(w) == 3
    assert abs(sum(w) - 1.0) < 1e-6
    assert all(x > 0 for x in w)


def test_cta_optimize_from_csv(tmp_path):
    fp = _write_returns_csv(tmp_path / "returns.csv")
    result = optimize_cta_from_csv(str(fp), corr_threshold=0.7)

    assert set(result.assets) == {"BTC", "ETH", "XRP"}
    assert len(result.cluster_labels) == len(result.assets)
    assert abs(sum(result.final_weights.values()) - 1.0) < 1e-6
    assert all(v >= 0 for v in result.final_weights.values())


def test_cta_optimize_from_matrix_directly():
    assets = ["A", "B", "C", "D"]
    matrix = [
        [0.01, 0.011, -0.002, -0.003],
        [-0.004, -0.0035, 0.006, 0.0055],
        [0.008, 0.0075, -0.001, -0.0015],
        [0.002, 0.0018, 0.004, 0.0038],
        [-0.003, -0.0027, 0.002, 0.0022],
    ]
    result = optimize_cta_risk_parity_with_clustering(assets, matrix, corr_threshold=0.65)

    assert abs(sum(result.cluster_weights.values()) - 1.0) < 1e-6
    assert abs(sum(result.final_weights.values()) - 1.0) < 1e-6
