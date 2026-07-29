from __future__ import annotations

import json
import hashlib
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from .api import ApiError, KalshiClient
from .config import BotConfig
from .live import LiveBroker
from .models import Incentive, Market, OrderBook, QuotePlan, Trade
from .paper import PaperBroker, quote_to_dict
from .risk import RiskEngine
from .state import DailyEquityStore
from .strategy import AdaptiveRewardStrategy


def find_incentive(client: KalshiClient, ticker: str) -> Optional[Incentive]:
    for row in client.get_incentives("active", "liquidity", 10000):
        if row.get("market_ticker") == ticker:
            return Incentive.from_api(row)
    return None


def _event(
    mode: str,
    ticker: str,
    book: OrderBook,
    plan: QuotePlan,
    position: Decimal,
    equity: Decimal,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {
        "time": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "ticker": ticker,
        "bid": str(book.best_bid),
        "ask": str(book.best_ask),
        "fair": str(plan.fair_value),
        "volatility": str(plan.volatility),
        "position": str(position),
        "equity": str(equity),
        "quote_bid": quote_to_dict(plan.bid),
        "quote_ask": quote_to_dict(plan.ask),
        "reasons": list(plan.reasons),
    }
    payload.update(extra or {})
    return json.dumps(payload, separators=(",", ":"))


def _risk_filtered(plan: QuotePlan, reasons: tuple) -> QuotePlan:
    if not reasons:
        return plan
    return replace(plan, bid=None, ask=None, reasons=plan.reasons + reasons)


def run_paper(
    client: KalshiClient,
    config: BotConfig,
    ticker: str,
    duration_seconds: float = 0,
) -> None:
    market = Market.from_api(client.get_market(ticker))
    incentive = find_incentive(client, ticker)
    strategy = AdaptiveRewardStrategy(config.strategy, config.runtime.poll_interval_seconds)
    risk = RiskEngine(config.risk)
    broker = PaperBroker(
        config.runtime.paper_starting_cash,
        order_ttl_seconds=config.runtime.order_ttl_seconds,
        maker_fee_per_contract=config.strategy.maker_fee_estimate_per_contract,
    )
    started = time.monotonic()
    previous_time = datetime.now(timezone.utc)
    min_trade_ts = int(previous_time.timestamp())
    kill_switch = Path(config.runtime.kill_switch_file).expanduser()

    try:
        while duration_seconds <= 0 or time.monotonic() - started < duration_seconds:
            cycle_started = time.monotonic()
            if kill_switch.exists():
                risk.halt("kill-switch file detected")
            observed = datetime.now(timezone.utc)
            book = OrderBook.from_api(ticker, client.get_orderbook(ticker), observed)
            trades = [
                Trade.from_api(item)
                for item in client.get_trades(ticker, min_ts=min_trade_ts, limit=1000)
            ]
            min_trade_ts = int(observed.timestamp())
            elapsed = max(0.0, (observed - previous_time).total_seconds())
            tick = market.tick_at(book.midpoint)
            broker.accrue_rewards(
                elapsed,
                book,
                tick,
                incentive,
                Decimal(str(config.strategy.reward_competition_multiple)),
            )
            broker.process_trades(trades)
            equity = broker.equity(book.midpoint)
            risk_reasons = risk.check(book, market, tick, equity, observed)
            plan = strategy.make_plan(book, tick, broker.position, incentive)
            plan = _risk_filtered(plan, risk_reasons)
            plan = risk.sanitize(
                plan,
                book,
                broker.position,
                max(Decimal("0"), broker.cash),
                RiskEngine.position_capital(broker.position, book.midpoint),
            )
            broker.replace_quotes(plan, book, observed)
            print(
                _event(
                    "paper",
                    ticker,
                    book,
                    plan,
                    broker.position,
                    equity,
                    {
                        "fills": len(broker.fills),
                        "trading_pnl": str(broker.trading_pnl(book.midpoint)),
                        "maker_fees": str(broker.maker_fees),
                        "estimated_rewards": str(broker.reward_estimate),
                    },
                ),
                flush=True,
            )
            previous_time = observed
            if risk.halted_reason:
                break
            sleep_for = config.runtime.poll_interval_seconds - (time.monotonic() - cycle_started)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        broker.cancel_all()


def run_live(
    client: KalshiClient,
    config: BotConfig,
    ticker: str,
    duration_seconds: float = 0,
) -> None:
    market = Market.from_api(client.get_market(ticker))
    incentive = find_incentive(client, ticker)
    strategy = AdaptiveRewardStrategy(config.strategy, config.runtime.poll_interval_seconds)
    risk = RiskEngine(config.risk)
    lock_name = ".kmmx-%s.lock" % hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:12]
    lock_path = str(Path(config.runtime.state_file).expanduser().with_name(lock_name))
    broker = LiveBroker(
        client,
        ticker,
        config.runtime.order_ttl_seconds,
        config.risk.max_fills_per_15_seconds,
        lock_path,
    )
    started = time.monotonic()
    kill_switch = Path(config.runtime.kill_switch_file).expanduser()
    equity_store = DailyEquityStore(config.runtime.state_file)

    try:
        while duration_seconds <= 0 or time.monotonic() - started < duration_seconds:
            cycle_started = time.monotonic()
            try:
                if kill_switch.exists():
                    risk.halt("kill-switch file detected")
                observed = datetime.now(timezone.utc)
                book = OrderBook.from_api(ticker, client.get_orderbook(ticker), observed)
                state = broker.snapshot()
                risk.set_initial_equity(equity_store.get_or_create(state.equity, observed))
                tick = market.tick_at(book.midpoint)
                risk_reasons = risk.check(book, market, tick, state.equity, observed)
                plan = strategy.make_plan(book, tick, state.position, incentive)
                plan = _risk_filtered(plan, risk_reasons)
                plan = risk.sanitize(
                    plan,
                    book,
                    state.position,
                    state.available_cash,
                    state.portfolio_capital,
                )
                broker.reconcile(plan, state)
                risk.record_api_success()
                print(
                    _event("live", ticker, book, plan, state.position, state.equity),
                    flush=True,
                )
                if risk.halted_reason:
                    break
            except (ApiError, RuntimeError, KeyError, ValueError) as exc:
                risk.record_api_error()
                print(
                    json.dumps(
                        {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "mode": "live",
                            "ticker": ticker,
                            "error": str(exc),
                            "consecutive_errors": risk.consecutive_api_errors,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                if risk.halted_reason:
                    try:
                        broker.cancel_all()
                    except Exception:
                        pass
                    break
            sleep_for = config.runtime.poll_interval_seconds - (time.monotonic() - cycle_started)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        broker.close(cancel_orders=config.runtime.cancel_orders_on_exit)
