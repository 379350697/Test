"""CTA portfolio optimization utilities.

This module provides practical CTA portfolio construction helpers:

1. Read return CSV and clean timestamps.
2. Cluster assets by correlation (threshold graph or hierarchical clustering).
3. Compute risk-parity weights inside clusters and across clusters.
4. Optionally apply portfolio-level scaling by target volatility / max leverage.
5. Support rolling rebalancing (monthly / quarterly).

Runtime dependency policy:
- Core logic uses only Python standard library.
- Hierarchical clustering can optionally use SciPy when available.
"""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, Literal, Any


ClusterMethod = Literal["threshold", "hierarchical"]
RebalanceFreq = Literal["monthly", "quarterly"]


@dataclass(frozen=True)
class ReturnSnapshot:
    """One timestamped multi-asset return observation."""

    ts: datetime
    values: dict[str, float]


def parse_returns_csv(path: str, ts_column: str = "ts") -> list[ReturnSnapshot]:
    """Read return CSV, parse timestamp, deduplicate and sort.

    Step 1: read CSV and convert timestamp values.
    Step 2: remove duplicate timestamps (keep the latest row) and sort ascending.
    Step 3: return cleaned snapshots.

    Args:
        path: CSV path. Format: one timestamp column + multiple asset columns.
        ts_column: Timestamp column name.

    Returns:
        Cleaned snapshots sorted by timestamp ascending.

    Raises:
        ValueError: Missing timestamp column, missing asset columns, or no valid rows.
    """

    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or ts_column not in reader.fieldnames:
            raise ValueError(f"missing timestamp column: {ts_column}")

        assets = [name for name in reader.fieldnames if name != ts_column]
        if not assets:
            raise ValueError("no asset columns found")

        dedup: dict[datetime, ReturnSnapshot] = {}
        for row in reader:
            raw_ts = (row.get(ts_column) or "").strip()
            if not raw_ts:
                continue
            ts = _parse_datetime(raw_ts)

            values: dict[str, float] = {}
            for asset in assets:
                raw = (row.get(asset) or "").strip()
                if not raw:
                    continue
                try:
                    values[asset] = float(raw)
                except ValueError:
                    continue

            if values:
                dedup[ts] = ReturnSnapshot(ts=ts, values=values)

    snapshots = sorted(dedup.values(), key=lambda x: x.ts)
    if not snapshots:
        raise ValueError("no valid rows parsed from csv")
    return snapshots


def optimize_cta_portfolio(
    snapshots: list[ReturnSnapshot],
    corr_threshold: float = 0.65,
    max_iter: int = 200,
    tolerance: float = 1e-8,
    cluster_method: ClusterMethod = "threshold",
    target_vol: float | None = None,
    max_leverage: float | None = None,
) -> dict[str, float]:
    """Optimize CTA weights with clustering + two-level risk parity.

    Args:
        snapshots: Cleaned return snapshots.
        corr_threshold: Similarity gate for cluster grouping.
        max_iter: Iteration cap for risk-parity solver.
        tolerance: Convergence tolerance for risk-parity solver.
        cluster_method: "threshold" (default) or "hierarchical".
        target_vol: Optional target volatility for portfolio-level scaling.
        max_leverage: Optional leverage cap (sum of absolute weights).

    Returns:
        Final weights keyed by asset symbol.
    """

    assets, matrix = _build_dense_matrix(snapshots)
    if len(assets) == 1:
        return {assets[0]: 1.0}

    corr = _correlation_matrix(matrix)
    clusters = _cluster_asset_indexes(corr, corr_threshold, method=cluster_method)

    cov = _covariance_matrix(matrix)

    # 1) cluster-internal risk parity
    intra_weights: dict[int, dict[int, float]] = {}
    cluster_series: list[list[float]] = []
    for cluster_id, index_list in enumerate(clusters):
        sub_cov = _submatrix(cov, index_list)
        local_weights = _risk_parity_weights(sub_cov, max_iter=max_iter, tolerance=tolerance)
        intra_weights[cluster_id] = {idx: w for idx, w in zip(index_list, local_weights)}

        synthetic_series = [
            sum(row[idx] * intra_weights[cluster_id][idx] for idx in index_list)
            for row in matrix
        ]
        cluster_series.append(synthetic_series)

    # 2) cluster-level risk parity
    cluster_cov = _covariance_matrix(_transpose(cluster_series))
    cluster_weights = _risk_parity_weights(cluster_cov, max_iter=max_iter, tolerance=tolerance)

    # 3) merge
    final: defaultdict[str, float] = defaultdict(float)
    for cluster_id, local_map in intra_weights.items():
        cluster_weight = cluster_weights[cluster_id]
        for idx, local_weight in local_map.items():
            final[assets[idx]] += cluster_weight * local_weight

    merged = _normalize_weights(dict(final))
    return _apply_risk_scaling(merged, assets, cov, target_vol=target_vol, max_leverage=max_leverage)


def rolling_rebalance_cta_portfolio(
    snapshots: list[ReturnSnapshot],
    rebalance: RebalanceFreq = "monthly",
    lookback: int = 90,
    corr_threshold: float = 0.65,
    cluster_method: ClusterMethod = "threshold",
    target_vol: float | None = None,
    max_leverage: float | None = None,
) -> list[dict[str, Any]]:
    """Run rolling CTA optimization on rebalance anchors.

    For each month/quarter boundary, this function takes the trailing `lookback`
    snapshots, recomputes clustering + risk parity weights, and records one
    rebalance decision.
    """

    if rebalance not in {"monthly", "quarterly"}:
        raise ValueError("rebalance must be 'monthly' or 'quarterly'")
    if lookback < 10:
        raise ValueError("lookback must be >= 10")

    ordered = sorted(snapshots, key=lambda x: x.ts)
    anchors = _rebalance_anchor_indexes(ordered, rebalance)

    plans: list[dict[str, Any]] = []
    for idx in anchors:
        if idx < lookback:
            continue
        window = ordered[idx - lookback : idx]
        if len(window) < lookback:
            continue

        weights = optimize_cta_portfolio(
            snapshots=window,
            corr_threshold=corr_threshold,
            cluster_method=cluster_method,
            target_vol=target_vol,
            max_leverage=max_leverage,
        )
        plans.append({
            "rebalance_ts": ordered[idx].ts.isoformat(),
            "lookback": lookback,
            "weights": weights,
            "leverage": round(sum(abs(v) for v in weights.values()), 8),
        })

    return plans


def optimize_cta_portfolio_from_csv(
    path: str,
    ts_column: str = "ts",
    corr_threshold: float = 0.65,
    cluster_method: ClusterMethod = "threshold",
    target_vol: float | None = None,
    max_leverage: float | None = None,
) -> dict[str, float]:
    """Convenience wrapper: parse CSV then optimize CTA portfolio."""

    snapshots = parse_returns_csv(path=path, ts_column=ts_column)
    return optimize_cta_portfolio(
        snapshots=snapshots,
        corr_threshold=corr_threshold,
        cluster_method=cluster_method,
        target_vol=target_vol,
        max_leverage=max_leverage,
    )


def _parse_datetime(raw: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(raw)


def _build_dense_matrix(snapshots: list[ReturnSnapshot]) -> tuple[list[str], list[list[float]]]:
    all_assets = sorted({asset for snap in snapshots for asset in snap.values})
    if not all_assets:
        raise ValueError("no asset returns available")

    matrix = [[snap.values.get(asset, 0.0) for asset in all_assets] for snap in snapshots]
    return all_assets, matrix


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    if not matrix:
        return []
    return [list(col) for col in zip(*matrix)]


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _covariance_matrix(matrix: list[list[float]]) -> list[list[float]]:
    cols = _transpose(matrix)
    n = len(cols)
    if n == 0:
        return []

    means = [_mean(col) for col in cols]
    t = len(matrix)
    denom = max(t - 1, 1)
    cov = [[0.0 for _ in range(n)] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            cov[i][j] = sum((cols[i][k] - means[i]) * (cols[j][k] - means[j]) for k in range(t)) / denom
    return cov


def _correlation_matrix(matrix: list[list[float]]) -> list[list[float]]:
    cov = _covariance_matrix(matrix)
    n = len(cov)
    corr = [[0.0 for _ in range(n)] for _ in range(n)]
    vols = [math.sqrt(max(cov[i][i], 0.0)) for i in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                corr[i][j] = 1.0
                continue
            denom = vols[i] * vols[j]
            corr[i][j] = cov[i][j] / denom if denom > 1e-12 else 0.0
    return corr


def _cluster_asset_indexes(corr: list[list[float]], threshold: float, method: ClusterMethod) -> list[list[int]]:
    """Dispatch clustering method with graceful fallback."""

    if method == "threshold":
        return _threshold_clusters(corr, threshold)
    if method == "hierarchical":
        clusters = _hierarchical_clusters(corr, threshold)
        if clusters:
            return clusters
        return _threshold_clusters(corr, threshold)
    raise ValueError("cluster_method must be 'threshold' or 'hierarchical'")


def _threshold_clusters(corr: list[list[float]], threshold: float) -> list[list[int]]:
    n = len(corr)
    graph: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if corr[i][j] >= threshold:
                graph[i].add(j)
                graph[j].add(i)

    clusters: list[list[int]] = []
    visited: set[int] = set()
    for i in range(n):
        if i in visited:
            continue
        stack = [i]
        component: list[int] = []
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            stack.extend(graph[node] - visited)
        clusters.append(sorted(component))
    return clusters


def _hierarchical_clusters(corr: list[list[float]], threshold: float) -> list[list[int]] | None:
    """Try SciPy hierarchical clustering; return None when SciPy unavailable."""

    try:
        from scipy.cluster.hierarchy import fcluster, linkage  # type: ignore[import-not-found,import-untyped]
        from scipy.spatial.distance import squareform  # type: ignore[import-not-found,import-untyped]
    except Exception:
        return None

    n = len(corr)
    if n == 0:
        return []
    if n == 1:
        return [[0]]

    dist = [[0.0 for _ in range(n)] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            dist[i][j] = max(0.0, 1.0 - corr[i][j])

    condensed = squareform(dist, checks=False)
    linkage_matrix = linkage(condensed, method="average")
    labels = fcluster(linkage_matrix, t=max(0.0, 1.0 - threshold), criterion="distance")

    out: dict[int, list[int]] = defaultdict(list)
    for idx, lbl in enumerate(labels):
        out[int(lbl)].append(idx)
    return [sorted(v) for _, v in sorted(out.items(), key=lambda x: x[0])]


def _mat_vec(mat: list[list[float]], vec: list[float]) -> list[float]:
    return [sum(mij * vj for mij, vj in zip(row, vec)) for row in mat]


def _risk_parity_weights(cov: list[list[float]], max_iter: int = 200, tolerance: float = 1e-8) -> list[float]:
    n = len(cov)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    weights = [1.0 / n] * n
    eps = 1e-12

    for _ in range(max_iter):
        marginal = _mat_vec(cov, weights)
        rc = [max(weights[i] * marginal[i], eps) for i in range(n)]
        target = sum(rc) / n

        updated = [weights[i] * target / rc[i] for i in range(n)]
        updated = _normalize_weights_indexed(updated)
        diff = max(abs(updated[i] - weights[i]) for i in range(n))
        weights = updated
        if diff < tolerance:
            break

    return weights


def _portfolio_vol(weights: list[float], cov: list[list[float]]) -> float:
    quad = 0.0
    for i in range(len(weights)):
        for j in range(len(weights)):
            quad += weights[i] * cov[i][j] * weights[j]
    return math.sqrt(max(quad, 0.0))


def _apply_risk_scaling(
    weights: dict[str, float],
    assets: list[str],
    cov: list[list[float]],
    target_vol: float | None,
    max_leverage: float | None,
) -> dict[str, float]:
    """Scale optimized weights by target volatility and leverage cap."""

    scaled = dict(weights)
    vector = [scaled.get(asset, 0.0) for asset in assets]

    if target_vol is not None and target_vol > 0:
        current_vol = _portfolio_vol(vector, cov)
        if current_vol > 1e-12:
            factor = target_vol / current_vol
            scaled = {k: v * factor for k, v in scaled.items()}
            vector = [scaled.get(asset, 0.0) for asset in assets]

    if max_leverage is not None and max_leverage > 0:
        leverage = sum(abs(v) for v in vector)
        if leverage > max_leverage:
            factor = max_leverage / leverage
            scaled = {k: v * factor for k, v in scaled.items()}

    return scaled


def _rebalance_anchor_indexes(snapshots: list[ReturnSnapshot], freq: RebalanceFreq) -> list[int]:
    """Return indexes where a new rebalance period starts."""

    anchors: list[int] = []
    last_key: tuple[int, int] | None = None

    for i, snap in enumerate(snapshots):
        if freq == "monthly":
            key = (snap.ts.year, snap.ts.month)
        else:
            quarter = (snap.ts.month - 1) // 3 + 1
            key = (snap.ts.year, quarter)

        if key != last_key:
            anchors.append(i)
            last_key = key

    return anchors


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    s = sum(max(v, 0.0) for v in weights.values())
    if s <= 1e-12:
        n = len(weights)
        return {k: 1.0 / n for k in weights} if n else {}
    return {k: max(v, 0.0) / s for k, v in weights.items()}


def _normalize_weights_indexed(weights: list[float]) -> list[float]:
    clipped = [max(w, 0.0) for w in weights]
    s = sum(clipped)
    if s <= 1e-12:
        n = len(clipped)
        return [1.0 / n] * n if n else []
    return [w / s for w in clipped]


def _submatrix(matrix: list[list[float]], idxs: list[int]) -> list[list[float]]:
    return [[matrix[i][j] for j in idxs] for i in idxs]
