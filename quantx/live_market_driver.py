from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .models import Candle


class LiveMarketDriver(Protocol):
    venue: str

    def stream(self) -> Iterable[tuple[str, list[Candle]]]:
        ...


@dataclass(slots=True)
class OKXKlineMarketDriver:
    venue: str = 'okx'

    def stream(self) -> Iterable[tuple[str, list[Candle]]]:
        return []
