from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Set

from .models import Incentive, ZERO, OrderBook, Quote, QuotePlan, Trade


@dataclass
class PaperOrder:
    side: str
    price: Decimal
    original_count: Decimal
    remaining: Decimal
    queue_ahead: Decimal
    placed_at: datetime
    expires_at: Optional[datetime]


@dataclass(frozen=True)
class PaperFill:
    side: str
    price: Decimal
    count: Decimal
    trade_id: str
    filled_at: datetime


class PaperBroker:
    """Trade-driven maker simulator with a price-time queue approximation."""

    def __init__(
        self,
        starting_cash: Decimal,
        order_ttl_seconds: Optional[int] = None,
        maker_fee_per_contract: Decimal = ZERO,
    ) -> None:
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = ZERO
        self.orders: Dict[str, PaperOrder] = {}
        self.fills: List[PaperFill] = []
        self.reward_estimate = ZERO
        self.maker_fees = ZERO
        self._seen_trades: Set[str] = set()
        self.order_ttl_seconds = order_ttl_seconds
        self.maker_fee_per_contract = maker_fee_per_contract

    def equity(self, mark: Decimal) -> Decimal:
        return self.cash + self.position * mark

    def trading_pnl(self, mark: Decimal) -> Decimal:
        return self.equity(mark) - self.starting_cash

    def replace_quotes(
        self, plan: QuotePlan, book: OrderBook, observed_at: Optional[datetime] = None
    ) -> None:
        now = observed_at or datetime.now(timezone.utc)
        desired = {quote.side: quote for quote in plan.quotes}
        for side in list(self.orders):
            order = self.orders[side]
            quote = desired.get(side)
            expired = order.expires_at is not None and now >= order.expires_at
            if (
                expired
                or quote is None
                or quote.price != order.price
                or quote.count != order.original_count
            ):
                del self.orders[side]
        for side, quote in desired.items():
            if side in self.orders:
                continue
            self.orders[side] = PaperOrder(
                side=side,
                price=quote.price,
                original_count=quote.count,
                remaining=quote.count,
                queue_ahead=book.size_at(side, quote.price),
                placed_at=now,
                expires_at=(
                    now + timedelta(seconds=self.order_ttl_seconds)
                    if self.order_ttl_seconds is not None
                    else None
                ),
            )

    def cancel_all(self) -> None:
        self.orders.clear()

    def _fill(self, order: PaperOrder, count: Decimal, trade: Trade) -> None:
        fill_count = min(order.remaining, count)
        if fill_count <= ZERO:
            return
        if order.side == "bid":
            self.cash -= order.price * fill_count
            self.position += fill_count
        else:
            self.cash += order.price * fill_count
            self.position -= fill_count
        fee = self.maker_fee_per_contract * fill_count
        self.cash -= fee
        self.maker_fees += fee
        order.remaining -= fill_count
        self.fills.append(
            PaperFill(order.side, order.price, fill_count, trade.trade_id, trade.created_at)
        )
        if order.remaining <= ZERO:
            self.orders.pop(order.side, None)

    def process_trades(self, trades: Sequence[Trade]) -> None:
        for trade in sorted(trades, key=lambda item: item.created_at):
            if trade.trade_id in self._seen_trades or trade.is_block_trade:
                continue
            self._seen_trades.add(trade.trade_id)
            if trade.taker_book_side == "ask":
                order = self.orders.get("bid")
                if (
                    order is not None
                    and order.expires_at is not None
                    and trade.created_at >= order.expires_at
                ):
                    self.orders.pop("bid", None)
                    order = None
                if order is None or trade.price > order.price:
                    continue
                if trade.price < order.price:
                    self._fill(order, order.remaining, trade)
                    continue
            else:
                order = self.orders.get("ask")
                if (
                    order is not None
                    and order.expires_at is not None
                    and trade.created_at >= order.expires_at
                ):
                    self.orders.pop("ask", None)
                    order = None
                if order is None or trade.price < order.price:
                    continue
                if trade.price > order.price:
                    self._fill(order, order.remaining, trade)
                    continue

            consumed = trade.count
            if order.queue_ahead > ZERO:
                queue_reduction = min(order.queue_ahead, consumed)
                order.queue_ahead -= queue_reduction
                consumed -= queue_reduction
            if consumed > ZERO:
                self._fill(order, consumed, trade)

    def accrue_rewards(
        self,
        elapsed_seconds: float,
        book: OrderBook,
        tick: Decimal,
        incentive: Optional[Incentive],
        competition_multiple: Decimal,
    ) -> Decimal:
        if incentive is None or elapsed_seconds <= 0:
            return ZERO
        if not (incentive.start <= book.observed_at <= incentive.end):
            return ZERO
        rate = incentive.estimated_rate_per_contract_hour(competition_multiple)
        earned = ZERO
        interval_start = book.observed_at - timedelta(seconds=elapsed_seconds)
        for order in self.orders.values():
            active_end = (
                min(book.observed_at, order.expires_at) if order.expires_at else book.observed_at
            )
            active_start = max(interval_start, order.placed_at)
            active_seconds = max(0.0, (active_end - active_start).total_seconds())
            if active_seconds <= 0:
                continue
            elapsed_hours = Decimal(str(active_seconds)) / Decimal("3600")
            distance = book.distance_from_best(order.side, order.price, tick)
            earned += order.remaining * rate * elapsed_hours * incentive.score_multiplier(distance)
        self.reward_estimate += earned
        return earned


def quote_to_dict(quote: Optional[Quote]) -> Optional[dict]:
    if quote is None:
        return None
    return {
        "side": quote.side,
        "price": str(quote.price),
        "count": str(quote.count),
        "expected_value_per_contract": str(quote.expected_value_per_contract),
        "reward_value_per_contract": str(quote.reward_value_per_contract),
        "trading_value_per_contract": str(quote.trading_value_per_contract),
    }
