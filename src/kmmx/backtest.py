from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .config import BotConfig
from .models import Incentive, Market, OrderBook, Trade, parse_time
from .paper import PaperBroker
from .risk import RiskEngine
from .strategy import AdaptiveRewardStrategy


@dataclass(frozen=True)
class BacktestResult:
    ticker: str
    snapshots: int
    fills: int
    duration_seconds: float
    starting_cash: Decimal
    ending_cash: Decimal
    ending_position: Decimal
    ending_mark: Decimal
    trading_pnl: Decimal
    maker_fees: Decimal
    estimated_rewards: Decimal
    estimated_total_pnl: Decimal
    max_drawdown: Decimal

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "snapshots": self.snapshots,
            "fills": self.fills,
            "duration_seconds": round(self.duration_seconds, 3),
            "starting_cash": str(self.starting_cash),
            "ending_cash": str(self.ending_cash),
            "ending_position": str(self.ending_position),
            "ending_mark": str(self.ending_mark),
            "trading_pnl": str(self.trading_pnl),
            "maker_fees": str(self.maker_fees),
            "estimated_rewards": str(self.estimated_rewards),
            "estimated_total_pnl": str(self.estimated_total_pnl),
            "max_drawdown": str(self.max_drawdown),
        }


def _load(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    metadata: Dict[str, Any] = {}
    snapshots: List[Dict[str, Any]] = []
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSON on line %s" % line_number) from exc
            if row.get("type") == "metadata":
                metadata = row
            elif row.get("type") == "snapshot":
                snapshots.append(row)
    if not metadata:
        raise ValueError("recording has no metadata row")
    if len(snapshots) < 2:
        raise ValueError("recording needs at least two snapshots")
    return metadata, snapshots


def run_backtest(path: str, config: BotConfig) -> BacktestResult:
    metadata, rows = _load(path)
    market = Market.from_api(metadata["market"])
    incentive_payload = metadata.get("incentive")
    incentive = Incentive.from_api(incentive_payload) if incentive_payload else None
    strategy = AdaptiveRewardStrategy(config.strategy, config.runtime.poll_interval_seconds)
    risk = RiskEngine(config.risk)
    broker = PaperBroker(
        config.runtime.paper_starting_cash,
        order_ttl_seconds=config.runtime.order_ttl_seconds,
        maker_fee_per_contract=config.strategy.maker_fee_estimate_per_contract,
    )
    first_time = parse_time(rows[0]["observed_at"])
    last_time = first_time
    if first_time is None:
        raise ValueError("first snapshot has no observed_at")
    peak_equity = broker.starting_cash
    max_drawdown = Decimal("0")
    ending_mark = Decimal("0.5")

    for row in rows:
        observed = parse_time(row.get("observed_at"))
        if observed is None:
            continue
        book = OrderBook.from_api(market.ticker, row["orderbook"], observed_at=observed)
        ending_mark = book.midpoint
        elapsed = max(0.0, (observed - last_time).total_seconds()) if last_time else 0.0
        tick = market.tick_at(book.midpoint)
        broker.accrue_rewards(
            elapsed,
            book,
            tick,
            incentive,
            Decimal(str(config.strategy.reward_competition_multiple)),
        )
        trades = [Trade.from_api(item) for item in row.get("trades") or []]
        broker.process_trades(trades)
        equity = broker.equity(book.midpoint)
        risk_reasons = risk.check(book, market, tick, equity, now=observed)
        plan = strategy.make_plan(book, tick, broker.position, incentive)
        if risk_reasons:
            plan = replace(
                plan,
                bid=None,
                ask=None,
                reasons=plan.reasons + risk_reasons,
            )
        plan = risk.sanitize(
            plan,
            book,
            broker.position,
            max(Decimal("0"), broker.cash),
            RiskEngine.position_capital(broker.position, book.midpoint),
        )
        broker.replace_quotes(plan, book, observed)
        equity = broker.equity(book.midpoint)
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        last_time = observed

    trading_pnl = broker.trading_pnl(ending_mark)
    duration = max(0.0, (last_time - first_time).total_seconds()) if last_time else 0.0
    return BacktestResult(
        ticker=market.ticker,
        snapshots=len(rows),
        fills=len(broker.fills),
        duration_seconds=duration,
        starting_cash=broker.starting_cash,
        ending_cash=broker.cash,
        ending_position=broker.position,
        ending_mark=ending_mark,
        trading_pnl=trading_pnl,
        maker_fees=broker.maker_fees,
        estimated_rewards=broker.reward_estimate,
        estimated_total_pnl=trading_pnl + broker.reward_estimate,
        max_drawdown=max_drawdown,
    )
