from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from .api import KalshiClient
from .config import BotConfig
from .models import Incentive, Market, ONE, ZERO, OrderBook


@dataclass(frozen=True)
class ScanCandidate:
    ticker: str
    title: str
    score: Decimal
    reward_per_day: Decimal
    estimated_base_reward_per_day: Decimal
    estimated_trading_pnl_per_day: Decimal
    estimated_net_per_day: Decimal
    capital_for_base_quotes: Decimal
    spread: Decimal
    midpoint: Decimal
    volume_24h: Decimal
    target_size: Decimal
    hours_to_close: Decimal
    warnings: Sequence[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "title": self.title,
            "score": str(self.score),
            "reward_per_day": str(self.reward_per_day),
            "estimated_base_reward_per_day": str(self.estimated_base_reward_per_day),
            "estimated_trading_pnl_per_day": str(self.estimated_trading_pnl_per_day),
            "estimated_net_per_day": str(self.estimated_net_per_day),
            "capital_for_base_quotes": str(self.capital_for_base_quotes),
            "spread": str(self.spread),
            "midpoint": str(self.midpoint),
            "volume_24h": str(self.volume_24h),
            "target_size": str(self.target_size),
            "hours_to_close": str(self.hours_to_close),
            "warnings": list(self.warnings),
        }


def _chunks(items: Sequence[str], size: int) -> Sequence[Sequence[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _candidate(
    incentive: Incentive,
    market: Market,
    book: OrderBook,
    config: BotConfig,
    now: datetime,
) -> ScanCandidate:
    warnings: List[str] = []
    tick = market.tick_at(book.midpoint)
    competition = Decimal(str(config.strategy.reward_competition_multiple))
    base_size = config.strategy.base_order_size
    rate = incentive.estimated_rate_per_contract_hour(competition)
    base_reward_daily = rate * Decimal("24") * base_size * Decimal("2")
    bid_queue_share = base_size / (book.best_bid_size + base_size)
    ask_queue_share = base_size / (book.best_ask_size + base_size)
    estimated_fills = market.volume_24h * (bid_queue_share + ask_queue_share) / Decimal("2")
    spread_edge = book.spread / Decimal("2")
    adverse_cost = (
        tick * Decimal(str(config.strategy.base_adverse_selection_ticks))
        + config.strategy.maker_fee_estimate_per_contract
    )
    trading_pnl = estimated_fills * (spread_edge - adverse_cost)
    net = base_reward_daily + trading_pnl
    capital = base_size * (book.best_bid + (ONE - book.best_ask))
    hours_to_close = (
        Decimal(str(max(0.0, (market.close_time - now).total_seconds()))) / Decimal("3600")
        if market.close_time
        else Decimal("99999")
    )
    penalty = ZERO
    if not book.valid:
        warnings.append("invalid book")
        penalty += Decimal("100")
    if hours_to_close < Decimal("24"):
        warnings.append("closes within 24h")
        penalty += (Decimal("24") - hours_to_close) / Decimal("24")
    if book.midpoint < Decimal("0.05") or book.midpoint > Decimal("0.95"):
        warnings.append("extreme probability")
        penalty += Decimal("0.5")
    if market.volume_24h <= ZERO:
        warnings.append("no 24h trades")
        penalty += Decimal("0.25")
    score = (net / max(capital, Decimal("0.01"))) - penalty
    return ScanCandidate(
        ticker=market.ticker,
        title=market.title,
        score=score,
        reward_per_day=incentive.reward_per_day,
        estimated_base_reward_per_day=base_reward_daily,
        estimated_trading_pnl_per_day=trading_pnl,
        estimated_net_per_day=net,
        capital_for_base_quotes=capital,
        spread=book.spread,
        midpoint=book.midpoint,
        volume_24h=market.volume_24h,
        target_size=incentive.target_size,
        hours_to_close=hours_to_close,
        warnings=tuple(warnings),
    )


def scan_markets(client: KalshiClient, config: BotConfig, limit: int = 20) -> List[ScanCandidate]:
    raw_incentives = client.get_incentives("active", "liquidity", 10000)
    incentives = [Incentive.from_api(item) for item in raw_incentives]
    incentives = [item for item in incentives if item.target_size > ZERO]
    incentives.sort(
        key=lambda item: item.reward_per_day / max(item.target_size, Decimal("1")), reverse=True
    )
    incentives = incentives[: max(50, limit * 5)]
    by_ticker: Dict[str, Incentive] = {}
    for item in incentives:
        by_ticker.setdefault(item.market_ticker, item)
    tickers = list(by_ticker)

    market_rows: List[Dict[str, Any]] = []
    for chunk in _chunks(tickers, 100):
        market_rows.extend(client.get_markets(chunk, limit=len(chunk)))
    markets = {item.ticker: item for item in map(Market.from_api, market_rows)}

    books: Dict[str, OrderBook] = {}
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(markets)))) as executor:
        pending = {executor.submit(client.get_orderbook, ticker, 20): ticker for ticker in markets}
        for future in as_completed(pending):
            ticker = pending[future]
            try:
                books[ticker] = OrderBook.from_api(ticker, future.result())
            except Exception:
                continue

    now = datetime.now(timezone.utc)
    candidates = [
        _candidate(by_ticker[ticker], markets[ticker], books[ticker], config, now)
        for ticker in markets
        if ticker in books
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:limit]
