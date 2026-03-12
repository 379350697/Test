from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SymbolBudget:
    symbol: str
    max_margin: float
    max_notional: float
    max_leverage: float


class MarginAllocator:
    def __init__(self, total_margin: float, max_symbol_weight: float = 0.5, max_leverage: float = 1.0):
        self.total_margin = float(total_margin)
        self.max_symbol_weight = float(max_symbol_weight)
        self.max_leverage = float(max_leverage)

    def allocate(self, *, watchlist: tuple[str, ...], target_scores: dict[str, float]) -> dict[str, SymbolBudget]:
        symbols = tuple(symbol.upper() for symbol in watchlist)
        positive_scores = {symbol: max(float(target_scores.get(symbol, 0.0) or 0.0), 0.0) for symbol in symbols}
        score_total = sum(positive_scores.values())
        if score_total <= 0:
            equal_weight = 1.0 / len(symbols) if symbols else 0.0
            weights = {symbol: equal_weight for symbol in symbols}
        else:
            weights = {symbol: score / score_total for symbol, score in positive_scores.items()}

        symbol_cap_margin = self.total_margin * self.max_symbol_weight
        budgets: dict[str, SymbolBudget] = {}
        for symbol in symbols:
            max_margin = min(self.total_margin * weights.get(symbol, 0.0), symbol_cap_margin)
            budgets[symbol] = SymbolBudget(
                symbol=symbol,
                max_margin=max_margin,
                max_notional=max_margin * self.max_leverage,
                max_leverage=self.max_leverage,
            )
        return budgets
