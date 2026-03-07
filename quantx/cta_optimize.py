from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from pathlib import Path
from statistics import mean
from typing import Iterable


@dataclass(slots=True)
class CtaOptimizationResult:
    """Container for CTA portfolio optimization outputs.

    Attributes:
        assets: Ordered asset symbols used for optimization.
        cluster_labels: Cluster id for each asset (same order as ``assets``).
        intra_cluster_weights: Risk parity weights inside each cluster.
        cluster_weights: Risk parity weights among clusters.
        final_weights: Final portfolio weights per asset.
    """

    assets: list[str]
    cluster_labels: list[int]
    intra_cluster_weights: dict[int, dict[str, float]]
    cluster_weights: dict[int, float]
    final_weights: dict[str, float]


def _parse_ts(value: str) -> datetime:
    """Parse timestamp from common date formats.

    Args:
        value: Datetime string in ISO-8601 or common date format.

    Returns:
        Parsed datetime.

    Raises:
        ValueError: If the timestamp format is unsupported.
    """

    candidate = value.strip()
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported datetime format: {value}")


def read_returns_csv(
    path: str,
    date_col: str = "ts",
    asset_col: str = "symbol",
    return_col: str = "ret",
) -> list[tuple[datetime, str, float]]:
    """Read returns CSV and convert date column.

    Step 1 in pipeline: load rows and normalize datetime format.

    Args:
        path: CSV file path.
        date_col: Datetime column name.
        asset_col: Asset/symbol column name.
        return_col: Return column name.

    Returns:
        Flat records as ``(timestamp, asset, return)``.

    Raises:
        FileNotFoundError: If CSV does not exist.
        KeyError: If required columns are missing.
        ValueError: If data cannot be parsed.
    """

    fp = Path(path)
    if not fp.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    rows: list[tuple[datetime, str, float]] = []
    with fp.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        required = {date_col, asset_col, return_col}
        missing = required - set(reader.fieldnames)
        if missing:
            raise KeyError(f"Missing columns: {sorted(missing)}")

        for row in reader:
            ts = _parse_ts(row[date_col])
            symbol = row[asset_col].strip()
            ret = float(row[return_col])
            rows.append((ts, symbol, ret))
    return rows


def dedupe_and_sort_returns(rows: Iterable[tuple[datetime, str, float]]) -> list[tuple[datetime, str, float]]:
    """Deduplicate rows and sort by (timestamp, asset).

    Step 2 in pipeline: keep the latest duplicate value for each (ts, symbol).

    Args:
        rows: Input records.

    Returns:
        Sorted records with duplicates removed.
    """

    deduped: dict[tuple[datetime, str], float] = {}
    for ts, symbol, ret in rows:
        deduped[(ts, symbol)] = ret

    ordered = sorted((ts, symbol, ret) for (ts, symbol), ret in deduped.items())
    return ordered


def build_returns_matrix(rows: Iterable[tuple[datetime, str, float]]) -> tuple[list[datetime], list[str], list[list[float]]]:
    """Build aligned returns matrix from normalized records.

    Step 3 in pipeline: return aligned matrix for optimization.

    Returns matrix shape is ``T x N`` where rows are timestamps and columns are assets.
    Only timestamps shared by all assets are retained.

    Args:
        rows: Clean records, usually from ``dedupe_and_sort_returns``.

    Returns:
        ``(timestamps, assets, matrix)``.

    Raises:
        ValueError: If data is insufficient for alignment.
    """

    per_asset: dict[str, dict[datetime, float]] = {}
    for ts, symbol, ret in rows:
        per_asset.setdefault(symbol, {})[ts] = ret

    assets = sorted(per_asset)
    if len(assets) < 2:
        raise ValueError("Need at least 2 assets for portfolio optimization")

    common_ts = set(per_asset[assets[0]].keys())
    for symbol in assets[1:]:
        common_ts &= set(per_asset[symbol].keys())

    timestamps = sorted(common_ts)
    if len(timestamps) < 3:
        raise ValueError("Need at least 3 aligned timestamps")

    matrix = [[per_asset[symbol][ts] for symbol in assets] for ts in timestamps]
    return timestamps, assets, matrix


def covariance_matrix(matrix: list[list[float]]) -> list[list[float]]:
    """Compute covariance matrix from returns matrix (T x N)."""

    t = len(matrix)
    n = len(matrix[0]) if matrix else 0
    if t < 2 or n < 1:
        return []

    cols = [[matrix[i][j] for i in range(t)] for j in range(n)]
    means = [mean(col) for col in cols]

    cov = [[0.0 for _ in range(n)] for _ in range(n)]
    denom = max(1, t - 1)
    for i in range(n):
        for j in range(n):
            acc = 0.0
            for k in range(t):
                acc += (cols[i][k] - means[i]) * (cols[j][k] - means[j])
            cov[i][j] = acc / denom
    return cov


def correlation_matrix(cov: list[list[float]]) -> list[list[float]]:
    """Convert covariance matrix to correlation matrix."""

    n = len(cov)
    if n == 0:
        return []

    std = [sqrt(max(cov[i][i], 0.0)) for i in range(n)]
    corr = [[0.0 for _ in range(n)] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            denom = std[i] * std[j]
            if denom <= 1e-12:
                corr[i][j] = 1.0 if i == j else 0.0
            else:
                corr[i][j] = max(-1.0, min(1.0, cov[i][j] / denom))
    return corr


def cluster_by_correlation(corr: list[list[float]], threshold: float = 0.6) -> list[int]:
    """Cluster assets by correlation threshold using connected components.

    Assets with pairwise correlation >= threshold are treated as connected.

    Args:
        corr: Correlation matrix (N x N).
        threshold: Link threshold in [-1, 1].

    Returns:
        Cluster label per asset index.
    """

    n = len(corr)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if corr[i][j] >= threshold:
                union(i, j)

    roots = [find(i) for i in range(n)]
    root_to_label: dict[int, int] = {}
    labels: list[int] = []
    next_label = 1
    for r in roots:
        if r not in root_to_label:
            root_to_label[r] = next_label
            next_label += 1
        labels.append(root_to_label[r])
    return labels


def _mat_vec_mul(mat: list[list[float]], vec: list[float]) -> list[float]:
    return [sum(row[j] * vec[j] for j in range(len(vec))) for row in mat]


def risk_parity_weights(
    cov: list[list[float]],
    max_iter: int = 300,
    tol: float = 1e-6,
    floor: float = 1e-8,
) -> list[float]:
    """Compute risk parity weights with iterative scaling.

    This method avoids third-party dependencies and converges well for small/medium
    covariance matrices commonly used in CTA allocation.

    Args:
        cov: Covariance matrix (N x N).
        max_iter: Maximum optimization iterations.
        tol: Stop threshold for max relative risk-contribution gap.
        floor: Numerical floor to keep weights positive.

    Returns:
        Normalized non-negative weights of length N.
    """

    n = len(cov)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    w = [1.0 / n for _ in range(n)]

    for _ in range(max_iter):
        mrc = _mat_vec_mul(cov, w)
        port_var = sum(w[i] * mrc[i] for i in range(n))
        if port_var <= floor:
            break
        port_vol = sqrt(port_var)

        rc = [max(floor, w[i] * mrc[i] / port_vol) for i in range(n)]
        target_rc = sum(rc) / n
        max_gap = max(abs(r - target_rc) / max(target_rc, floor) for r in rc)
        if max_gap < tol:
            break

        # Multiplicative update: lower weight if RC is too high, raise if too low.
        adjusted = [max(floor, w[i] * target_rc / rc[i]) for i in range(n)]
        total = sum(adjusted)
        w = [x / total for x in adjusted]

    total = sum(w)
    return [x / total for x in w]


def optimize_cta_risk_parity_with_clustering(
    assets: list[str],
    returns_matrix: list[list[float]],
    corr_threshold: float = 0.6,
) -> CtaOptimizationResult:
    """Optimize CTA portfolio using Correlation Clustering + Risk Parity.

    Workflow:
        1) compute covariance/correlation
        2) cluster assets by correlation connectivity
        3) risk parity within each cluster
        4) risk parity among cluster synthetic returns
        5) combine into final asset weights

    Args:
        assets: Asset symbols aligned with matrix columns.
        returns_matrix: Returns matrix (T x N).
        corr_threshold: Correlation threshold for clustering.

    Returns:
        CtaOptimizationResult with detailed intermediate/final weights.

    Raises:
        ValueError: If inputs are malformed.
    """

    if not assets or not returns_matrix:
        raise ValueError("assets and returns_matrix must be non-empty")
    if len(assets) != len(returns_matrix[0]):
        raise ValueError("assets count must match returns matrix columns")

    cov = covariance_matrix(returns_matrix)
    corr = correlation_matrix(cov)
    labels = cluster_by_correlation(corr, threshold=corr_threshold)

    cluster_to_idx: dict[int, list[int]] = {}
    for idx, cluster_id in enumerate(labels):
        cluster_to_idx.setdefault(cluster_id, []).append(idx)

    intra_weights: dict[int, dict[str, float]] = {}
    cluster_series: list[list[float]] = []
    ordered_clusters = sorted(cluster_to_idx)

    for cluster_id in ordered_clusters:
        idxs = cluster_to_idx[cluster_id]
        sub_cov = [[cov[i][j] for j in idxs] for i in idxs]
        sub_weights = risk_parity_weights(sub_cov)
        intra_weights[cluster_id] = {assets[idxs[k]]: sub_weights[k] for k in range(len(idxs))}

        # Compute cluster synthetic returns (weighted sum per timestamp).
        synthetic = [sum(row[idxs[k]] * sub_weights[k] for k in range(len(idxs))) for row in returns_matrix]
        cluster_series.append(synthetic)

    cluster_returns_matrix = [[series[t] for series in cluster_series] for t in range(len(cluster_series[0]))]
    cluster_cov = covariance_matrix(cluster_returns_matrix)
    cluster_weight_list = risk_parity_weights(cluster_cov)
    cluster_weights = {ordered_clusters[i]: cluster_weight_list[i] for i in range(len(ordered_clusters))}

    final_weights = {asset: 0.0 for asset in assets}
    for cluster_id in ordered_clusters:
        cw = cluster_weights[cluster_id]
        for asset, iw in intra_weights[cluster_id].items():
            final_weights[asset] = cw * iw

    norm = sum(final_weights.values())
    if norm <= 1e-12:
        raise ValueError("final weights normalization failed")
    final_weights = {k: v / norm for k, v in final_weights.items()}

    return CtaOptimizationResult(
        assets=assets,
        cluster_labels=labels,
        intra_cluster_weights=intra_weights,
        cluster_weights=cluster_weights,
        final_weights=final_weights,
    )


def optimize_cta_from_csv(
    csv_path: str,
    date_col: str = "ts",
    asset_col: str = "symbol",
    return_col: str = "ret",
    corr_threshold: float = 0.6,
) -> CtaOptimizationResult:
    """Convenience wrapper: read CSV -> clean data -> optimize CTA weights."""

    raw_rows = read_returns_csv(csv_path, date_col=date_col, asset_col=asset_col, return_col=return_col)
    clean_rows = dedupe_and_sort_returns(raw_rows)
    _, assets, matrix = build_returns_matrix(clean_rows)
    return optimize_cta_risk_parity_with_clustering(assets, matrix, corr_threshold=corr_threshold)
