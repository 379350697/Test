from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime
from math import sqrt
from statistics import mean

from .models import BacktestConfig, BacktestResult, Position, RunMetadata, Trade
from .analytics import evaluate_targets, extended_metrics
from .indicator_cache import IndicatorCache
from .repro import now_utc_iso, python_fingerprint, stable_hash
from .reporting import build_promotion_summary, build_venue_contract
from .strategies import get_strategy_class
from .strategy_loader import load_strategy_repos
from .runtime.events import FillEvent, MarketEvent, OrderEvent
from .runtime.ledger_engine import LedgerEngine
from .runtime.models import OrderIntent
from .runtime.order_engine import OrderEngine
from .runtime.session import RuntimeSession
from .runtime.strategy_runtime import StrategyRuntime
from .runtime.paper_exchange import enrich_runtime_snapshot


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 0
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return abs(mdd)


def _stability_score(metrics: dict[str, float], n_trades: int) -> tuple[dict[str, float], float]:
    quality = min(100.0, max(0.0, metrics.get("sharpe", 0) * 20 + 50))
    risk = max(0.0, 100 - metrics.get("max_drawdown_pct", 100) * 200)
    robustness = min(100.0, 40 + metrics.get("win_rate", 0) * 60)
    cost = max(0.0, 100 - metrics.get("fee_ratio", 1) * 500)
    overtrade = max(0.0, 100 - max(0, n_trades - 200) * 0.5)
    breakdown = {
        "quality": round(quality, 2),
        "risk": round(risk, 2),
        "robustness": round(robustness, 2),
        "cost_sensitivity": round(cost, 2),
        "anti_overtrading": round(overtrade, 2),
    }
    total = round(mean(breakdown.values()), 2)
    return breakdown, total


def _runtime_ts(value) -> str:
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _record_bar_backtest_trade(
    session: RuntimeSession,
    trade: Trade,
    position_side: str,
    *,
    strategy_id: str | None = None,
) -> None:
    side = trade.side.lower()
    reduce_only = (position_side == 'long' and side == 'sell') or (position_side == 'short' and side == 'buy')
    ts = _runtime_ts(trade.ts)
    intent = OrderIntent(
        symbol=trade.symbol,
        side=side,
        position_side=position_side,
        qty=trade.qty,
        price=trade.price,
        order_type='market',
        time_in_force='ioc',
        reduce_only=reduce_only,
        strategy_id=strategy_id,
        reason=trade.reason,
        created_ts=ts,
        tags=('bar_backtest',),
    )
    submission = session.submit_intents([intent], exchange='bar_backtest', ts=ts)
    if not submission or submission[-1].status == 'rejected':
        return

    client_order_id = submission[-1].client_order_id
    lifecycle = [
        OrderEvent(
            symbol=trade.symbol,
            exchange='bar_backtest',
            ts=ts,
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,
            status='acked',
            payload={'reason': trade.reason},
        ),
        OrderEvent(
            symbol=trade.symbol,
            exchange='bar_backtest',
            ts=ts,
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,
            status='working',
            payload={'reason': trade.reason},
        ),
        FillEvent(
            symbol=trade.symbol,
            exchange='bar_backtest',
            ts=ts,
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,
            trade_id=f'{client_order_id}-fill-1',
            side=side,
            position_side=position_side,
            qty=trade.qty,
            price=trade.price,
            fee=trade.fee,
            payload={'reason': trade.reason},
        ),
    ]
    session.apply_events(lifecycle)


def _event_ts_as_datetime(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return datetime.utcnow()


def _is_order_marketable(order, market_price: float) -> bool:
    if order.order_type == 'market' or order.price is None:
        return True
    if order.side == 'buy':
        return market_price <= order.price
    return market_price >= order.price


def _drive_event_backtest_fills(
    session: RuntimeSession,
    event: MarketEvent,
    trades: list[Trade],
) -> None:
    market_price = float(event.payload['price'])
    pending = [
        order
        for order in session.order_engine.orders.values()
        if order.symbol == event.symbol and order.status == 'submitted' and _is_order_marketable(order, market_price)
    ]

    for order in pending:
        lifecycle = [
            OrderEvent(
                symbol=order.symbol,
                exchange=event.exchange,
                ts=event.ts,
                client_order_id=order.client_order_id,
                exchange_order_id=order.client_order_id,
                status='acked',
                payload={'source': 'event_backtest'},
            ),
            OrderEvent(
                symbol=order.symbol,
                exchange=event.exchange,
                ts=event.ts,
                client_order_id=order.client_order_id,
                exchange_order_id=order.client_order_id,
                status='working',
                payload={'source': 'event_backtest'},
            ),
            FillEvent(
                symbol=order.symbol,
                exchange=event.exchange,
                ts=event.ts,
                client_order_id=order.client_order_id,
                exchange_order_id=order.client_order_id,
                trade_id=f'{order.client_order_id}-fill-1',
                side=order.side,
                position_side=order.position_side,
                qty=order.qty,
                price=market_price,
                fee=0.0,
                payload={'source': 'event_backtest'},
            ),
        ]
        session.apply_events(lifecycle)
        trades.append(
            Trade(
                ts=_event_ts_as_datetime(event.ts),
                symbol=order.symbol,
                side=order.side.upper(),
                qty=order.qty,
                price=market_price,
                fee=0.0,
                reason=order.reason or 'event_backtest',
            )
        )


def run_event_backtest(
    event_tape: list[MarketEvent],
    strategy,
    config: BacktestConfig,
    *,
    data_hash: str | None = None,
) -> BacktestResult:
    runtime = StrategyRuntime(strategy=strategy)
    session = RuntimeSession(mode='event_backtest', wallet_balance=config.initial_cash)
    trades: list[Trade] = []
    equity_curve: list[tuple[datetime, float]] = []
    drawdown_curve: list[tuple[datetime, float]] = []
    peak_eq = config.initial_cash

    for event in event_tape:
        session.apply_events([event])
        intents = runtime.on_event(event)
        session.submit_intents(intents, exchange=event.exchange, ts=event.ts)
        _drive_event_backtest_fills(session, event, trades)

        dt = _event_ts_as_datetime(event.ts)
        equity = session.ledger_engine.ledger.equity
        peak_eq = max(peak_eq, equity)
        dd = (equity - peak_eq) / peak_eq if peak_eq else 0.0
        equity_curve.append((dt, equity))
        drawdown_curve.append((dt, dd))

    if not equity_curve:
        now = datetime.utcnow()
        equity_curve.append((now, config.initial_cash))
        drawdown_curve.append((now, 0.0))

    eq_values = [v for _, v in equity_curve] or [config.initial_cash]
    rets = [eq_values[i] / eq_values[i - 1] - 1 for i in range(1, len(eq_values)) if eq_values[i - 1] > 0]
    avg_ret = mean(rets) if rets else 0.0
    vol = (sum((r - avg_ret) ** 2 for r in rets) / max(1, len(rets))) ** 0.5
    sharpe = (avg_ret / vol * sqrt(252)) if vol > 0 else 0.0
    pnl = eq_values[-1] - config.initial_cash
    win_rate = 0.0
    fee_paid = sum(t.fee for t in trades)

    metrics = {
        'total_return_pct': (eq_values[-1] / config.initial_cash - 1) * 100,
        'pnl': pnl,
        'max_drawdown_pct': _max_drawdown(eq_values) * 100,
        'sharpe': sharpe,
        'trades': float(len(trades)),
        'win_rate': win_rate,
        'fee_paid': fee_paid,
        'fee_ratio': fee_paid / max(1e-9, abs(pnl) + 1),
    }
    metrics.update(extended_metrics(eq_values))
    metrics.update({k: float(v) for k, v in evaluate_targets(metrics).items()})
    breakdown, total = _stability_score(metrics, len(trades))

    strategy_profile = {
        'strategy_id': getattr(strategy, 'strategy_id', strategy.__class__.__name__),
        'class_name': strategy.__class__.__name__,
        'module': strategy.__class__.__module__,
        'version': getattr(strategy, 'version', '0.1.0'),
    }
    metadata = RunMetadata(
        strategy_name=strategy_profile['strategy_id'],
        strategy_version=strategy_profile['version'],
        strategy_spec_hash=stable_hash(strategy_profile),
        strategy_source_hash=stable_hash(strategy.__class__.__name__),
        param_hash=stable_hash(getattr(strategy, 'params', {})),
        data_hash=data_hash if data_hash is not None else stable_hash([
            (event.ts, event.symbol, event.exchange, event.channel, event.payload) for event in event_tape
        ]),
        python_version=python_fingerprint(),
        created_at=now_utc_iso(),
    )

    runtime_snapshot = enrich_runtime_snapshot(session.snapshot())
    runtime_snapshot['fidelity'] = 'high'
    runtime_snapshot['venue_contract'] = build_venue_contract(symbol=config.symbol, fidelity='high')

    return BacktestResult(
        config,
        metadata,
        equity_curve,
        drawdown_curve,
        trades,
        metrics,
        breakdown,
        total,
        extra={
            'strategy_profile': strategy_profile,
            'runtime': runtime_snapshot,
        },
    )
def run_backtest(
    candles,
    strategy_name: str,
    strategy_params: dict,
    config: BacktestConfig,
    *,
    use_indicator_cache: bool = False,
    data_hash: str | None = None,
) -> BacktestResult:
    strategy_cls = get_strategy_class(strategy_name)
    strategy = strategy_cls(**strategy_params)
    indicator_cache = IndicatorCache.from_candles(candles) if use_indicator_cache else None
    strategy.indicator_cache = indicator_cache

    cash = config.initial_cash
    pos = Position(config.symbol)
    trades: list[Trade] = []
    equity_curve: list[tuple[datetime, float]] = []
    drawdown_curve: list[tuple[datetime, float]] = []
    peak_eq = cash
    orders_per_day: defaultdict[str, int] = defaultdict(int)
    last_trade_idx: int | None = None
    closed_pnls: list[float] = []
    entry_idx: int | None = None
    peak_price_since_entry: float = 0.0
    trough_price_since_entry: float = 0.0
    runtime_session = RuntimeSession(mode='bar_backtest', wallet_balance=config.initial_cash)
    runtime_strategy_id = getattr(strategy, 'name', strategy_name)

    for i in range(len(candles) - 1):
        c = candles[i]
        nxt = candles[i + 1]
        signal = strategy.signal(candles, i)
        day_key = c.ts.date().isoformat()
        runtime_session.apply_events([
            MarketEvent(
                symbol=config.symbol,
                exchange='bar_backtest',
                channel='bar_close',
                ts=_runtime_ts(c.ts),
                payload={'price': c.close},
            )
        ])

        # shared ATR (used by exits and position sizing)
        atr_val = 0.0
        atr_period = int(strategy_params.get("atr_period", 14) or 14)
        if indicator_cache is not None:
            atr_val = indicator_cache.atr(atr_period)[i] or 0.0
        elif i >= atr_period:
            trs = []
            for k in range(i - atr_period + 1, i + 1):
                prev_close = candles[k - 1].close if k > 0 else candles[k].close
                tr = max(
                    candles[k].high - candles[k].low,
                    abs(candles[k].high - prev_close),
                    abs(candles[k].low - prev_close),
                )
                trs.append(tr)
            atr_val = mean(trs) if trs else 0.0

        mark = cash + pos.qty * c.close
        peak_eq = max(peak_eq, mark)
        dd = (mark - peak_eq) / peak_eq if peak_eq else 0.0
        equity_curve.append((c.ts, mark))
        drawdown_curve.append((c.ts, dd))

        if abs(dd) >= config.risk.max_drawdown_pct:
            if pos.qty != 0:
                if pos.qty > 0:
                    px = nxt.open * (1 - config.slippage_pct)
                    fee = pos.qty * px * config.fee_rate
                    realized = (px - pos.avg_price) * pos.qty - fee
                    closed_pnls.append(realized)
                    cash += pos.qty * px - fee
                    trade = Trade(nxt.ts, config.symbol, "SELL", pos.qty, px, fee, "max_drawdown_stop")
                    trades.append(trade)
                    _record_bar_backtest_trade(runtime_session, trade, 'long', strategy_id=runtime_strategy_id)
                else:
                    qty_abs = abs(pos.qty)
                    px = nxt.open * (1 + config.slippage_pct)
                    fee = qty_abs * px * config.fee_rate
                    realized = (pos.avg_price - px) * qty_abs - fee
                    closed_pnls.append(realized)
                    cash -= qty_abs * px + fee
                    trade = Trade(nxt.ts, config.symbol, "BUY", qty_abs, px, fee, "max_drawdown_stop")
                    trades.append(trade)
                    _record_bar_backtest_trade(runtime_session, trade, 'short', strategy_id=runtime_strategy_id)
                pos.qty = 0
                pos.avg_price = 0.0
                entry_idx = None
                peak_price_since_entry = 0.0
                trough_price_since_entry = 0.0
            break

        # per-trade risk exits (optional, strategy param driven)
        if pos.qty != 0:
            stop_atr_mult = float(strategy_params.get("stop_atr_mult", 0) or 0)
            trail_atr_mult = float(strategy_params.get("trail_atr_mult", 0) or 0)
            max_hold_bars = int(strategy_params.get("max_hold_bars", 0) or 0)

            peak_price_since_entry = max(peak_price_since_entry, c.high) if pos.qty > 0 else peak_price_since_entry
            trough_price_since_entry = min(trough_price_since_entry, c.low) if pos.qty < 0 else trough_price_since_entry
            bars_held = (i - entry_idx) if entry_idx is not None else 0

            if pos.qty > 0:
                hit_stop = stop_atr_mult > 0 and atr_val > 0 and c.close <= (pos.avg_price - stop_atr_mult * atr_val)
                hit_trail = trail_atr_mult > 0 and atr_val > 0 and c.close <= (peak_price_since_entry - trail_atr_mult * atr_val)
            else:
                hit_stop = stop_atr_mult > 0 and atr_val > 0 and c.close >= (pos.avg_price + stop_atr_mult * atr_val)
                hit_trail = trail_atr_mult > 0 and atr_val > 0 and c.close >= (trough_price_since_entry + trail_atr_mult * atr_val)

            donchian_exit_lookback = int(strategy_params.get("donchian_exit_lookback", 0) or 0)
            hit_donchian = False
            if donchian_exit_lookback > 1 and i >= donchian_exit_lookback:
                win = candles[i - donchian_exit_lookback + 1 : i + 1]
                low_n = min(x.low for x in win)
                high_n = max(x.high for x in win)
                if pos.qty > 0:
                    hit_donchian = c.close < low_n
                else:
                    hit_donchian = c.close > high_n

            hit_time = max_hold_bars > 0 and bars_held >= max_hold_bars

            if hit_stop or hit_trail or hit_donchian or hit_time:
                reason = "stop_loss" if hit_stop else ("trailing_stop" if hit_trail else ("donchian_exit" if hit_donchian else "time_stop"))
                if pos.qty > 0:
                    px = nxt.open * (1 - config.slippage_pct)
                    fee = pos.qty * px * config.fee_rate
                    realized = (px - pos.avg_price) * pos.qty - fee
                    closed_pnls.append(realized)
                    cash += pos.qty * px - fee
                    trade = Trade(nxt.ts, config.symbol, "SELL", pos.qty, px, fee, reason)
                    trades.append(trade)
                    _record_bar_backtest_trade(runtime_session, trade, 'long', strategy_id=runtime_strategy_id)
                else:
                    qty_abs = abs(pos.qty)
                    px = nxt.open * (1 + config.slippage_pct)
                    fee = qty_abs * px * config.fee_rate
                    realized = (pos.avg_price - px) * qty_abs - fee
                    closed_pnls.append(realized)
                    cash -= qty_abs * px + fee
                    trade = Trade(nxt.ts, config.symbol, "BUY", qty_abs, px, fee, reason)
                    trades.append(trade)
                    _record_bar_backtest_trade(runtime_session, trade, 'short', strategy_id=runtime_strategy_id)
                pos.qty = 0
                pos.avg_price = 0.0
                entry_idx = None
                peak_price_since_entry = 0.0
                trough_price_since_entry = 0.0
                last_trade_idx = i
                orders_per_day[day_key] += 1
                continue

        if orders_per_day[day_key] >= config.risk.max_orders_per_day:
            continue

        if signal != 0:
            # short-open gate: BTC/ETH long-only; altcoins allow short with filters
            allow_short_open = True
            if config.symbol.upper() in {"BTCUSDT", "ETHUSDT"}:
                allow_short_open = False
            else:
                short_adx_filter = float(strategy_params.get("short_adx_filter", 0) or 0)
                short_ma_period = int(strategy_params.get("short_ma_period", 200) or 200)
                require_price_below_ma = bool(strategy_params.get("short_require_price_below_ma", True))

                cond_adx = False
                if short_adx_filter > 0 and i >= atr_period + 1:
                    p = int(strategy_params.get("adx_period", 14) or 14)
                    if i >= p + 1:
                        if indicator_cache is not None:
                            adx_val = indicator_cache.adx(p)[i]
                            cond_adx = (adx_val or 0.0) > short_adx_filter
                        else:
                            trs, pdms, ndms = [], [], []
                            for k in range(i - p + 1, i + 1):
                                cur = candles[k]
                                prev = candles[k - 1]
                                up_move = cur.high - prev.high
                                down_move = prev.low - cur.low
                                pdm = up_move if (up_move > down_move and up_move > 0) else 0.0
                                ndm = down_move if (down_move > up_move and down_move > 0) else 0.0
                                tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
                                trs.append(tr)
                                pdms.append(pdm)
                                ndms.append(ndm)
                            atr_adx = mean(trs) if trs else 0.0
                            if atr_adx > 1e-12:
                                pdi = 100.0 * (mean(pdms) / atr_adx)
                                ndi = 100.0 * (mean(ndms) / atr_adx)
                                denom = pdi + ndi
                                adx_val = 100.0 * abs(pdi - ndi) / denom if denom > 1e-12 else 0.0
                                cond_adx = adx_val > short_adx_filter

                cond_ma = False
                if require_price_below_ma and i >= short_ma_period:
                    if indicator_cache is not None:
                        ma = indicator_cache.sma(short_ma_period)[i]
                    else:
                        ma = mean([x.close for x in candles[i - short_ma_period + 1 : i + 1]])
                    cond_ma = ma is not None and c.close < ma

                # OR condition: ADX > gate OR price < MA200
                allow_short_open = cond_adx or cond_ma

            # close opposite side first (for true long/short switching)
            if signal > 0 and pos.qty < 0:
                qty_abs = abs(pos.qty)
                px = nxt.open * (1 + config.slippage_pct)
                fee = qty_abs * px * config.fee_rate
                realized = (pos.avg_price - px) * qty_abs - fee
                closed_pnls.append(realized)
                cash -= qty_abs * px + fee
                trade = Trade(nxt.ts, config.symbol, "BUY", qty_abs, px, fee, f"signal:{signal}")
                trades.append(trade)
                _record_bar_backtest_trade(runtime_session, trade, 'short', strategy_id=runtime_strategy_id)
                pos.qty = 0
                pos.avg_price = 0.0
                entry_idx = None
                peak_price_since_entry = 0.0
                trough_price_since_entry = 0.0
                last_trade_idx = i
                orders_per_day[day_key] += 1
                continue
            if signal < 0 and pos.qty > 0:
                px = nxt.open * (1 - config.slippage_pct)
                fee = pos.qty * px * config.fee_rate
                realized = (px - pos.avg_price) * pos.qty - fee
                closed_pnls.append(realized)
                cash += pos.qty * px - fee
                trade = Trade(nxt.ts, config.symbol, "SELL", pos.qty, px, fee, f"signal:{signal}")
                trades.append(trade)
                _record_bar_backtest_trade(runtime_session, trade, 'long', strategy_id=runtime_strategy_id)
                pos.qty = 0
                pos.avg_price = 0.0
                entry_idx = None
                peak_price_since_entry = 0.0
                trough_price_since_entry = 0.0
                last_trade_idx = i
                orders_per_day[day_key] += 1
                continue

            # open side if flat
            if pos.qty == 0:
                if signal < 0 and not allow_short_open:
                    continue
                if last_trade_idx is not None and (i - last_trade_idx) < config.risk.cooldown_bars:
                    continue

                open_cash = cash * 0.2
                if strategy_name == "dca" and signal > 0:
                    open_cash = min(cash, float(strategy_params.get("buy_amount_usdt", 100)))

                risk_per_trade = float(strategy_params.get("risk_per_trade", 0) or 0)
                stop_atr_mult = float(strategy_params.get("stop_atr_mult", 0) or 0)
                max_position_pct = float(strategy_params.get("max_position_pct", 0) or 0)
                atr_floor_mult = float(strategy_params.get("atr_floor_mult", 0) or 0)
                atr_ma_period = int(strategy_params.get("atr_ma_period", 50) or 50)
                if risk_per_trade > 0 and stop_atr_mult > 0 and atr_val > 0:
                    atr_eff = atr_val
                    if atr_floor_mult > 0:
                        if indicator_cache is not None:
                            atr_series = indicator_cache.atr(atr_period)
                            start = max(0, i - atr_ma_period + 1)
                            atr_hist = [v for v in atr_series[start : i + 1] if v is not None]
                        else:
                            atr_hist = []
                            for k in range(i - atr_ma_period + 1, i + 1):
                                if k < 0:
                                    continue
                                trs2 = []
                                if k >= atr_period:
                                    for kk in range(k - atr_period + 1, k + 1):
                                        prev_close2 = candles[kk - 1].close if kk > 0 else candles[kk].close
                                        tr2 = max(
                                            candles[kk].high - candles[kk].low,
                                            abs(candles[kk].high - prev_close2),
                                            abs(candles[kk].low - prev_close2),
                                        )
                                        trs2.append(tr2)
                                if trs2:
                                    atr_hist.append(mean(trs2))
                        if atr_hist:
                            atr_ma = mean(atr_hist)
                            atr_floor = atr_ma * atr_floor_mult
                            atr_eff = max(atr_eff, atr_floor)

                    risk_amount = max(mark, 0.0) * risk_per_trade
                    qty_risk = risk_amount / max(1e-12, (atr_eff * stop_atr_mult))
                    px_est = nxt.open * (1 + config.slippage_pct if signal > 0 else 1 - config.slippage_pct)
                    open_cash_risk = qty_risk * px_est
                    if max_position_pct > 0:
                        open_cash_cap = max(mark, 0.0) * max_position_pct
                        open_cash = min(cash, open_cash_risk, open_cash_cap)
                    else:
                        open_cash = min(cash, open_cash_risk)

                if open_cash <= 0:
                    continue

                if signal > 0:
                    px = nxt.open * (1 + config.slippage_pct)
                    qty = open_cash / px
                    fee = qty * px * config.fee_rate
                    cash -= qty * px + fee
                    pos.qty = qty
                    pos.avg_price = px
                    trade = Trade(nxt.ts, config.symbol, "BUY", qty, px, fee, f"signal:{signal}")
                    trades.append(trade)
                    _record_bar_backtest_trade(runtime_session, trade, 'long', strategy_id=runtime_strategy_id)
                    peak_price_since_entry = c.high
                    trough_price_since_entry = c.low
                else:
                    px = nxt.open * (1 - config.slippage_pct)
                    qty = open_cash / px
                    fee = qty * px * config.fee_rate
                    cash += qty * px - fee
                    pos.qty = -qty
                    pos.avg_price = px
                    trade = Trade(nxt.ts, config.symbol, "SELL", qty, px, fee, f"signal:{signal}")
                    trades.append(trade)
                    _record_bar_backtest_trade(runtime_session, trade, 'short', strategy_id=runtime_strategy_id)
                    peak_price_since_entry = c.high
                    trough_price_since_entry = c.low

                pos.last_trade_ts = c.ts
                entry_idx = i
                last_trade_idx = i
                orders_per_day[day_key] += 1

    if candles:
        runtime_session.apply_events([
            MarketEvent(
                symbol=config.symbol,
                exchange='bar_backtest',
                channel='bar_close',
                ts=_runtime_ts(candles[-1].ts),
                payload={'price': candles[-1].close},
            )
        ])
        end_equity = cash + pos.qty * candles[-1].close
        equity_curve.append((candles[-1].ts, end_equity))
    eq_values = [v for _, v in equity_curve] or [config.initial_cash]
    rets = [eq_values[i] / eq_values[i - 1] - 1 for i in range(1, len(eq_values)) if eq_values[i - 1] > 0]
    avg_ret = mean(rets) if rets else 0.0
    vol = (sum((r - avg_ret) ** 2 for r in rets) / max(1, len(rets))) ** 0.5
    sharpe = (avg_ret / vol * sqrt(252)) if vol > 0 else 0.0
    pnl = eq_values[-1] - config.initial_cash
    closed_trades = [x for x in closed_pnls]
    win_rate = (sum(1 for x in closed_trades if x > 0) / len(closed_trades)) if closed_trades else 0.0
    fee_paid = sum(t.fee for t in trades)

    metrics = {
        "total_return_pct": (eq_values[-1] / config.initial_cash - 1) * 100,
        "pnl": pnl,
        "max_drawdown_pct": _max_drawdown(eq_values) * 100,
        "sharpe": sharpe,
        "trades": float(len(trades)),
        "win_rate": win_rate,
        "fee_paid": fee_paid,
        "fee_ratio": fee_paid / max(1e-9, abs(pnl) + 1),
    }
    metrics.update(extended_metrics(eq_values))
    metrics.update({k: float(v) for k, v in evaluate_targets(metrics).items()})
    breakdown, total = _stability_score(metrics, len(trades))
    strategy_profile = strategy_cls.profile()
    metadata = RunMetadata(
        strategy_name=strategy_name,
        strategy_version=strategy.version,
        strategy_spec_hash=stable_hash(strategy_profile),
        strategy_source_hash=strategy_cls.source_hash(),
        param_hash=stable_hash(strategy_params),
        data_hash=data_hash if data_hash is not None else stable_hash([(c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume) for c in candles]),
        python_version=python_fingerprint(),
        created_at=now_utc_iso(),
    )
    runtime_snapshot = enrich_runtime_snapshot(runtime_session.snapshot())
    runtime_snapshot['fidelity'] = 'low'
    runtime_snapshot['venue_contract'] = build_venue_contract(symbol=config.symbol, fidelity='low')
    return BacktestResult(
        config,
        metadata,
        equity_curve,
        drawdown_curve,
        trades,
        metrics,
        breakdown,
        total,
        extra={
            "strategy_profile": strategy_profile,
            "runtime": runtime_snapshot,
        },
    )


def _run_job(job):
    candles, strategy_name, params, config_dict, strategy_repo_paths, use_indicator_cache, data_hash = job
    if strategy_repo_paths:
        load_strategy_repos(strategy_repo_paths)
    config = BacktestConfig(**config_dict)
    return run_backtest(candles, strategy_name, params, config, use_indicator_cache=use_indicator_cache, data_hash=data_hash)


def run_parallel_matrix(
    candles_by_symbol_tf: dict,
    strategy_grid: list[tuple[str, dict]],
    config_template: dict,
    max_workers: int = 4,
    strategy_repo_paths: list[str] | None = None,
    use_indicator_cache: bool = False,
):
    jobs = []
    for (symbol, tf), candles in candles_by_symbol_tf.items():
        job_data_hash = stable_hash([(c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume) for c in candles])
        for strategy_name, params in strategy_grid:
            cfg = dict(config_template)
            cfg["symbol"] = symbol
            cfg["timeframe"] = tf
            jobs.append((candles, strategy_name, params, cfg, strategy_repo_paths or [], use_indicator_cache, job_data_hash))
    if max_workers <= 1:
        return [_run_job(job) for job in jobs]
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            return list(ex.map(_run_job, jobs))
    except OSError:
        return [_run_job(job) for job in jobs]


def result_to_dict(res: BacktestResult, mode: str = "full") -> dict:
    if mode not in {"full", "summary", "minimal"}:
        raise ValueError(f"unsupported result mode: {mode}")

    payload = {
        "config": asdict(res.config),
        "metadata": asdict(res.metadata),
        "metrics": res.metrics,
        "score": {"total": res.score_total, "breakdown": res.score_breakdown},
    }
    payload["promotion_summary"] = build_promotion_summary(
        payload,
        fidelity=str(res.extra.get("runtime", {}).get("fidelity", "low")),
        runtime_mode=str(res.extra.get("runtime", {}).get("mode", "bar_backtest")),
        trade_count=len(res.trades),
        stability_score=float(res.score_total),
    )
    venue_contract = dict(res.extra.get("runtime", {}).get("venue_contract", {}))
    if not venue_contract:
        venue_contract = build_venue_contract(
            symbol=str(res.config.symbol),
            fidelity=str(res.extra.get("runtime", {}).get("fidelity", "low")),
        )
    payload["venue_contract"] = venue_contract
    payload["runtime_mode"] = str(venue_contract.get("runtime_mode", "cash"))
    payload["fidelity"] = str(venue_contract.get("fidelity", "unknown"))
    if mode == "minimal":
        return payload
    if mode == "summary":
        payload["trade_count"] = len(res.trades)
        payload["last_trade"] = asdict(res.trades[-1]) if res.trades else None
        return payload

    payload.update(
        {
            "trades": [asdict(t) for t in res.trades],
            "equity_curve": [(t.isoformat(), v) for t, v in res.equity_curve],
            "drawdown_curve": [(t.isoformat(), v) for t, v in res.drawdown_curve],
            "extra": res.extra,
        }
    )
    return payload
