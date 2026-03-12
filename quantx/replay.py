"""Daily replay summary generator for personal live trading operations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from .audit import JsonlAuditStore
from .oms import JsonlOMSStore
from .runtime.events import AccountEvent, FillEvent, MarketEvent
from .runtime.models import OrderIntent
from .runtime.paper_exchange import PaperExchangeConfig, PaperExchangeSimulator
from .runtime.ledger_engine import LedgerEngine
from .runtime.replay_store import RuntimeReplayStore


@dataclass(slots=True)
class DailyReplayReport:
    day: str
    event_counts: dict[str, int]
    level_counts: dict[str, int]
    reject_reason_top: list[tuple[str, int]]
    retries: int
    accepted: int
    rejected: int
    oms_event_counts: dict[str, int]
    audit_ok: bool
    audit_events: int
    invalid_event_lines: int
    runtime_summary: dict[str, Any]
    paper_summary: dict[str, Any]
    drift_metrics: dict[str, Any]


def _same_day(ts: str, day: date) -> bool:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.date() == day


def _append_sequence_state(sequences: dict[str, list[str]], client_order_id: str, status: str) -> None:
    sequence = sequences.setdefault(client_order_id, [])
    if not sequence or sequence[-1] != status:
        sequence.append(status)


def _append_runtime_replay_state(sequences: dict[str, list[str]], client_order_id: str, status: str) -> None:
    prefixes = {
        'acked': ['intent_created', 'risk_accepted', 'submitted', 'acked'],
        'working': ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working'],
        'partially_filled': ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working', 'partially_filled'],
        'filled': ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working', 'filled'],
        'canceled': ['intent_created', 'risk_accepted', 'submitted', 'acked', 'working', 'canceled'],
        'rejected': ['intent_created', 'rejected'],
        'expired': ['intent_created', 'risk_accepted', 'submitted', 'expired'],
    }
    sequence = sequences.setdefault(client_order_id, [])
    for state in prefixes.get(status, [status]):
        if state not in sequence:
            sequence.append(state)


def _paper_replay_config(market_rows: list[dict[str, Any]]) -> PaperExchangeConfig:
    config = PaperExchangeConfig(mode='paper_replay')
    for row in market_rows:
        payload = row.get('payload', {}) if isinstance(row.get('payload'), dict) else {}
        if 'paper_slippage_bps' in payload:
            config.slippage_bps = float(payload.get('paper_slippage_bps', 0.0) or 0.0)
        if 'paper_partial_fill_ratio' in payload:
            config.partial_fill_ratio = float(payload.get('paper_partial_fill_ratio', 1.0) or 1.0)
        if 'paper_queue_delay_ticks' in payload:
            config.queue_delay_ticks = int(payload.get('paper_queue_delay_ticks', 1) or 1)
        if 'paper_cancel_delay_ticks' in payload:
            config.cancel_delay_ticks = int(payload.get('paper_cancel_delay_ticks', 1) or 1)
    return config


def _event_dt(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return datetime.min


def _is_reduce_only(position_side: str, side: str) -> bool:
    return (position_side == 'long' and side == 'sell') or (position_side == 'short' and side == 'buy')


def _market_event_from_row(row: dict[str, Any]) -> MarketEvent:
    payload = row.get('payload', {}) if isinstance(row.get('payload'), dict) else {}
    return MarketEvent(
        symbol=str(row.get('symbol', '')).upper(),
        exchange=str(row.get('exchange', 'replay')),
        channel=str(row.get('channel', 'market')),
        ts=str(row.get('ts', '')),
        payload={'price': float(payload.get('price', 0.0) or 0.0)},
    )


def _rerun_paper_summary(events: list[dict[str, Any]], live_summary: dict[str, Any]) -> dict[str, Any]:
    market_rows = RuntimeReplayStore.market_tape(events)
    if not market_rows:
        paper_summary = dict(live_summary)
        paper_summary['mode'] = 'paper_replay'
        return paper_summary

    simulator = PaperExchangeSimulator(initial_cash=0.0, config=_paper_replay_config(market_rows))
    fill_prices: list[float] = []
    funding_total = 0.0
    seen_order_ids: set[str] = set()
    replay_intents: list[tuple[datetime, str, OrderIntent]] = []
    funding_events: list[AccountEvent] = []

    for ev in RuntimeReplayStore.execution_events(events):
        kind = str(ev.get('kind', ''))
        if kind == 'fill_event':
            client_order_id = str(ev.get('client_order_id', ''))
            if client_order_id in seen_order_ids:
                continue
            seen_order_ids.add(client_order_id)
            side = str(ev.get('side', '')).lower()
            position_side = str(ev.get('position_side', 'long')).lower()
            ts = str(ev.get('ts', ''))
            replay_intents.append((
                _event_dt(ts),
                ts,
                OrderIntent(
                    symbol=str(ev.get('symbol', '')).upper(),
                    side=side,
                    position_side=position_side,
                    qty=float(ev.get('qty', 0.0) or 0.0),
                    price=float(ev.get('price', 0.0) or 0.0),
                    order_type='market',
                    time_in_force='ioc',
                    reduce_only=_is_reduce_only(position_side, side),
                    intent_id=client_order_id or None,
                    strategy_id='paper_replay',
                    reason='live_fill_replay',
                ),
            ))
            continue

        if kind == 'account_event' and str(ev.get('event_type', '')) == 'funding':
            payload = ev.get('payload', {}) if isinstance(ev.get('payload'), dict) else {}
            funding_events.append(
                AccountEvent(
                    exchange=str(ev.get('exchange', 'paper_replay')),
                    ts=str(ev.get('ts', '')),
                    event_type='funding',
                    payload={
                        'symbol': str(payload.get('symbol') or ev.get('symbol') or '').upper(),
                        'position_side': str(payload.get('position_side', 'long')).lower(),
                        'amount': float(payload.get('amount', 0.0) or 0.0),
                    },
                )
            )

    replay_intents.sort(key=lambda item: item[0])
    market_events = sorted((_market_event_from_row(row) for row in market_rows), key=lambda event: _event_dt(event.ts))

    intent_idx = 0
    for market_event in market_events:
        market_dt = _event_dt(market_event.ts)
        while intent_idx < len(replay_intents) and replay_intents[intent_idx][0] <= market_dt:
            _, submit_ts, intent = replay_intents[intent_idx]
            simulator.submit_intents([intent], exchange_name='paper_replay', ts=submit_ts)
            intent_idx += 1
        emitted = simulator.on_market_event(market_event)
        fill_prices.extend(event.price for event in emitted if isinstance(event, FillEvent))

    if market_events:
        last_market = market_events[-1]
        while intent_idx < len(replay_intents):
            _, submit_ts, intent = replay_intents[intent_idx]
            simulator.submit_intents([intent], exchange_name='paper_replay', ts=submit_ts)
            emitted = simulator.on_market_event(
                MarketEvent(
                    symbol=last_market.symbol,
                    exchange=last_market.exchange,
                    channel=last_market.channel,
                    ts=submit_ts,
                    payload=dict(last_market.payload),
                )
            )
            fill_prices.extend(event.price for event in emitted if isinstance(event, FillEvent))
            intent_idx += 1

    for account_event in sorted(funding_events, key=lambda event: _event_dt(event.ts)):
        simulator.session.apply_events([account_event])
        funding_total += float(account_event.payload.get('amount', 0.0) or 0.0)

    paper_summary = simulator.snapshot()
    paper_summary['mode'] = 'paper_replay'
    paper_summary['fill_prices'] = fill_prices
    paper_summary['funding_total'] = funding_total
    return paper_summary


def _summarize_runtime_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    order_state_sequences: dict[str, list[str]] = {}
    ledger_engine = LedgerEngine(wallet_balance=0.0)
    last_symbol = ''
    last_exchange = 'replay'
    live_fill_prices: list[float] = []
    funding_total = 0.0

    for ev in events:
        kind = str(ev.get('kind', ''))
        if kind == 'order_event':
            client_order_id = str(ev.get('client_order_id', 'unknown'))
            _append_runtime_replay_state(order_state_sequences, client_order_id, str(ev.get('status', 'unknown')))
            last_exchange = str(ev.get('exchange', last_exchange) or last_exchange)
            last_symbol = str(ev.get('symbol', last_symbol) or last_symbol)
            continue

        if kind == 'fill_event':
            client_order_id = str(ev.get('client_order_id', 'unknown'))
            _append_runtime_replay_state(order_state_sequences, client_order_id, 'filled')
            fill = FillEvent(
                symbol=str(ev.get('symbol', '')).upper(),
                exchange=str(ev.get('exchange', 'replay')),
                ts=str(ev.get('ts', '')),
                client_order_id=client_order_id,
                exchange_order_id=str(ev.get('exchange_order_id', '')) or None,
                trade_id=str(ev.get('trade_id', '')),
                side=str(ev.get('side', '')).lower(),
                position_side=str(ev.get('position_side', 'net')).lower(),
                qty=float(ev.get('qty', 0.0) or 0.0),
                price=float(ev.get('price', 0.0) or 0.0),
                fee=float(ev.get('fee', 0.0) or 0.0),
                payload=ev.get('payload', {}) if isinstance(ev.get('payload'), dict) else {},
            )
            ledger_engine.apply_fill(fill)
            live_fill_prices.append(fill.price)
            last_exchange = fill.exchange
            last_symbol = fill.symbol
            continue

        if kind == 'market_event':
            payload = ev.get('payload', {}) if isinstance(ev.get('payload'), dict) else {}
            price = payload.get('price')
            if price is None:
                continue
            market = MarketEvent(
                symbol=str(ev.get('symbol', '')).upper(),
                exchange=str(ev.get('exchange', 'replay')),
                channel=str(ev.get('channel', 'market')),
                ts=str(ev.get('ts', '')),
                payload={'price': float(price)},
            )
            ledger_engine.apply_market_event(market)
            last_exchange = market.exchange
            last_symbol = market.symbol
            continue

        if kind == 'account_event' and str(ev.get('event_type', '')) == 'funding':
            payload = ev.get('payload', {}) if isinstance(ev.get('payload'), dict) else {}
            symbol = str(payload.get('symbol') or ev.get('symbol') or last_symbol).upper()
            position_side = str(payload.get('position_side', 'long')).lower()
            account = AccountEvent(
                exchange=str(ev.get('exchange', last_exchange)),
                ts=str(ev.get('ts', '')),
                event_type='funding',
                payload={
                    'symbol': symbol,
                    'position_side': position_side,
                    'amount': float(payload.get('amount', 0.0) or 0.0),
                },
            )
            ledger_engine.apply_account_event(account)
            funding_total += float(account.payload['amount'])
            last_exchange = account.exchange
            last_symbol = symbol

    positions: dict[str, dict[str, dict[str, float]]] = {}
    for (symbol, position_side), leg in ledger_engine.ledger.positions.items():
        positions.setdefault(symbol, {})[position_side] = {
            'qty': leg.qty,
            'avg_entry_price': leg.avg_entry_price,
            'realized_pnl': leg.realized_pnl,
            'unrealized_pnl': leg.unrealized_pnl,
            'fee_total': leg.fee_total,
            'funding_total': leg.funding_total,
        }

    return {
        'mode': 'live_replay',
        'order_state_sequences': order_state_sequences,
        'ledger': {
            'wallet_balance': ledger_engine.ledger.wallet_balance,
            'equity': ledger_engine.ledger.equity,
            'available_margin': ledger_engine.ledger.available_margin,
            'used_margin': ledger_engine.ledger.used_margin,
            'maintenance_margin': ledger_engine.ledger.maintenance_margin,
            'risk_ratio': ledger_engine.ledger.risk_ratio,
        },
        'positions': positions,
        'fill_prices': live_fill_prices,
        'funding_total': funding_total,
    }


def _flatten_sequences(sequences: dict[str, list[str]]) -> list[str]:
    flattened: list[str] = []
    for key in sorted(sequences):
        flattened.extend(sequences[key])
    return flattened


def _build_drift_metrics(live_summary: dict[str, Any], paper_summary: dict[str, Any]) -> dict[str, Any]:
    live_sequence = _flatten_sequences(live_summary.get('order_state_sequences', {}))
    paper_sequence = _flatten_sequences(paper_summary.get('order_state_sequences', {}))
    max_len = max(len(live_sequence), len(paper_sequence))
    matches = sum(1 for live_state, paper_state in zip(live_sequence, paper_sequence) if live_state == paper_state)
    order_state_match_rate = 1.0 if max_len == 0 else matches / max_len

    live_fill_prices = live_summary.get('fill_prices', [])
    paper_fill_prices = paper_summary.get('fill_prices', [])
    fill_pairs = list(zip(live_fill_prices, paper_fill_prices))
    fill_price_drift = (
        sum(abs(live_price - paper_price) for live_price, paper_price in fill_pairs) / len(fill_pairs)
        if fill_pairs
        else 0.0
    )

    live_partial_ratio = sum(1 for state in live_sequence if state == 'partially_filled') / max(1, len(live_fill_prices))
    paper_partial_ratio = sum(1 for state in paper_sequence if state == 'partially_filled') / max(1, len(paper_fill_prices))

    live_ledger = live_summary.get('ledger', {})
    paper_ledger = paper_summary.get('ledger', {})
    return {
        'paper_vs_live': {
            'order_state_match_rate': round(order_state_match_rate, 6),
            'fill_price_drift': round(fill_price_drift, 6),
            'partial_fill_ratio_drift': round(abs(live_partial_ratio - paper_partial_ratio), 6),
            'equity_drift': round(abs(float(live_ledger.get('equity', 0.0)) - float(paper_ledger.get('equity', 0.0))), 6),
            'available_margin_drift': round(abs(float(live_ledger.get('available_margin', 0.0)) - float(paper_ledger.get('available_margin', 0.0))), 6),
            'funding_booking_drift': round(abs(float(live_summary.get('funding_total', 0.0)) - float(paper_summary.get('funding_total', 0.0))), 6),
        }
    }


def build_daily_replay_report(
    *,
    event_log_path: str,
    oms_store_path: str | None = None,
    audit_store_path: str | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    target_day = date.fromisoformat(day) if day else datetime.utcnow().date()

    all_events, invalid_event_lines = RuntimeReplayStore(event_log_path).load()
    events = [r for r in all_events if _same_day(str(r.get('ts', '')), target_day)]

    event_counter: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    retries = 0
    accepted_ids: set[str] = set()
    rejected_ids: set[str] = set()
    accepted_legacy = 0
    rejected_legacy = 0

    for ev in events:
        payload_raw = ev.get('payload')
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        kind = str(ev.get('kind', ''))
        if kind in {'market_event', 'order_event', 'fill_event', 'account_event'}:
            event_counter[kind] += 1
            level_counter[str(payload.get('level', 'INFO'))] += 1
            if kind == 'order_event':
                status = str(ev.get('status', 'unknown'))
                client_order_id = str(ev.get('client_order_id', 'unknown'))
                if status in {'acked', 'working', 'partially_filled', 'filled'}:
                    accepted_ids.add(client_order_id)
                if status == 'rejected':
                    rejected_ids.add(client_order_id)
                    reason = str(payload.get('reason', 'unknown'))
                    reason_counter[reason] += 1
            continue

        event_name = str(ev.get('event', 'unknown'))
        event_counter[event_name] += 1
        level_counter[str(ev.get('level', 'INFO'))] += 1
        if event_name == 'place_order_retry':
            retries += 1
        if event_name == 'order_accepted':
            accepted_legacy += 1
        if event_name == 'order_rejected':
            rejected_legacy += 1
            reason = str(payload.get('reason', 'unknown'))
            reason_counter[reason] += 1

    accepted = accepted_legacy + len(accepted_ids)
    rejected = rejected_legacy + len(rejected_ids)

    oms_event_counts: dict[str, int] = {}
    if oms_store_path:
        oms_events = JsonlOMSStore(oms_store_path).load()
        counter: Counter[str] = Counter()
        for oms_ev in oms_events:
            if _same_day(oms_ev.ts, target_day):
                counter[oms_ev.event] += 1
        oms_event_counts = dict(counter)

    audit_ok = True
    audit_events = 0
    if audit_store_path:
        store = JsonlAuditStore(audit_store_path)
        audit_ok = store.verify()
        audit_events = sum(1 for ev in store.load() if _same_day(ev.ts, target_day))

    runtime_summary = _summarize_runtime_events(events)
    paper_summary = _rerun_paper_summary(events, runtime_summary)
    drift_metrics = _build_drift_metrics(runtime_summary, paper_summary)

    report = DailyReplayReport(
        day=target_day.isoformat(),
        event_counts=dict(event_counter),
        level_counts=dict(level_counter),
        reject_reason_top=reason_counter.most_common(10),
        retries=retries,
        accepted=accepted,
        rejected=rejected,
        oms_event_counts=oms_event_counts,
        audit_ok=audit_ok,
        audit_events=audit_events,
        invalid_event_lines=invalid_event_lines,
        runtime_summary=runtime_summary,
        paper_summary=paper_summary,
        drift_metrics=drift_metrics,
    )
    return asdict(report)
