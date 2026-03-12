from __future__ import annotations

from dataclasses import dataclass, field

from .events import MarketEvent
from .fill_engine import FillEngine, FillEngineConfig
from .models import OrderIntent
from .session import RuntimeSession


@dataclass(slots=True)
class PaperExchangeConfig:
    mode: str = 'paper'
    queue_delay_ticks: int = 1
    cancel_delay_ticks: int = 1
    partial_fill_ratio: float = 1.0
    slippage_bps: float = 0.0

    def fill_engine_config(self) -> FillEngineConfig:
        return FillEngineConfig(
            queue_delay_ticks=self.queue_delay_ticks,
            cancel_delay_ticks=self.cancel_delay_ticks,
            partial_fill_ratio=self.partial_fill_ratio,
            slippage_bps=self.slippage_bps,
        )


@dataclass(slots=True)
class PaperExchangeSimulator:
    initial_cash: float = 10_000.0
    config: PaperExchangeConfig = field(default_factory=PaperExchangeConfig)
    session: RuntimeSession = field(init=False)
    fill_engine: FillEngine = field(init=False)

    def __post_init__(self) -> None:
        self.session = RuntimeSession(mode=self.config.mode, wallet_balance=self.initial_cash)
        self.fill_engine = FillEngine(self.config.fill_engine_config())

    def submit_intents(self, intents: list[OrderIntent], *, exchange_name: str, ts: str) -> list[object]:
        existing_ids = set(self.session.order_engine.orders)
        emitted: list[object] = list(self.session.submit_intents(intents, exchange=exchange_name, ts=ts))

        for client_order_id in self.session.order_engine.orders:
            if client_order_id in existing_ids:
                continue
            order = self.session.order_engine.get_order(client_order_id)
            if order.status != 'submitted':
                continue
            emitted.extend(self.session.apply_events(self.fill_engine.submit_order(order, exchange=exchange_name, ts=ts)))

        return emitted

    def on_market_event(self, event: MarketEvent) -> list[object]:
        emitted: list[object] = list(self.session.apply_events([event]))
        emitted.extend(self.session.apply_events(self.fill_engine.on_market_event(event)))
        return emitted

    def cancel_order(self, *, client_order_id: str, ts: str) -> list[object]:
        order = self.session.order_engine.get_order(client_order_id)
        return list(
            self.fill_engine.request_cancel(
                client_order_id=client_order_id,
                symbol=order.symbol,
                exchange=self.config.mode,
                ts=ts,
            )
        )

    def snapshot(self) -> dict[str, object]:
        return self.session.snapshot()
