from __future__ import annotations

from dataclasses import dataclass, field

from quantx.risk_engine import check_cross_margin_health

from .models import AccountLedger, OrderIntent


@dataclass(slots=True)
class RuntimeRiskLimits:
    min_available_margin: float = 0.0
    max_risk_ratio: float = 0.5


@dataclass(slots=True)
class RuntimeRiskValidator:
    limits: RuntimeRiskLimits = field(default_factory=RuntimeRiskLimits)

    def validate_intent(self, intent: OrderIntent, ledger: AccountLedger) -> tuple[bool, str]:
        if intent.position_side not in {'long', 'short'}:
            return False, 'invalid_position_side'

        if intent.reduce_only:
            if self._would_increase_position(intent):
                return False, 'reduce_only_would_increase_position'

            current_qty = ledger.positions.get((intent.symbol, intent.position_side))
            if current_qty is None or intent.qty > current_qty.qty + 1e-12:
                return False, 'reduce_only_exceeds_position'
            return True, 'ok'

        return self.check_account_health(ledger)

    def check_account_health(self, ledger: AccountLedger) -> tuple[bool, str]:
        return check_cross_margin_health(
            available_margin=ledger.available_margin,
            risk_ratio=ledger.risk_ratio,
            min_available_margin=self.limits.min_available_margin,
            max_risk_ratio=self.limits.max_risk_ratio,
        )

    def _would_increase_position(self, intent: OrderIntent) -> bool:
        return (intent.position_side == 'long' and intent.side == 'buy') or (
            intent.position_side == 'short' and intent.side == 'sell'
        )
