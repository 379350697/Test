from __future__ import annotations

from quantx.live_market_driver import OKXKlineMarketDriver


class _ClosedBarStub:
    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []

    def get_candles(self, symbol: str, *, bar: str = "5m", limit: int = 200):
        self.calls.append((symbol, bar, limit))
        return [
            {
                "ts": "1710000300000",
                "open": 100.0 if symbol == "BTC-USDT-SWAP" else 200.0,
                "high": 102.0 if symbol == "BTC-USDT-SWAP" else 202.0,
                "low": 99.0 if symbol == "BTC-USDT-SWAP" else 199.0,
                "close": 101.5 if symbol == "BTC-USDT-SWAP" else 201.5,
                "volume": 12.5 if symbol == "BTC-USDT-SWAP" else 8.0,
                "confirmed": True,
            },
            {
                "ts": "1710000600000",
                "open": 101.5 if symbol == "BTC-USDT-SWAP" else 201.5,
                "high": 103.0 if symbol == "BTC-USDT-SWAP" else 203.0,
                "low": 100.0 if symbol == "BTC-USDT-SWAP" else 200.0,
                "close": 102.0 if symbol == "BTC-USDT-SWAP" else 202.0,
                "volume": 10.0 if symbol == "BTC-USDT-SWAP" else 6.0,
                "confirmed": False,
            },
        ]


def test_okx_kline_market_driver_emits_only_new_closed_5m_bars_per_symbol():
    client = _ClosedBarStub()
    driver = OKXKlineMarketDriver(
        client=client,
        watchlist=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        timeframe="5m",
    )

    first = driver.poll_once()
    second = driver.poll_once()

    assert set(first) == {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
    assert first["BTC-USDT-SWAP"][-1].close == 101.5
    assert second == {}
    assert client.calls == [
        ("BTC-USDT-SWAP", "5m", 200),
        ("ETH-USDT-SWAP", "5m", 200),
        ("BTC-USDT-SWAP", "5m", 200),
        ("ETH-USDT-SWAP", "5m", 200),
    ]
