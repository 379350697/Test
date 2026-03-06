from __future__ import annotations

import json
from pathlib import Path

from .backtest import result_to_dict


def _render_payload(payload: dict, out_dir: str) -> dict:
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)

    json_path = p / "report.json"
    md_path = p / "report.md"
    chart_path = p / "equity.png"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md = [
        f"# QuantX Report - {payload['metadata']['strategy_name']}",
        "",
        "## Metrics",
    ]
    for k, v in payload["metrics"].items():
        md.append(f"- **{k}**: {v}")
    md.extend(
        [
            "",
            "## Stability Score",
            f"- total: {payload['score']['total']}",
            *[f"- {k}: {v}" for k, v in payload["score"].get("breakdown", {}).items()],
            "",
            "## Reproducibility",
            f"- strategy_version: {payload['metadata']['strategy_version']}",
            f"- strategy_spec_hash: {payload['metadata']['strategy_spec_hash']}",
            f"- strategy_source_hash: {payload['metadata']['strategy_source_hash']}",
            f"- param_hash: {payload['metadata']['param_hash']}",
            f"- data_hash: {payload['metadata']['data_hash']}",
            f"- python: {payload['metadata']['python_version']}",
            "",
            "## Artifacts",
            "- report.json",
            "- equity.png",
        ]
    )
    strategy_profile = payload.get("extra", {}).get("strategy_profile", {})
    if strategy_profile:
        md.extend(["", "## Strategy Profile"])
        for k, v in strategy_profile.items():
            md.append(f"- {k}: {v}")

    if payload.get("extra"):
        md.extend(["", "## Extra"])
        for k, v in payload["extra"].items():
            md.append(f"- {k}: {v}")

    md_path.write_text("\n".join(md), encoding="utf-8")

    try:
        import matplotlib.pyplot as plt

        ys = [y for _, y in payload["equity_curve"]]
        plt.figure(figsize=(10, 4))
        plt.plot(ys)
        plt.title("Equity Curve")
        plt.xlabel("bar")
        plt.ylabel("equity")
        plt.tight_layout()
        plt.savefig(chart_path)
        plt.close()
    except Exception:
        chart_path.write_text("matplotlib unavailable", encoding="utf-8")

    return {"json": str(json_path), "markdown": str(md_path), "chart": str(chart_path)}


def write_report(result, out_dir: str) -> dict:
    payload = result_to_dict(result)
    return _render_payload(payload, out_dir)


def write_report_payload(payload: dict, out_dir: str) -> dict:
    return _render_payload(payload, out_dir)
