from pathlib import Path

import pytest

from quantx.config_validation import ConfigValidationError, load_and_validate_cta_strategy_config


def test_load_and_validate_cta_strategy_config_ok():
    payload = load_and_validate_cta_strategy_config("quantx/configs/cta_strategy_stable.yaml")
    assert payload["strategy"] == "cta_strategy"
    assert payload["timeframe"] == "4h"


def test_load_and_validate_cta_strategy_config_invalid_risk(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
strategy: cta_strategy
timeframe: 4h
universe:
  symbols: [BTCUSDT]
signal:
  lookback: 200
risk_trade_level:
  risk_per_trade: 0.03
portfolio:
  leverage_clip: [0.25, 8.0]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="risk_trade_level.risk_per_trade"):
        load_and_validate_cta_strategy_config(str(bad))
