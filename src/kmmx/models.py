from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


ZERO = Decimal("0")
ONE = Decimal("1")


def decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    match = re.match(r"^(.*\.)(\d+)([+-]\d\d:\d\d)$", normalized)
    if match:
        fraction = (match.group(2) + "000000")[:6]
        normalized = match.group(1) + fraction + match.group(3)
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PriceLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBook:
    ticker: str
    yes_bids: Tuple[PriceLevel, ...]
    no_bids: Tuple[PriceLevel, ...]
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_api(
        cls,
        ticker: str,
        payload: Dict[str, Any],
        observed_at: Optional[datetime] = None,
    ) -> "OrderBook":
        book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
        yes_raw = book.get("yes_dollars") or book.get("yes") or []
        no_raw = book.get("no_dollars") or book.get("no") or []

        def levels(rows: Iterable[Sequence[Any]]) -> Tuple[PriceLevel, ...]:
            parsed = (PriceLevel(decimal(row[0]), decimal(row[1])) for row in rows)
            return tuple(sorted(parsed, key=lambda level: level.price))

        return cls(
            ticker=ticker,
            yes_bids=levels(yes_raw),
            no_bids=levels(no_raw),
            observed_at=observed_at or datetime.now(timezone.utc),
        )

    @property
    def valid(self) -> bool:
        return bool(self.yes_bids and self.no_bids and self.best_bid < self.best_ask)

    @property
    def best_bid(self) -> Decimal:
        return self.yes_bids[-1].price if self.yes_bids else ZERO

    @property
    def best_ask(self) -> Decimal:
        return ONE - self.no_bids[-1].price if self.no_bids else ONE

    @property
    def best_bid_size(self) -> Decimal:
        return self.yes_bids[-1].size if self.yes_bids else ZERO

    @property
    def best_ask_size(self) -> Decimal:
        return self.no_bids[-1].size if self.no_bids else ZERO

    @property
    def spread(self) -> Decimal:
        return self.best_ask - self.best_bid

    @property
    def midpoint(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal("2")

    @property
    def imbalance(self) -> Decimal:
        total = self.best_bid_size + self.best_ask_size
        if total <= ZERO:
            return ZERO
        return (self.best_bid_size - self.best_ask_size) / total

    @property
    def microprice(self) -> Decimal:
        total = self.best_bid_size + self.best_ask_size
        if total <= ZERO:
            return self.midpoint
        return (self.best_ask * self.best_bid_size + self.best_bid * self.best_ask_size) / total

    def size_at(self, side: str, price: Decimal) -> Decimal:
        if side == "bid":
            for level in self.yes_bids:
                if level.price == price:
                    return level.size
            return ZERO
        for level in self.no_bids:
            if ONE - level.price == price:
                return level.size
        return ZERO

    def distance_from_best(self, side: str, price: Decimal, tick: Decimal) -> int:
        if tick <= ZERO:
            return 0
        distance = self.best_bid - price if side == "bid" else price - self.best_ask
        return max(0, int((distance / tick).to_integral_value()))


@dataclass(frozen=True)
class Market:
    ticker: str
    title: str
    status: str
    close_time: Optional[datetime]
    volume_24h: Decimal
    price_ranges: Tuple[Dict[str, Any], ...]
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "Market":
        return cls(
            ticker=str(payload.get("ticker", "")),
            title=str(payload.get("title", "")),
            status=str(payload.get("status", "")),
            close_time=parse_time(payload.get("close_time")),
            volume_24h=decimal(payload.get("volume_24h_fp", payload.get("volume_24h", 0))),
            price_ranges=tuple(payload.get("price_ranges") or ()),
            raw=payload,
        )

    def tick_at(self, price: Decimal) -> Decimal:
        for price_range in self.price_ranges:
            start = decimal(price_range.get("start"))
            end = decimal(price_range.get("end"), "1")
            if start <= price <= end:
                return decimal(price_range.get("step"), "0.01")
        return Decimal("0.01")


@dataclass(frozen=True)
class Incentive:
    incentive_id: str
    market_ticker: str
    start: datetime
    end: datetime
    reward_dollars: Decimal
    target_size: Decimal
    discount_factor: Decimal
    incentive_type: str = "liquidity"

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "Incentive":
        start = parse_time(payload.get("start_date"))
        end = parse_time(payload.get("end_date"))
        if start is None or end is None:
            raise ValueError("incentive is missing start_date or end_date")
        return cls(
            incentive_id=str(payload.get("id", "")),
            market_ticker=str(payload.get("market_ticker", "")),
            start=start,
            end=end,
            reward_dollars=decimal(payload.get("period_reward")) / Decimal("10000"),
            target_size=decimal(payload.get("target_size_fp")),
            discount_factor=decimal(payload.get("discount_factor_bps")) / Decimal("10000"),
            incentive_type=str(payload.get("incentive_type", "liquidity")),
        )

    @property
    def duration_hours(self) -> Decimal:
        seconds = Decimal(str(max(1.0, (self.end - self.start).total_seconds())))
        return seconds / Decimal("3600")

    @property
    def reward_per_day(self) -> Decimal:
        return self.reward_dollars * Decimal("24") / self.duration_hours

    def estimated_rate_per_contract_hour(self, competition_multiple: Decimal) -> Decimal:
        if self.target_size <= ZERO or competition_multiple <= ZERO:
            return ZERO
        return self.reward_dollars / (self.duration_hours * self.target_size * competition_multiple)

    def score_multiplier(self, distance_ticks: int) -> Decimal:
        if distance_ticks <= 0:
            return ONE
        if not (ZERO < self.discount_factor <= ONE):
            return ZERO
        return self.discount_factor**distance_ticks


@dataclass(frozen=True)
class Trade:
    trade_id: str
    ticker: str
    price: Decimal
    count: Decimal
    taker_book_side: str
    created_at: datetime
    is_block_trade: bool = False

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "Trade":
        created = parse_time(payload.get("created_time"))
        if created is None:
            raise ValueError("trade is missing created_time")
        return cls(
            trade_id=str(payload.get("trade_id", "")),
            ticker=str(payload.get("ticker", "")),
            price=decimal(payload.get("yes_price_dollars", payload.get("yes_price", 0))),
            count=decimal(payload.get("count_fp", payload.get("count", 0))),
            taker_book_side=str(
                payload.get("taker_book_side")
                or ("bid" if payload.get("taker_side") == "yes" else "ask")
            ),
            created_at=created,
            is_block_trade=bool(payload.get("is_block_trade", False)),
        )


@dataclass(frozen=True)
class Quote:
    side: str
    price: Decimal
    count: Decimal
    expected_value_per_contract: Decimal
    reward_value_per_contract: Decimal
    trading_value_per_contract: Decimal

    @property
    def collateral(self) -> Decimal:
        unit = self.price if self.side == "bid" else ONE - self.price
        return max(ZERO, unit * self.count)


@dataclass(frozen=True)
class QuotePlan:
    ticker: str
    fair_value: Decimal
    volatility: Decimal
    bid: Optional[Quote]
    ask: Optional[Quote]
    reasons: Tuple[str, ...] = ()

    @property
    def quotes(self) -> Tuple[Quote, ...]:
        return tuple(quote for quote in (self.bid, self.ask) if quote is not None)

    @property
    def collateral(self) -> Decimal:
        return sum((quote.collateral for quote in self.quotes), ZERO)
