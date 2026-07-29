from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Optional, Tuple

from .config import RiskConfig
from .models import Market, ONE, ZERO, OrderBook, Quote, QuotePlan


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.initial_equity: Optional[Decimal] = None
        self.previous_midpoint: Optional[Decimal] = None
        self.consecutive_api_errors = 0
        self.halted_reason: Optional[str] = None

    def set_initial_equity(self, equity: Decimal) -> None:
        if self.initial_equity is None:
            self.initial_equity = equity

    def record_api_success(self) -> None:
        self.consecutive_api_errors = 0

    def record_api_error(self) -> None:
        self.consecutive_api_errors += 1
        if self.consecutive_api_errors >= self.config.max_consecutive_api_errors:
            self.halted_reason = "maximum consecutive API errors reached"

    def halt(self, reason: str) -> None:
        self.halted_reason = reason

    def check(
        self,
        book: OrderBook,
        market: Market,
        tick: Decimal,
        equity: Decimal,
        now: Optional[datetime] = None,
    ) -> Tuple[str, ...]:
        current = now or datetime.now(timezone.utc)
        reasons = []
        self.set_initial_equity(equity)

        if self.halted_reason:
            reasons.append(self.halted_reason)
        if market.status not in {"active", "open"}:
            reasons.append("market is not open")
        if not book.valid:
            reasons.append("order book is not two-sided")
        age = (current - book.observed_at).total_seconds()
        if age > self.config.max_book_age_seconds:
            reasons.append("order book is stale")
        if market.close_time is not None:
            seconds_to_close = (market.close_time - current).total_seconds()
            if seconds_to_close < self.config.min_seconds_to_close:
                reasons.append("market is too close to closing")
        if self.initial_equity is not None:
            drawdown = self.initial_equity - equity
            if drawdown >= self.config.max_daily_loss:
                self.halted_reason = "daily loss limit reached"
                reasons.append(self.halted_reason)
        if self.previous_midpoint is not None and tick > ZERO:
            movement_ticks = abs(book.midpoint - self.previous_midpoint) / tick
            if movement_ticks >= self.config.max_mid_move_ticks:
                self.halted_reason = "midpoint jump limit reached"
                reasons.append(self.halted_reason)
        self.previous_midpoint = book.midpoint
        return tuple(dict.fromkeys(reasons))

    @staticmethod
    def position_capital(position: Decimal, mark: Decimal) -> Decimal:
        if position >= ZERO:
            return position * mark
        return abs(position) * (ONE - mark)

    def sanitize(
        self,
        plan: QuotePlan,
        book: OrderBook,
        position: Decimal,
        available_cash: Decimal,
        portfolio_capital: Decimal,
    ) -> QuotePlan:
        if self.halted_reason:
            return replace(
                plan,
                bid=None,
                ask=None,
                reasons=plan.reasons + (self.halted_reason,),
            )
        quotes = list(plan.quotes[: self.config.max_open_orders_per_market])
        current_market_capital = self.position_capital(position, book.midpoint)
        market_room = max(ZERO, self.config.max_market_capital - current_market_capital)
        portfolio_room = max(ZERO, self.config.max_portfolio_capital - portfolio_capital)
        room = min(max(ZERO, available_cash), market_room, portfolio_room)
        requested = sum((quote.collateral for quote in quotes), ZERO)
        reasons = list(plan.reasons)
        if requested > room and requested > ZERO:
            scale = room / requested
            resized = []
            for quote in quotes:
                count = (quote.count * scale).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
                if count > ZERO:
                    resized.append(replace(quote, count=count))
            quotes = resized
            reasons.append("quote sizes reduced by capital limits")
        elif room <= ZERO:
            quotes = []
            reasons.append("no capital room")

        bid: Optional[Quote] = None
        ask: Optional[Quote] = None
        for quote in quotes:
            if quote.side == "bid":
                bid = quote
            elif quote.side == "ask":
                ask = quote
        return replace(plan, bid=bid, ask=ask, reasons=tuple(dict.fromkeys(reasons)))
