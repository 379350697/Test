"""Meta-portfolio allocator across strategy sleeves (P2)."""

from __future__ import annotations


def blend_strategy_weights(
    regime: str,
    regime_mix: dict[str, dict[str, float]],
    strategy_weights: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Blend strategy-level asset weights using regime-specific sleeve mix."""

    mix = regime_mix.get(regime)
    if not mix:
        raise ValueError(f"unknown regime: {regime}")

    out: dict[str, float] = {}
    for sleeve, sleeve_w in mix.items():
        sw = strategy_weights.get(sleeve, {})
        for asset, w in sw.items():
            out[asset] = out.get(asset, 0.0) + sleeve_w * w

    gross = sum(abs(v) for v in out.values())
    if gross <= 1e-12:
        return out
    return {k: v / gross for k, v in out.items()}
