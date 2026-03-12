from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_promotion_summary(
    payload: dict[str, Any],
    *,
    fidelity: str | None = None,
    runtime_mode: str | None = None,
    trade_count: int | None = None,
    stability_score: float | None = None,
) -> dict[str, object]:
    metrics = payload.get('metrics', {}) if isinstance(payload.get('metrics'), dict) else {}
    extra = payload.get('extra', {}) if isinstance(payload.get('extra'), dict) else {}
    runtime = extra.get('runtime', {}) if isinstance(extra.get('runtime'), dict) else {}
    score = payload.get('score', {}) if isinstance(payload.get('score'), dict) else {}
    trades = payload.get('trades') if isinstance(payload.get('trades'), list) else None
    config = payload.get('config', {}) if isinstance(payload.get('config'), dict) else {}

    resolved_trade_count = trade_count
    if resolved_trade_count is None:
        if trades is not None:
            resolved_trade_count = len(trades)
        else:
            resolved_trade_count = int(_as_float(metrics.get('trades', payload.get('trade_count', 0.0))))

    resolved_fidelity = fidelity or str(runtime.get('fidelity') or payload.get('fidelity') or 'unknown')
    resolved_runtime_mode = runtime_mode or str(runtime.get('mode') or payload.get('mode') or config.get('timeframe') or 'unknown')
    resolved_stability_score = stability_score if stability_score is not None else _as_float(score.get('total', 0.0))

    return {
        'fidelity': resolved_fidelity,
        'trade_count': int(resolved_trade_count or 0),
        'fee_ratio': _as_float(metrics.get('fee_ratio', 0.0)),
        'max_drawdown_pct': _as_float(metrics.get('max_drawdown_pct', 0.0)),
        'stability_score': float(resolved_stability_score),
        'runtime_mode': resolved_runtime_mode,
    }


def build_venue_contract(
    *,
    symbol: str | None = None,
    exchange: str | None = None,
    product: str | None = None,
    margin_mode: str | None = None,
    position_mode: str | None = None,
    runtime_mode: str | None = None,
    fidelity: str | None = None,
) -> dict[str, str]:
    normalized_symbol = str(symbol or '').upper()
    inferred_swap = normalized_symbol.endswith('-SWAP')
    resolved_product = str(product or ('swap' if inferred_swap else 'spot')).lower()
    resolved_exchange = str(exchange or ('okx' if inferred_swap else 'simulated')).lower()
    resolved_margin_mode = str(margin_mode or ('cross' if resolved_product == 'swap' else 'cash')).lower()
    resolved_position_mode = str(position_mode or ('net' if resolved_product == 'swap' else 'long_short')).lower()
    resolved_runtime_mode = str(runtime_mode or ('derivatives' if resolved_product == 'swap' else 'cash')).lower()
    resolved_fidelity = str(fidelity or 'unknown').lower()
    return {
        'exchange': resolved_exchange,
        'product': resolved_product,
        'margin_mode': resolved_margin_mode,
        'position_mode': resolved_position_mode,
        'runtime_mode': resolved_runtime_mode,
        'fidelity': resolved_fidelity,
    }


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _render_payload(payload: dict, out_dir: str) -> dict:
    payload = dict(payload)
    payload.setdefault('promotion_summary', build_promotion_summary(payload))

    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)

    json_path = p / 'report.json'
    md_path = p / 'report.md'
    chart_path = p / 'equity.png'

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    md = [
        f"# QuantX Report - {payload['metadata']['strategy_name']}",
        '',
        '## Metrics',
    ]
    for k, v in payload['metrics'].items():
        md.append(f"- **{k}**: {v}")

    promotion_summary = payload.get('promotion_summary', {})
    if promotion_summary:
        md.extend(['', '## Promotion Summary'])
        for k, v in promotion_summary.items():
            md.append(f"- {k}: {v}")

    md.extend(
        [
            '',
            '## Stability Score',
            f"- total: {payload['score']['total']}",
            *[f"- {k}: {v}" for k, v in payload['score'].get('breakdown', {}).items()],
            '',
            '## Reproducibility',
            f"- strategy_version: {payload['metadata']['strategy_version']}",
            f"- strategy_spec_hash: {payload['metadata']['strategy_spec_hash']}",
            f"- strategy_source_hash: {payload['metadata']['strategy_source_hash']}",
            f"- param_hash: {payload['metadata']['param_hash']}",
            f"- data_hash: {payload['metadata']['data_hash']}",
            f"- python: {payload['metadata']['python_version']}",
            '',
            '## Artifacts',
            '- report.json',
            '- equity.png',
        ]
    )
    strategy_profile = payload.get('extra', {}).get('strategy_profile', {})
    if strategy_profile:
        md.extend(['', '## Strategy Profile'])
        for k, v in strategy_profile.items():
            md.append(f"- {k}: {v}")

    if payload.get('extra'):
        md.extend(['', '## Extra'])
        for k, v in payload['extra'].items():
            md.append(f"- {k}: {v}")

    md_path.write_text('\n'.join(md), encoding='utf-8')

    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]

        ys = [y for _, y in payload['equity_curve']]
        plt.figure(figsize=(10, 4))
        plt.plot(ys)
        plt.title('Equity Curve')
        plt.xlabel('bar')
        plt.ylabel('equity')
        plt.tight_layout()
        plt.savefig(chart_path)
        plt.close()
    except Exception:
        chart_path.write_text('matplotlib unavailable', encoding='utf-8')

    return {'json': str(json_path), 'markdown': str(md_path), 'chart': str(chart_path)}


def write_report(result, out_dir: str) -> dict:
    from .backtest import result_to_dict

    payload = result_to_dict(result)
    return _render_payload(payload, out_dir)


def write_report_payload(payload: dict, out_dir: str) -> dict:
    return _render_payload(payload, out_dir)
